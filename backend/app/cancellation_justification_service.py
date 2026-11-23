"""HU-36 ST-04 (CA-09): justificación de cancelaciones con evidencia privada."""
from datetime import datetime, timezone
from enum import Enum

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.actors import require_admin, require_user
from app.cancellation_evidence import EvidenceStore
from app.cancellation_policy import FaultStatus
from app.cancellation_repository import CancellationRepository, JustificationRepository
from app.client_requests import _find_request_by_id
from app.models import CancellationJustification, ensure_utc_datetime, format_tegucigalpa_iso


class JustificationDecision(str, Enum):
    APROBAR = "APROBAR"
    RECHAZAR = "RECHAZAR"


def serialize_justification(item: CancellationJustification) -> dict:
    return {
        "id": item.id,
        "cancelacion_id": item.cancelacion_id,
        "motivo": item.motivo,
        "evidencias": list(item.evidencias or []),
        "estado": item.estado,
        "enviada_en": format_tegucigalpa_iso(item.enviada_en),
        "resuelta_en": format_tegucigalpa_iso(item.resuelta_en),
        "observaciones_admin": item.observaciones_admin,
    }


def submit_justification(
    db: Session,
    solicitud_id: str,
    *,
    motivo: str,
    evidencias: list[str],
    claims: dict,
    cancellations: CancellationRepository,
    justifications: JustificationRepository,
    evidence: EvidenceStore,
    now: datetime | None = None,
) -> dict:
    user = require_user(db, claims)
    modalidad, record = _find_request_by_id(db, solicitud_id)
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud")
    cancellation = cancellations.get(db, modalidad, str(record.id))
    if cancellation is None:
        raise HTTPException(404, "La solicitud no tiene una cancelación registrada.")
    if not cancellation.genera_incumplimiento:
        raise HTTPException(409, "Esta cancelación no generó incumplimiento; no requiere justificación.")
    if cancellation.responsable_uid != user.firebase_uid:
        raise HTTPException(403, "Solo quien tiene el incumplimiento registrado puede justificarlo.")

    existing = justifications.get_by_cancellation(db, cancellation.id)
    if existing is not None:
        if existing.estado == "PENDIENTE":  # reintento idempotente
            return serialize_justification(existing)
        raise HTTPException(409, "La cancelación ya tiene una justificación resuelta.")
    if cancellation.estado_incumplimiento != FaultStatus.PENDIENTE_JUSTIFICACION.value:
        raise HTTPException(409, "El incumplimiento ya no está pendiente de justificación.")

    evidence.ensure_owned(user.id, evidencias)
    item = CancellationJustification(
        cancelacion_id=cancellation.id, autor_uid=user.firebase_uid, motivo=motivo,
        evidencias=list(evidencias), estado="PENDIENTE",
        enviada_en=ensure_utc_datetime(now or datetime.now(timezone.utc)),
    )
    justifications.add(db, item)
    try:
        db.commit()
    except IntegrityError:  # envío concurrente: gana el primero
        db.rollback()
        return serialize_justification(justifications.get_by_cancellation(db, cancellation.id))
    return serialize_justification(item)


def resolve_justification(
    db: Session,
    justification_id: str,
    *,
    decision: JustificationDecision,
    observaciones: str,
    claims: dict,
    cancellations: CancellationRepository,
    justifications: JustificationRepository,
    now: datetime | None = None,
) -> dict:
    admin = require_admin(db, claims)
    item = justifications.get(db, justification_id)
    if item is None:
        raise HTTPException(404, "Justificación no encontrada.")
    db.refresh(item, with_for_update=True)

    approved = decision is JustificationDecision.APROBAR
    target = "APROBADA" if approved else "RECHAZADA"
    if item.estado != "PENDIENTE":
        if item.estado == target:
            return serialize_justification(item)
        raise HTTPException(409, f"La justificación ya fue {item.estado.lower()}.")

    cancellation = cancellations.get_by_id(db, item.cancelacion_id)
    item.estado = target
    item.resuelta_en = ensure_utc_datetime(now or datetime.now(timezone.utc))
    item.admin_uid = admin.firebase_uid
    item.observaciones_admin = observaciones
    # CA-09: aprobada => la falta sale del cómputo; ambos registros se conservan como auditoría
    cancellation.estado_incumplimiento = (
        FaultStatus.JUSTIFICADA if approved else FaultStatus.NO_JUSTIFICADA
    ).value
    db.commit()
    return serialize_justification(item)