"""HU-36 ST-01: caso de uso de cancelación con auditoría."""
from datetime import datetime, timezone
from typing import Protocol, Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cancellation_policy import (
    CancellationContext, CancellationCause, CancellationNotAllowed,
    CancellationPolicy, FaultStatus,
)
from app.cancellation_evidence import EvidenceStore
from app.cancellation_repository import CancellationRepository
from app.financial_settlement import check_third_unjustified_fault_alert, get_refund_for_request
from app.models import RequestCancellation, User, ensure_utc_datetime, format_tegucigalpa_iso
from app.notifications_events import notify_service_cancelled, notify_third_fault_admin_alert
from app.request_states import state_key, transition_request
from app.service_reception import _find_locked_request


class CancellationObserver(Protocol):
    """Punto de extensión: ST-05 registrará aquí el reembolso idempotente."""

    def on_cancelled(self, db: Session, record, cancellation: RequestCancellation) -> None: ...

class RejectedAdjustmentLookup(Protocol):
    def is_rejected(self, db: Session, modalidad: str, solicitud_id: str) -> bool: ...

def _resolve_actor_role(record, user: User) -> str:
    if record.cliente_id == user.firebase_uid:
        return "CLIENTE"
    if record.tecnico_id and record.tecnico_id in {user.firebase_uid, str(user.id)}:
        return "TECNICO"
    raise HTTPException(403, "Solo el cliente o el técnico de la solicitud pueden cancelarla.")


def serialize_cancellation(item: RequestCancellation) -> dict:
    return {
        "solicitud_id": item.solicitud_id,
        "modalidad": item.modalidad,
        "estado": "CANCELADA",
        "estado_previo": item.estado_previo,
        "autor_rol": item.autor_rol,
        "causa": item.causa,
        "motivo": item.motivo,
        "evidencias": list(item.evidencias or []),
        "regla_aplicada": item.regla_aplicada,
        "genera_incumplimiento": item.genera_incumplimiento,
        "responsable_rol": item.responsable_rol,
        "estado_incumplimiento": item.estado_incumplimiento,
        "permite_reapertura": item.permite_reapertura,
        "cancelada_en": format_tegucigalpa_iso(item.cancelada_en),
        "solicitud_reabierta_id": item.solicitud_reabierta_id,
    }


def cancel_request(
    db: Session,
    solicitud_id: str,
    *,
    causa: CancellationCause,
    motivo: str,
    evidencias: list[str],
    claims: dict,
    policy: CancellationPolicy,
    repository: CancellationRepository,
    observers: Sequence[CancellationObserver] = (),
    adjustments: RejectedAdjustmentLookup | None = None,
    evidence: EvidenceStore | None = None,
    now: datetime | None = None,
) -> dict:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(401, "Sesión inválida o expirada")

    modalidad, record = _find_locked_request(db, solicitud_id)  # FOR UPDATE
    actor_role = _resolve_actor_role(record, user)
    record_id = str(record.id)

    existing = repository.get(db, modalidad, record_id)
    if existing is not None:  # reintento idempotente: Cancelada es final
        data = serialize_cancellation(existing)
        data["reembolso"] = get_refund_for_request(db, modalidad, record_id)
        data["alerta_administrativa"] = check_third_unjustified_fault_alert(db, existing.responsable_uid)
        return data
    previous = state_key(record.estado)
    if previous in {"COMPLETADA", "CANCELADA"}:
        raise HTTPException(409, f"La solicitud ya está {previous} y no puede cancelarse.")
    if (record.seguimiento_recepcion or {}).get("incidencia_abierta"):
        raise HTTPException(409, "Una incidencia abierta requiere resolución administrativa.")
    if evidencias:
        if evidence is None:
            raise HTTPException(503, "Almacenamiento de evidencia no disponible.")
        evidence.ensure_owned(user.id, evidencias)
    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    adjustment_rejected = (
        causa is CancellationCause.AJUSTE_RECHAZADO
        and adjustments is not None
        and adjustments.is_rejected(db, modalidad, record_id)
    )
    ctx = CancellationContext(
        previous_state=previous, actor_role=actor_role, cause=causa,
        scheduled_at=ensure_utc_datetime(record.fecha_hora), now=moment,
        adjustment_rejected=adjustment_rejected,
    )
    try:
        decision = policy.evaluate(ctx)
    except CancellationNotAllowed as exc:
        raise HTTPException(409, str(exc)) from exc

    transition_request(db, record, "CANCELADA", role=actor_role, uid=user.firebase_uid, now=moment)

    responsible_uid = None
    if decision.responsible_role == "CLIENTE":
        responsible_uid = record.cliente_id
    elif decision.responsible_role == "TECNICO":
        responsible_uid = record.tecnico_id

    cancellation = RequestCancellation(
        modalidad=modalidad, solicitud_id=record_id,
        autor_uid=user.firebase_uid, autor_rol=actor_role,
        estado_previo=previous, causa=causa.value, motivo=motivo, evidencias=list(evidencias),
        regla_aplicada=decision.rule, genera_incumplimiento=decision.counts_fault,
        responsable_rol=decision.responsible_role, responsable_uid=responsible_uid,
        estado_incumplimiento=(FaultStatus.PENDIENTE_JUSTIFICACION if decision.counts_fault
                               else FaultStatus.NO_APLICA).value,
        permite_reapertura=decision.allows_reopen, cancelada_en=moment,
    )
    repository.add(db, cancellation)
    db.flush()
    for observer in observers:
        observer.on_cancelled(db, record, cancellation)
    notify_service_cancelled(db, record, modalidad=modalidad, actor_role=actor_role, now=moment)
    db.commit()
    data = serialize_cancellation(cancellation)
    data["reembolso"] = get_refund_for_request(db, modalidad, record_id)
    alert = check_third_unjustified_fault_alert(db, cancellation.responsable_uid)
    data["alerta_administrativa"] = alert
    if alert.get("alerta_administrativa_activa"):
        notify_third_fault_admin_alert(
            db,
            responsible_uid=cancellation.responsable_uid,
            solicitud_id=record_id,
            now=moment,
        )
        db.commit()
    return data