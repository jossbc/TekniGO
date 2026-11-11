"""Optional trade documents, separate from mandatory safety verification."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.identity_documents import UploadedPart, _get_user, _parse_multipart_parts, _require_admin
from app.models import TechnicianProfile, TradeCertificate, TradeCertificateDecision, User
from app.security import get_current_user
from app.storage import ALLOWED_CONTENT_TYPES, MAX_DOCUMENT_BYTES, PrivateDocumentStorage

router = APIRouter()
OWNER_PATH = "/technicians/me/certifications"
ADMIN_PATH = "/admin/certifications"


def approved_certificate_owners(db: Session, user_ids) -> set[uuid.UUID]:
    if not user_ids:
        return set()
    return set(db.scalars(select(TradeCertificate.usuario_id).where(
        TradeCertificate.usuario_id.in_(user_ids), TradeCertificate.estado == "APROBADO",
    ).distinct()))


def _technician(claims: dict, db: Session) -> User:
    user = _get_user(claims, db)
    if not any(role.rol == "TECNICO" for role in user.roles) or db.get(TechnicianProfile, user.id) is None:
        raise HTTPException(403, "Solo los técnicos pueden adjuntar y consultar certificaciones.")
    return user


def _metadata(doc: TradeCertificate) -> dict:
    return {"id": str(doc.id), "titulo": doc.titulo, "estado": doc.estado,
            "observaciones": doc.observaciones, "creado_en": doc.creado_en}


def _store_certificate(db: Session, user_id: uuid.UUID, titulo: str, part: UploadedPart) -> dict:
    saved = PrivateDocumentStorage().store(
        user_id=user_id, doc_type="CERTIFICACION_OFICIO", content=part.content,
        declared_content_type=part.content_type, label="certificación",
    )
    doc = TradeCertificate(usuario_id=user_id, titulo=titulo, ruta_privada=saved.ruta_privada,
                           content_type=saved.content_type)
    db.add(doc)
    db.commit()
    return _metadata(doc)


@router.post(OWNER_PATH, status_code=201)
async def upload_certificate(request: Request, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = await run_in_threadpool(_technician, claims, db)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_DOCUMENT_BYTES + 16 * 1024:
            raise HTTPException(413, "El archivo supera el máximo de 5 MB.")
        body.extend(chunk)
    parts = _parse_multipart_parts(request.headers.get("content-type", ""), bytes(body))
    if set(parts) != {"titulo", "archivo"}:
        raise HTTPException(400, "Indica el título y adjunta un archivo.")
    try:
        title = parts["titulo"].content.decode("utf-8").strip()
    except UnicodeError:
        raise HTTPException(400, "El título no es válido.") from None
    if not 1 <= len(title) <= 150 or not title.isprintable():
        raise HTTPException(400, "El título debe tener entre 1 y 150 caracteres.")
    response.headers["Cache-Control"] = "no-store, private"
    return await run_in_threadpool(_store_certificate, db, user.id, title, parts["archivo"])


@router.get(OWNER_PATH)
def list_my_certificates(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = _technician(claims, db)
    docs = db.scalars(select(TradeCertificate).where(TradeCertificate.usuario_id == user.id)
                      .order_by(TradeCertificate.creado_en.desc(), TradeCertificate.id))
    response.headers["Cache-Control"] = "no-store, private"
    return [_metadata(doc) for doc in docs]


@router.get(ADMIN_PATH)
def list_certificates(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db),
                      estado: Literal["PENDIENTE", "APROBADO", "RECHAZADO", "REVOCADO"] = "PENDIENTE",
                      offset: int = Query(0, ge=0), usuario_id: uuid.UUID | None = None):
    _require_admin(claims, db)
    query = select(TradeCertificate, User.nombre_completo).join(User, User.id == TradeCertificate.usuario_id)
    if usuario_id is not None:
        query = query.where(TradeCertificate.usuario_id == usuario_id)
    rows = db.execute(query.where(TradeCertificate.estado == estado)
                      .order_by(TradeCertificate.creado_en.desc(), TradeCertificate.id)
                      .offset(offset).limit(21)).all()
    response.headers["Cache-Control"] = "no-store, private"
    result = {"certificaciones": [dict(_metadata(doc), tecnico=nombre) for doc, nombre in rows[:20]],
              "hay_mas": len(rows) > 20}
    if usuario_id is not None:
        result["tiene_certificaciones"] = bool(rows) or bool(db.scalar(
            select(TradeCertificate.id).where(TradeCertificate.usuario_id == usuario_id).limit(1)
        ))
    return result


class CertificateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: Literal["APROBADO", "RECHAZADO", "REVOCADO"]
    observaciones: str = Field(default="", max_length=2000)


@router.patch(ADMIN_PATH + "/{certificate_id}")
def review_certificate(certificate_id: uuid.UUID, payload: CertificateDecision, claims=Depends(get_current_user),
                       db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    doc = db.get(TradeCertificate, certificate_id, with_for_update=True, populate_existing=True)
    if doc is None:
        raise HTTPException(404, "Certificación no encontrada.")
    observations = payload.observaciones.strip()
    if payload.estado in ("RECHAZADO", "REVOCADO") and not observations:
        raise HTTPException(400, "Escribe una observación para rechazar o revocar la certificación.")
    expected = "APROBADO" if payload.estado == "REVOCADO" else "PENDIENTE"
    if doc.estado != expected:
        raise HTTPException(409, "La certificación ya cambió de estado. Actualiza la lista.")
    doc.estado = payload.estado
    doc.observaciones = observations or None
    db.add(TradeCertificateDecision(certificacion_id=doc.id, administrador_id=admin.id,
                                    estado=doc.estado, observaciones=doc.observaciones))
    db.commit()
    return _metadata(doc)


@router.get(ADMIN_PATH + "/{certificate_id}/content")
def download_certificate(certificate_id: uuid.UUID, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(claims, db)
    doc = db.get(TradeCertificate, certificate_id)
    if doc is None:
        raise HTTPException(404, "Certificación no encontrada.")
    try:
        data = PrivateDocumentStorage().read_private(doc.ruta_privada)
    except FileNotFoundError:
        raise HTTPException(404, "Archivo privado no encontrado.") from None
    except ValueError:
        raise HTTPException(502, "No se pudo recuperar el archivo privado.") from None
    extension = ALLOWED_CONTENT_TYPES[doc.content_type]
    return Response(content=data, media_type=doc.content_type, headers={
        "Cache-Control": "no-store, private",
        "Content-Disposition": f'attachment; filename="certificacion{extension}"',
        "X-Content-Type-Options": "nosniff",
    })
