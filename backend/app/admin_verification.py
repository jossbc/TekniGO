"""HU-17B: private technician dossiers and version-bound document decisions."""
from datetime import date, datetime, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, and_, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _require_admin, _serialize_doc
from app.identity_processing import IdentityProcessingRetry, process_current_identity
from app.models import DocumentReviewDecision, IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, TechnicianProfile, User, UserRole
from app.security import get_current_user
from app.technician_activation import ANTECEDENTES_TYPES, IDENTITY_BASIC_TYPES, compute_document_expiration, sync_technician_verification_state

router = APIRouter(prefix="/admin", tags=["Administración"])


class DocumentReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documento_id: uuid.UUID
    estado: Literal["APROBADO", "RECHAZADO", "VENCIDO", "INVALIDADO"]
    observaciones: str | None = Field(default=None, max_length=2000)
    fecha_vencimiento: date | None = None
    legible: bool = False
    identidad_coincide: bool | None = None

    @model_validator(mode="after")
    def validate_decision(self):
        self.observaciones = (self.observaciones or "").strip() or None
        if self.estado != "APROBADO" and not self.observaciones:
            raise ValueError("El rechazo o invalidación exige una observación")
        if self.estado == "APROBADO" and not self.legible:
            raise ValueError("Confirma que el documento es legible antes de aprobarlo")
        return self


class AdminVerificationReviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Accepted for old clients, but operational state is ALWAYS computed by HU-18.
    estado_verificacion: str | None = None
    observaciones_verificacion: str | None = Field(default=None, max_length=2000)
    documentos: list[DocumentReviewItem] = Field(min_length=1, max_length=5)


def current_request(db: Session, user_id: uuid.UUID):
    return db.scalar(select(IdentityVerificationRequest).where(
        IdentityVerificationRequest.usuario_id == user_id,
    ).order_by(IdentityVerificationRequest.version.desc()).limit(1))


def _dossier_user(db: Session, user_id: uuid.UUID, *, lock=False):
    query = select(User).where(User.id == user_id)
    if lock:
        query = query.with_for_update()
    user = db.scalar(query.execution_options(populate_existing=True))
    profile = db.get(TechnicianProfile, user_id)
    if user is None or not any(role.rol in {"CLIENTE", "TECNICO"} for role in user.roles):
        raise HTTPException(404, "No se encontró el expediente")
    if any(role.rol == "TECNICO" for role in user.roles) and profile is None:
        raise HTTPException(409, "El perfil técnico está incompleto")
    return user, profile


@router.get("/technicians")
def list_technician_dossiers(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    _require_admin(claims, db)
    response.headers["Cache-Control"] = "no-store, private"
    users = db.scalars(select(User).join(UserRole).where(UserRole.rol == "TECNICO").order_by(User.creado_en, User.id).offset(offset).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(UserRole).where(UserRole.rol == "TECNICO"))
    return {"total": total, "expedientes": [{
        "usuario_id": str(user.id), "nombre_completo": user.nombre_completo,
        "estado_identidad": user.estado_identidad,
    } for user in users]}


def _roles(user):
    return sorted(role.rol for role in user.roles if role.rol in {"CLIENTE", "TECNICO"})


@router.get("/dossiers")
def list_dossiers(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db),
                  offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                  tipo: Literal["TODOS", "CLIENTE", "TECNICO"] = "TODOS"):
    _require_admin(claims, db)
    response.headers["Cache-Control"] = "no-store, private"
    technician = select(UserRole.usuario_id).where(UserRole.usuario_id == User.id, UserRole.rol == "TECNICO").exists()
    client = select(UserRole.usuario_id).where(UserRole.usuario_id == User.id, UserRole.rol == "CLIENTE").exists()
    latest_request = select(IdentityVerificationRequest.id).where(
        IdentityVerificationRequest.usuario_id == User.id,
    ).order_by(IdentityVerificationRequest.version.desc()).limit(1).correlate(User).scalar_subquery()
    latest_result = select(IdentityVerificationResult.estado).where(
        IdentityVerificationResult.solicitud_id == latest_request,
        IdentityVerificationResult.proceso_responsable == "PROCESAMIENTO_IDENTIDAD",
    ).order_by(IdentityVerificationResult.creado_en.desc(), IdentityVerificationResult.id.desc()).limit(1).scalar_subquery()
    fallback = and_(client, User.estado_identidad != "VERIFICADO", latest_result.in_(["RECHAZADO", "INCIERTO", "ERROR"]))
    verified_technician = select(TechnicianProfile.usuario_id).where(
        TechnicianProfile.usuario_id == User.id,
        TechnicianProfile.estado_verificacion == "VERIFICADO",
    ).exists()
    pending_technician = and_(technician, ~verified_technician)
    condition = fallback if tipo == "CLIENTE" else pending_technician if tipo == "TECNICO" else or_(pending_technician, fallback)
    query = select(User).where(condition)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    users = db.scalars(query.order_by(User.creado_en, User.id).offset(offset).limit(limit)).all()
    return {"total": total, "expedientes": [{
        "usuario_id": str(user.id), "nombre_completo": user.nombre_completo,
        "estado_identidad": user.estado_identidad, "roles": _roles(user),
    } for user in users]}


