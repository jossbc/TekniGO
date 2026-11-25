"""HU-42 ST-02: carga de liquidaciones por el técnico y bandeja administrativa.

Esta etapa solo registra envíos PENDIENTES y permite consultarlos. No aprueba,
no rechaza y nunca modifica la deuda ni la restricción financiera (CA-02).
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settlements as rules
from app.database import get_db
from app.identity_documents import UploadedPart, _get_user, _parse_multipart_parts, _require_admin
from app.models import (
    CommissionSettlement,
    CommissionSettlementAudit,
    TechnicianProfile,
    User,
)
from app.security import get_current_user
from app.storage import MAX_DOCUMENT_BYTES, PrivateDocumentStorage, validate_document_bytes
from app.technician_activation import is_operationally_enabled

router = APIRouter()
logger = logging.getLogger(__name__)

PROOF_DOC_TYPE = "COMPROBANTE_LIQUIDACION"
PROOF_ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp")
PROOF_FORMATS_LABEL = "JPG, PNG o WEBP"
# Margen para los campos de texto del multipart además del archivo.
_MAX_BODY_BYTES = MAX_DOCUMENT_BYTES + 64 * 1024
NO_STORE = {"Cache-Control": "no-store, private"}
PROOF_HEADERS = {
    **NO_STORE,
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": 'inline; filename="comprobante"',
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _text_field(parts: dict[str, UploadedPart], name: str) -> str:
    part = parts.get(name)
    if part is None:
        raise HTTPException(400, f"Falta el campo '{name}'")
    try:
        return part.content.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise HTTPException(400, f"El campo '{name}' no es válido") from None


def _serialize_for_owner(row: CommissionSettlement) -> dict:
    # El titular consulta estado y observaciones; nunca ruta ni contenido del comprobante.
    return {
        "id": str(row.id),
        "importe_declarado": str(row.importe_declarado),
        "referencia": row.referencia,
        "estado": row.estado,
        "importe_verificado": str(row.importe_verificado) if row.importe_verificado is not None else None,
        "importe_aplicado": str(row.importe_aplicado),
        "importe_excedente": str(row.importe_excedente),
        "observacion": row.observacion,
        "creado_en": _iso(row.creado_en),
        "revisado_en": _iso(row.revisado_en),
    }


def _serialize_for_admin(row: CommissionSettlement, tech: User | None, reviewer: User | None) -> dict:
    return {
        **_serialize_for_owner(row),
        "tecnico_id": str(row.tecnico_id),
        "tecnico_nombre": tech.nombre_completo if tech else None,
        "tecnico_telefono": tech.telefono if tech else None,
        "content_type": row.content_type,
        "tamano_bytes": row.tamano_bytes,
        "revisado_por": str(row.revisado_por) if row.revisado_por else None,
        "revisado_por_nombre": reviewer.nombre_completo if reviewer else None,
    }


def _require_technician(claims: dict, db: Session, *, lock: bool = False) -> User:
    user = _get_user(claims, db, lock=lock)
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Solo los técnicos pueden liquidar comisiones")
    return user


def get_settlement_summary(claims: dict, db: Session) -> dict:
    user = _require_technician(claims, db)
    profile = db.get(TechnicianProfile, user.id)
    rows = db.scalars(
        select(CommissionSettlement)
        .where(CommissionSettlement.tecnico_id == user.id)
        .order_by(CommissionSettlement.creado_en.desc())
    ).all()
    pending = sum((r.importe_declarado for r in rows if r.estado == rules.PENDIENTE), Decimal("0.00"))
    return {
        "moneda": "HNL",
        "deuda_vigente": str(rules.outstanding_debt(db, user)),
        "importe_en_revision": str(rules.money(pending)),
        "restriccion_financiera": bool(profile.restriccion_financiera) if profile else False,
        "servicios_efectivo_sin_liquidar": int(profile.servicios_efectivo_sin_liquidar or 0) if profile else 0,
        "suspendido": bool(profile.suspendido) if profile else False,
        "estado_verificacion": profile.estado_verificacion if profile else None,
        "habilitado_operativamente": is_operationally_enabled(user, profile) if profile else False,
        "formatos_permitidos": list(PROOF_ALLOWED_TYPES),
        "formatos_texto": PROOF_FORMATS_LABEL,
        "tamano_maximo_bytes": MAX_DOCUMENT_BYTES,
        "liquidaciones": [_serialize_for_owner(r) for r in rows],
    }


def submit_settlement(
    *,
    importe: str,
    referencia: str,
    comprobante: UploadedPart,
    claims: dict,
    db: Session,
    storage: PrivateDocumentStorage | None = None,
) -> dict:
    user = _require_technician(claims, db, lock=True)  # serializa envíos del mismo técnico
    try:
        amount = rules.validate_amount(importe)
        normalized = rules.normalize_reference(referencia)
    except rules.SettlementValidationError as exc:
        raise HTTPException(400, str(exc)) from None

    if rules.outstanding_debt(db, user) <= 0:
        raise HTTPException(409, "No tienes comisiones pendientes por liquidar")

    # Validación en servidor del contenido real, antes de guardar nada.
    real_mime = validate_document_bytes(comprobante.content, comprobante.content_type, "el comprobante")
    if real_mime not in PROOF_ALLOWED_TYPES:
        raise HTTPException(400, f"El comprobante debe ser una imagen ({PROOF_FORMATS_LABEL})")
    digest = hashlib.sha256(comprobante.content).hexdigest()

    # CA-06: referencia o comprobante ya usados por una liquidación no rechazada.
    duplicate = db.scalar(
        select(CommissionSettlement.id).where(
            CommissionSettlement.estado != rules.RECHAZADA,
            (CommissionSettlement.referencia_normalizada == normalized)
            | (CommissionSettlement.comprobante_sha256 == digest),
        )
    )
    if duplicate is not None:
        raise HTTPException(409, "Esta referencia o este comprobante ya fue registrado en otra liquidación")

    store = storage or PrivateDocumentStorage()
    saved = store.store(
        user_id=user.id,
        doc_type=PROOF_DOC_TYPE,
        content=comprobante.content,
        declared_content_type=comprobante.content_type,
        label="el comprobante",
    )
    row = CommissionSettlement(
        id=uuid.uuid4(),
        tecnico_id=user.id,
        importe_declarado=amount,
        referencia=referencia.strip(),
        referencia_normalizada=normalized,
        ruta_privada=saved.ruta_privada,
        content_type=saved.content_type,
        tamano_bytes=saved.tamano_bytes,
        comprobante_sha256=saved.sha256,
        estado=rules.PENDIENTE,
    )
    try:
        db.add(row)
        db.commit()  # carrera: los índices únicos parciales son la última defensa
    except BaseException as exc:
        db.rollback()
        _discard_stored_proof(store, saved.ruta_privada)  # no dejar archivos cifrados huérfanos
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Esta referencia o este comprobante ya fue registrado en otra liquidación") from None
        raise
    db.refresh(row)
    return _serialize_for_owner(row)


def _discard_stored_proof(store: PrivateDocumentStorage, ruta_privada: str) -> None:
    try:
        store.delete_private(ruta_privada)
    except Exception:  # la limpieza nunca debe ocultar el error original
        logger.warning("No se pudo eliminar un comprobante huérfano de liquidación")


def list_admin_settlements(claims: dict, db: Session, estado: str | None = None) -> dict:
    _require_admin(claims, db)
    query = select(CommissionSettlement).order_by(CommissionSettlement.creado_en.asc())
    if estado:
        clean = estado.strip().upper()
        if clean not in rules.SETTLEMENT_STATES:
            raise HTTPException(400, "Estado de liquidación inválido")
        query = query.where(CommissionSettlement.estado == clean)
    rows = db.scalars(query).all()
    ids = {r.tecnico_id for r in rows} | {r.revisado_por for r in rows if r.revisado_por}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {
        "moneda": "HNL",
        "liquidaciones": [
            _serialize_for_admin(r, users.get(r.tecnico_id), users.get(r.revisado_por)) for r in rows
        ],
    }


def get_proof_for_admin(
    settlement_id: uuid.UUID, claims: dict, db: Session, storage: PrivateDocumentStorage | None = None
) -> tuple[bytes, str]:
    admin = _require_admin(claims, db)
    row = db.get(CommissionSettlement, settlement_id)
    if row is None:
        raise HTTPException(404, "No se encontró la liquidación")
    store = storage or PrivateDocumentStorage()
    try:
        content = store.read_private(row.ruta_privada)
        # Auditoría de acceso: quién, qué liquidación (sin contenido ni ruta).
        logger.info("Acceso administrativo a comprobante: admin=%s liquidacion=%s", admin.id, row.id)
        return content, row.content_type
    except FileNotFoundError:
        raise HTTPException(404, "Comprobante no encontrado") from None
    except ValueError:
        raise HTTPException(502, "No se pudo recuperar el comprobante privado") from None


@router.get("/users/me/settlements")
def my_settlements_endpoint(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers.update(NO_STORE)
    return get_settlement_summary(claims, db)


@router.post("/users/me/settlements", status_code=201)
async def submit_settlement_endpoint(
    request: Request, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    response.headers.update(NO_STORE)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        raise HTTPException(413, "El comprobante supera el tamaño máximo permitido de 5 MB")
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(413, "El comprobante supera el tamaño máximo permitido de 5 MB")
    parts = _parse_multipart_parts(request.headers.get("content-type", ""), body)
    if "comprobante" not in parts:
        raise HTTPException(400, "Falta adjuntar el comprobante")
    return submit_settlement(
        importe=_text_field(parts, "importe"),
        referencia=_text_field(parts, "referencia"),
        comprobante=parts["comprobante"],
        claims=claims,
        db=db,
    )


@router.get("/admin/settlements")
def admin_settlements_endpoint(
    response: Response, estado: str | None = None, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    response.headers.update(NO_STORE)
    return list_admin_settlements(claims, db, estado)


def _serialize_audit(entry: CommissionSettlementAudit, reviewer: User | None) -> dict:
    return {
        "id": str(entry.id),
        "liquidacion_id": str(entry.liquidacion_id),
        "tecnico_id": str(entry.tecnico_id),
        "accion": entry.accion,
        "estado_anterior": entry.estado_anterior,
        "estado_nuevo": entry.estado_nuevo,
        "referencia": entry.referencia,
        "importe_verificado": str(entry.importe_verificado) if entry.importe_verificado is not None else None,
        "importe_aplicado": str(entry.importe_aplicado),
        "importe_excedente": str(entry.importe_excedente),
        "deuda_anterior": str(entry.deuda_anterior),
        "deuda_posterior": str(entry.deuda_posterior),
        "contador_anterior": entry.contador_anterior,
        "contador_posterior": entry.contador_posterior,
        "restriccion_anterior": bool(entry.restriccion_anterior),
        "restriccion_posterior": bool(entry.restriccion_posterior),
        "observacion": entry.observacion,
        "responsable_id": str(entry.responsable_id),
        "responsable_nombre": reviewer.nombre_completo if reviewer else None,
        "creado_en": _iso(entry.creado_en),
    }


class SettlementDecisionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    importe_verificado: Any = None
    observacion: str | None = None


def decide_settlement(
    settlement_id: uuid.UUID,
    payload: SettlementDecisionSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-42 ST-04 (CA-03 a CA-07): aprobación transaccional o rechazo con auditoría."""
    admin = _require_admin(claims, db)
    decision = (payload.decision or "").strip().upper()
    if decision not in ("APROBAR", "RECHAZAR"):
        raise HTTPException(400, "Decisión inválida. Usa 'APROBAR' o 'RECHAZAR'.")

    # Bloqueo pesimista sobre la liquidación para evitar decisiones concurrentes o repetidas (CA-06).
    row = db.scalar(
        select(CommissionSettlement)
        .where(CommissionSettlement.id == settlement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "No se encontró la liquidación")
    if row.estado != rules.PENDIENTE:
        raise HTTPException(409, "Esta liquidación ya fue decidida y no puede modificarse")

    tech = db.scalar(
        select(User).where(User.id == row.tecnico_id).with_for_update().execution_options(populate_existing=True)
    )
    if tech is None:
        raise HTTPException(404, "No se encontró el técnico asociado a la liquidación")
    profile = db.scalar(
        select(TechnicianProfile)
        .where(TechnicianProfile.usuario_id == tech.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    eval_now = now if now is not None else datetime.now(timezone.utc)
    if eval_now.tzinfo is None:
        eval_now = eval_now.replace(tzinfo=timezone.utc)

    estado_anterior = row.estado
    deuda_anterior = rules.outstanding_debt(db, tech)
    contador_anterior = int(profile.servicios_efectivo_sin_liquidar or 0) if profile else 0
    restriccion_anterior = bool(profile.restriccion_financiera) if profile else False
    obs = (payload.observacion or "").strip() or None

    if decision == "RECHAZAR":
        if not obs:
            raise HTTPException(400, "Debes indicar una observación para rechazar la liquidación")
        if not rules.can_transition(estado_anterior, rules.RECHAZADA):
            raise HTTPException(409, "Esta liquidación ya fue decidida y no puede modificarse")

        row.estado = rules.RECHAZADA
        row.importe_verificado = None
        row.importe_aplicado = Decimal("0.00")
        row.importe_excedente = Decimal("0.00")
        row.observacion = obs
        row.revisado_por = admin.id
        row.revisado_en = eval_now

        accion = "RECHAZO"
        deuda_posterior = deuda_anterior
        contador_posterior = contador_anterior
        restriccion_posterior = restriccion_anterior
    else:
        if payload.importe_verificado is None or str(payload.importe_verificado).strip() == "":
            raise HTTPException(400, "Debes indicar el importe verificado para aprobar la liquidación")
        try:
            verificado = rules.validate_amount(payload.importe_verificado)
        except rules.SettlementValidationError as exc:
            raise HTTPException(400, str(exc)) from None

        aplicado, excedente = rules.split_applied_amount(verificado, deuda_anterior)
        deuda_posterior = max(deuda_anterior - aplicado, Decimal("0.00"))
        target_state = rules.EN_CONCILIACION if excedente > 0 else rules.APROBADA
        if not rules.can_transition(estado_anterior, target_state):
            raise HTTPException(409, "Esta liquidación ya fue decidida y no puede modificarse")

        if excedente > 0:
            accion = "SOBREPAGO_CONCILIACION"
        elif deuda_posterior == Decimal("0.00"):
            accion = "APROBACION_TOTAL"
        else:
            accion = "APROBACION_PARCIAL"

        row.estado = target_state
        row.importe_verificado = verificado
        row.importe_aplicado = aplicado
        row.importe_excedente = excedente
        row.observacion = obs
        row.revisado_por = admin.id
        row.revisado_en = eval_now

        contador_posterior, restriccion_posterior = rules.apply_regularization_if_settled(
            db,
            tech,
            profile,
            remaining_debt=deuda_posterior,
            admin_id=admin.id,
            reference=row.referencia,
        )

    audit = CommissionSettlementAudit(
        id=uuid.uuid4(),
        liquidacion_id=row.id,
        tecnico_id=tech.id,
        accion=accion,
        estado_anterior=estado_anterior,
        estado_nuevo=row.estado,
        referencia=row.referencia,
        importe_verificado=row.importe_verificado,
        importe_aplicado=row.importe_aplicado,
        importe_excedente=row.importe_excedente,
        deuda_anterior=deuda_anterior,
        deuda_posterior=deuda_posterior,
        contador_anterior=contador_anterior,
        contador_posterior=contador_posterior,
        restriccion_anterior=restriccion_anterior,
        restriccion_posterior=restriccion_posterior,
        observacion=row.observacion,
        responsable_id=admin.id,
        creado_en=eval_now,
    )
    db.add(audit)
    db.commit()
    db.refresh(row)
    db.refresh(audit)

    logger.info(
        "Decisión administrativa de liquidación: admin=%s liquidacion=%s accion=%s aplicado=%s excedente=%s",
        admin.id, row.id, accion, row.importe_aplicado, row.importe_excedente,
    )
    return {
        "liquidacion": _serialize_for_admin(row, tech, admin),
        "efecto_financiero": {
            "accion": accion,
            "moneda": "HNL",
            "deuda_anterior": str(deuda_anterior),
            "deuda_posterior": str(deuda_posterior),
            "importe_aplicado": str(row.importe_aplicado),
            "importe_excedente": str(row.importe_excedente),
            "servicios_efectivo_sin_liquidar": contador_posterior,
            "restriccion_financiera": restriccion_posterior,
            "suspendido": bool(profile.suspendido) if profile else False,
            "estado_verificacion": profile.estado_verificacion if profile else None,
        },
        "auditoria": _serialize_audit(audit, admin),
    }


def list_settlement_audit(settlement_id: uuid.UUID, claims: dict, db: Session) -> dict:
    _require_admin(claims, db)
    row = db.get(CommissionSettlement, settlement_id)
    if row is None:
        raise HTTPException(404, "No se encontró la liquidación")
    entries = db.scalars(
        select(CommissionSettlementAudit)
        .where(CommissionSettlementAudit.liquidacion_id == settlement_id)
        .order_by(CommissionSettlementAudit.creado_en.asc())
    ).all()
    reviewer_ids = {e.responsable_id for e in entries}
    reviewers = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(reviewer_ids))).all()}
        if reviewer_ids
        else {}
    )
    return {
        "liquidacion_id": str(settlement_id),
        "movimientos": [_serialize_audit(e, reviewers.get(e.responsable_id)) for e in entries],
    }


@router.get("/admin/settlements/{settlement_id}/proof")
def admin_settlement_proof_endpoint(
    settlement_id: uuid.UUID, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    content, media_type = get_proof_for_admin(settlement_id, claims, db)
    return Response(content=content, media_type=media_type, headers=PROOF_HEADERS)


@router.post("/admin/settlements/{settlement_id}/decision")
def admin_decide_settlement_endpoint(
    settlement_id: uuid.UUID,
    payload: SettlementDecisionSchema,
    response: Response,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    return decide_settlement(settlement_id, payload, claims, db)


@router.get("/admin/settlements/{settlement_id}/audit")
def admin_settlement_audit_endpoint(
    settlement_id: uuid.UUID,
    response: Response,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    return list_settlement_audit(settlement_id, claims, db)
