"""HU-36 ST-04: evidencia privada, consulta de cancelación y justificación."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.actors import require_admin, require_user
from app.cancellation_evidence import EvidenceStore, get_evidence_store
from app.cancellation_justification_service import (
    JustificationDecision, list_admin_justifications, resolve_justification, serialize_justification, submit_justification,
)
from app.cancellation_repository import (
    CancellationRepository, JustificationRepository,
    SqlAlchemyCancellationRepository, SqlAlchemyJustificationRepository,
)
from app.cancellation_service import _resolve_actor_role, serialize_cancellation
from app.client_requests import _find_request_by_id
from app.database import get_db
from app.financial_settlement import check_third_unjustified_fault_alert, get_refund_for_request
from app.identity_documents import _parse_multipart_parts
from app.security import get_current_user
from app.storage import MAX_DOCUMENT_BYTES, detect_mime

router = APIRouter(tags=["Justificación de cancelación (HU-36)"])
NO_STORE = "no-store, private"


def get_cancellation_repo() -> CancellationRepository:
    return SqlAlchemyCancellationRepository()


def get_justification_repo() -> JustificationRepository:
    return SqlAlchemyJustificationRepository()


class JustificationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1500)]
    evidencias: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=10)


class ResolveJustificationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: JustificationDecision
    observaciones: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1500)]


@router.post("/service-requests/{solicitud_id}/cancellation-evidence", status_code=201)
async def upload_cancellation_evidence_endpoint(
    solicitud_id: str,
    request: Request,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    evidence: EvidenceStore = Depends(get_evidence_store),
):
    response.headers["Cache-Control"] = NO_STORE
    user = require_user(db, claims)
    _, record = _find_request_by_id(db, solicitud_id)
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud")
    _resolve_actor_role(record, user)  # 403 si no participa
    body = await request.body()
    parts = _parse_multipart_parts(request.headers.get("content-type", ""), body)
    part = parts.get("file") or parts.get("archivo")
    if part is None:
        raise HTTPException(400, "Debes adjuntar un archivo en el campo 'file'.")
    data = evidence.save(owner_id=user.id, content=part.content, content_type=part.content_type, label="la evidencia")
    return {**data, "nombre": getattr(part, "filename", None) or "evidencia"}


@router.get("/service-requests/{solicitud_id}/cancellation")
def get_cancellation_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    cancellations: CancellationRepository = Depends(get_cancellation_repo),
    justifications: JustificationRepository = Depends(get_justification_repo),
):
    response.headers["Cache-Control"] = NO_STORE
    user = require_user(db, claims)
    modalidad, record = _find_request_by_id(db, solicitud_id)
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud")
    _resolve_actor_role(record, user)
    cancellation = cancellations.get(db, modalidad, str(record.id))
    if cancellation is None:
        raise HTTPException(404, "La solicitud no tiene una cancelación registrada.")
    data = serialize_cancellation(cancellation)
    if cancellation.autor_uid != user.firebase_uid:
        data["evidencias"] = []  # la evidencia es privada del autor
    justification = justifications.get_by_cancellation(db, cancellation.id)
    own = justification is not None and justification.autor_uid == user.firebase_uid
    data["justificacion"] = serialize_justification(justification) if own else None
    data["reembolso"] = get_refund_for_request(db, modalidad, str(record.id))
    data["alerta_administrativa"] = check_third_unjustified_fault_alert(db, cancellation.responsable_uid)
    return data


@router.post("/service-requests/{solicitud_id}/cancellation/justification", status_code=201)
def submit_justification_endpoint(
    solicitud_id: str,
    payload: JustificationSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    cancellations: CancellationRepository = Depends(get_cancellation_repo),
    justifications: JustificationRepository = Depends(get_justification_repo),
    evidence: EvidenceStore = Depends(get_evidence_store),
):
    response.headers["Cache-Control"] = NO_STORE
    return submit_justification(
        db, solicitud_id, motivo=payload.motivo, evidencias=payload.evidencias, claims=claims,
        cancellations=cancellations, justifications=justifications, evidence=evidence,
    )

@router.get("/admin/cancellation-justifications")
def list_admin_justifications_endpoint(
    response: Response,
    estado: str = "TODOS",
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    cancellations: CancellationRepository = Depends(get_cancellation_repo),
    justifications: JustificationRepository = Depends(get_justification_repo),
):
    response.headers["Cache-Control"] = NO_STORE
    return list_admin_justifications(
        db, estado=estado, claims=claims, cancellations=cancellations, justifications=justifications,
    )

@router.post("/admin/cancellation-justifications/{justification_id}/resolve")
def resolve_justification_endpoint(
    justification_id: str,
    payload: ResolveJustificationSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    cancellations: CancellationRepository = Depends(get_cancellation_repo),
    justifications: JustificationRepository = Depends(get_justification_repo),
):
    response.headers["Cache-Control"] = NO_STORE
    return resolve_justification(
        db, justification_id, decision=payload.decision, observaciones=payload.observaciones,
        claims=claims, cancellations=cancellations, justifications=justifications,
    )


def _serve(evidence: EvidenceStore, refs: list[str], index: int) -> Response:
    if index < 0 or index >= len(refs):
        raise HTTPException(404, "Evidencia no encontrada.")
    content = evidence.read(refs[index])
    return Response(
        content=content, media_type=detect_mime(content) or "application/octet-stream",
        headers={"Cache-Control": NO_STORE, "Content-Disposition": "inline"},
    )


@router.get("/admin/cancellation-justifications/{justification_id}/evidence/{index}")
def download_justification_evidence_endpoint(
    justification_id: str, index: int,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    justifications: JustificationRepository = Depends(get_justification_repo),
    evidence: EvidenceStore = Depends(get_evidence_store),
):
    require_admin(db, claims)
    item = justifications.get(db, justification_id)
    if item is None:
        raise HTTPException(404, "Justificación no encontrada.")
    return _serve(evidence, list(item.evidencias or []), index)


@router.get("/admin/cancellations/{cancellation_id}/evidence/{index}")
def download_cancellation_evidence_endpoint(
    cancellation_id: int, index: int,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    cancellations: CancellationRepository = Depends(get_cancellation_repo),
    evidence: EvidenceStore = Depends(get_evidence_store),
):
    require_admin(db, claims)
    item = cancellations.get_by_id(db, cancellation_id)
    if item is None:
        raise HTTPException(404, "Cancelación no encontrada.")
    return _serve(evidence, list(item.evidencias or []), index)