@router.get("/dossiers/{usuario_id}/dossier")
@router.get("/technicians/{usuario_id}/dossier")
def get_technician_dossier(usuario_id: uuid.UUID, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(claims, db)
    response.headers["Cache-Control"] = "no-store, private"
    user, profile = _dossier_user(db, usuario_id)
    request = current_request(db, user.id)
    docs = db.scalars(select(IdentityDocument).where(IdentityDocument.solicitud_id == request.id)).all() if request else []
    results = db.scalars(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == request.id,
        IdentityVerificationResult.proceso_responsable != "CARGA_DOCUMENTAL",
    ).order_by(IdentityVerificationResult.creado_en.desc())).all() if request else []
    decisions = db.scalars(select(DocumentReviewDecision).join(IdentityDocument, DocumentReviewDecision.documento_id == IdentityDocument.id).where(IdentityDocument.usuario_id == user.id).order_by(DocumentReviewDecision.creado_en.desc(), DocumentReviewDecision.id)).all()
    return {
        "usuario_id": str(user.id), "nombre_completo": user.nombre_completo, "dni": user.dni,
        "telefono": user.telefono, "telefono_verificado": bool(user.telefono_verificado),
        "roles": _roles(user),
        "estado_identidad": user.estado_identidad, "estado_verificacion": profile.estado_verificacion if profile else None,
        "solicitud_id": str(request.id) if request else None, "version": request.version if request else None,
        "documentos": [{**_serialize_doc(doc), "fecha_emision": doc.fecha_emision_declarada, "fecha_vencimiento": doc.fecha_vencimiento} for doc in docs],
        "resultados_identidad": [{"estado": result.estado, "motivo": result.motivo, "proceso_responsable": result.proceso_responsable, "fecha": result.creado_en} for result in results],
        "decisiones": [{"documento_id": str(d.documento_id), "administrador_id": str(d.administrador_id), "version": d.version_evaluada, "estado": d.estado, "observaciones": d.observaciones, "fecha": d.creado_en} for d in decisions],
    }


def apply_technician_review(usuario_id: uuid.UUID, payload: AdminVerificationReviewSchema, claims: dict, db: Session):
    admin = _require_admin(claims, db)
    # Uploads and reviews take the SAME user lock, so a replacement cannot race this check.
    user, technician = _dossier_user(db, usuario_id, lock=True)
    request = current_request(db, user.id)
    if request is None:
        raise HTTPException(409, "El expediente todavía no tiene documentos")
    current_docs = {doc.id: doc for doc in db.scalars(select(IdentityDocument).where(IdentityDocument.solicitud_id == request.id)).all()}
    if len({item.documento_id for item in payload.documentos}) != len(payload.documentos):
        raise HTTPException(400, "No repitas un documento en la revisión")
    processing = db.scalar(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == request.id,
        IdentityVerificationResult.proceso_responsable == "PROCESAMIENTO_IDENTIDAD",
    ).order_by(IdentityVerificationResult.creado_en.desc()).limit(1))
    # Validate the whole batch before mutating any document.
    for item in payload.documentos:
        doc = current_docs.get(item.documento_id)
        if doc is None:
            raise HTTPException(409, "El documento fue sustituido o no pertenece a este expediente. Actualiza la revisión.")
        if item.estado == "APROBADO":
            if doc.estado in ("VENCIDO", "INVALIDADO"):
                raise HTTPException(409, "Reemplaza el documento vencido o invalidado antes de aprobarlo")
            expiry = doc.fecha_vencimiento or compute_document_expiration(doc.tipo, doc.fecha_emision_declarada)
            if item.fecha_vencimiento is not None:
                expiry = min(expiry, item.fecha_vencimiento) if expiry else item.fecha_vencimiento
            if expiry and expiry < date.today():
                raise HTTPException(409, "No se puede aprobar un documento vencido")
            if doc.tipo in ANTECEDENTES_TYPES and (not doc.fecha_emision_declarada or doc.fecha_emision_declarada > date.today()):
                raise HTTPException(409, "Los antecedentes necesitan una fecha de emisión válida")
            if doc.tipo in IDENTITY_BASIC_TYPES:
                if item.identidad_coincide is not True:
                    raise HTTPException(409, "Confirma la coincidencia del DNI, datos registrados y selfie")
                if processing and processing.estado == "RECHAZADO" and not item.observaciones:
                    raise HTTPException(409, "Explica en las observaciones cómo resolviste la discrepancia detectada por la evaluación automática antes de aprobar.")
    for item in payload.documentos:
        doc = current_docs[item.documento_id]
        doc.estado, doc.observaciones = item.estado, item.observaciones
        expiry = doc.fecha_vencimiento or compute_document_expiration(doc.tipo, doc.fecha_emision_declarada)
        if item.fecha_vencimiento:
            expiry = min(expiry, item.fecha_vencimiento) if expiry else item.fecha_vencimiento
        doc.fecha_vencimiento = expiry
        db.add(DocumentReviewDecision(
            documento_id=doc.id, solicitud_id=request.id, administrador_id=admin.id,
            version_evaluada=request.version, estado=item.estado, observaciones=item.observaciones,
            legible=item.legible, identidad_coincide=item.identidad_coincide,
        ))
    db.flush()
    if technician is not None:
        technician.observaciones_verificacion = payload.observaciones_verificacion
        evaluation = sync_technician_verification_state(user, technician, db,
            proceso_responsable="REVISION_DOCUMENTAL_ADMIN", actor_usuario_id=admin.id,
            causa_override=payload.observaciones_verificacion)
    else:
        identity_docs = {doc.tipo: doc for doc in current_docs.values() if doc.tipo in IDENTITY_BASIC_TYPES}
        if len(identity_docs) == 3 and all(doc.estado == "APROBADO" and (not doc.fecha_vencimiento or doc.fecha_vencimiento >= date.today()) for doc in identity_docs.values()):
            user.estado_identidad = "VERIFICADO"
        elif any(doc.estado == "RECHAZADO" for doc in identity_docs.values()):
            user.estado_identidad = "RECHAZADO"
        else:
            user.estado_identidad = "EN_REVISION"
        evaluation = {"estado_identidad": user.estado_identidad}
    if any(current_docs[item.documento_id].tipo in IDENTITY_BASIC_TYPES for item in payload.documentos):
        db.add(IdentityVerificationResult(
            solicitud_id=request.id, estado=user.estado_identidad, creado_en=datetime.now(timezone.utc),
            motivo="Revisión administrativa de DNI y selfie: " + user.estado_identidad + ". " + " ".join(doc.observaciones for doc in current_docs.values() if doc.tipo in IDENTITY_BASIC_TYPES and doc.observaciones),
            proceso_responsable="REVISION_ADMINISTRATIVA_IDENTIDAD", version_proceso="1",
            evidencia={"administrador_id": str(admin.id), "version": request.version,
                       "documentos": [str(item.documento_id) for item in payload.documentos],
                       "resultado_automatico_id": str(processing.id) if processing else None,
                       "revision_manual_de_rechazo": bool(processing and processing.estado == "RECHAZADO")},
        ))
    db.commit()
    return {"status": "success", **evaluation, "estado_identidad": user.estado_identidad,
            "revision_completa": user.estado_identidad == "VERIFICADO" and
                (technician is None or technician.estado_verificacion == "VERIFICADO")}


@router.patch("/dossiers/{usuario_id}/verification-review")
@router.patch("/technicians/{usuario_id}/verification-review")
def review_technician_dossier(usuario_id: uuid.UUID, payload: AdminVerificationReviewSchema, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    return apply_technician_review(usuario_id, payload, claims, db)


@router.post('/dossiers/{usuario_id}/identity-verification')
@router.post('/technicians/{usuario_id}/identity-verification')
def process_technician_identity_admin(
    usuario_id: uuid.UUID, payload: IdentityProcessingRetry,
    claims=Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_admin(claims, db)
    user, _ = _dossier_user(db, usuario_id, lock=True)
    result = process_current_identity({'uid': user.firebase_uid}, db, request_id=payload.solicitud_id)
    return {'estado': result.estado, 'observacion': result.motivo}
