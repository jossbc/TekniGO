"""HU-36 ST-03: solicitud de ajuste único y respuesta del cliente."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Protocol, Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjustment_policy import AdjustmentContext, AdjustmentPolicy, AdjustmentRuleViolation
from app.adjustment_repository import AdjustmentRepository
from app.financial_settlement import (
    calculate_payment_split,
    configured_commission_rate,
    register_complementary_payment,
)
from app.models import ServiceAdjustment, User, ensure_utc_datetime, format_tegucigalpa_iso
from app.notifications_events import notify_adjustment_requested, notify_adjustment_responded
from app.request_states import state_key
from app.service_reception import _find_locked_request
from app.service_completion import has_active_incident

CENT = Decimal("0.01")


class AdjustmentDecision(str, Enum):
    APROBAR = "APROBAR"
    RECHAZAR = "RECHAZAR"


class AdjustmentObserver(Protocol):
    """Punto de extensión: ST-05 registrará el cobro complementario al aprobar."""

    def on_resolved(self, db: Session, record, adjustment: ServiceAdjustment) -> None: ...


def to_money(value) -> Decimal:
    """Política de redondeo: 2 decimales, ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def serialize_adjustment(item: ServiceAdjustment) -> dict:
    return {
        "id": item.id,
        "solicitud_id": item.solicitud_id,
        "modalidad": item.modalidad,
        "motivo": item.motivo,
        "moneda": "HNL",
        "monto_base": str(to_money(item.monto_base)),
        "nuevo_total": str(to_money(item.nuevo_total)),
        "diferencia": str(to_money(item.diferencia)),
        "estado": item.estado,
        "solicitado_en": format_tegucigalpa_iso(item.solicitado_en),
        "respondido_en": format_tegucigalpa_iso(item.respondido_en),
        "comentario_respuesta": item.comentario_respuesta,
    }


def _require_user(db: Session, claims: dict) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(401, "Sesión inválida o expirada")
    return user


def _is_selected_technician(record, user: User) -> bool:
    return bool(record.tecnico_id) and record.tecnico_id in {user.firebase_uid, str(user.id)}


def _agreed_total(record) -> Decimal:
    return to_money(record.monto_total if record.monto_total is not None else record.monto_base)


def request_adjustment(
    db: Session,
    solicitud_id: str,
    *,
    motivo: str,
    monto_base: Decimal,
    nuevo_total: Decimal,
    claims: dict,
    policy: AdjustmentPolicy,
    repository: AdjustmentRepository,
    now: datetime | None = None,
) -> dict:
    user = _require_user(db, claims)
    modalidad, record = _find_locked_request(db, solicitud_id)  # FOR UPDATE
    record_id = str(record.id)

    base, total = to_money(monto_base), to_money(nuevo_total)
    ctx = AdjustmentContext(
        actor_is_selected_technician=_is_selected_technician(record, user),
        request_state=state_key(record.estado),
        already_requested=repository.get(db, modalidad, record_id) is not None,
        monto_base=base,
        nuevo_total=total,
        agreed_total=_agreed_total(record),
        has_active_incident=has_active_incident(record, db)[0],
    )
    try:
        policy.check(ctx)
    except AdjustmentRuleViolation as exc:
        raise HTTPException(exc.status_code, exc.message) from exc

    adjustment = ServiceAdjustment(
        modalidad=modalidad, solicitud_id=record_id,
        tecnico_uid=user.firebase_uid, cliente_uid=record.cliente_id,
        motivo=motivo, monto_base=base, nuevo_total=total, diferencia=total - base,
        estado="PENDIENTE", solicitado_en=ensure_utc_datetime(now or datetime.now(timezone.utc)),
    )
    repository.add(db, adjustment)
    db.flush()
    notify_adjustment_requested(db, record, adjustment, now=adjustment.solicitado_en)
    db.commit()
    return serialize_adjustment(adjustment)


def respond_adjustment(
    db: Session,
    solicitud_id: str,
    *,
    decision: AdjustmentDecision,
    comentario: str | None,
    metodo_pago_diferencia: str | None = None,
    referencia_pasarela_diferencia: str | None = None,
    claims: dict,
    repository: AdjustmentRepository,
    observers: Sequence[AdjustmentObserver] = (),
    now: datetime | None = None,
) -> dict:
    user = _require_user(db, claims)
    modalidad, record = _find_locked_request(db, solicitud_id)  # FOR UPDATE
    if record.cliente_id != user.firebase_uid:
        raise HTTPException(403, "Solo el cliente propietario puede responder el ajuste.")

    adjustment = repository.get(db, modalidad, str(record.id))
    if adjustment is None:
        raise HTTPException(404, "La solicitud no tiene un ajuste registrado.")

    target = "APROBADO" if decision is AdjustmentDecision.APROBAR else "RECHAZADO"
    if adjustment.estado != "PENDIENTE":
        if adjustment.estado == target:  # reintento idempotente
            return serialize_adjustment(adjustment)
        raise HTTPException(409, f"El ajuste ya fue {adjustment.estado.lower()} y no puede cambiarse.")
    if state_key(record.estado) != "EN PROCESO":
        raise HTTPException(409, "El ajuste solo puede responderse mientras el servicio está En proceso.")
    if has_active_incident(record, db)[0]:
        raise HTTPException(409, "No se puede responder el ajuste mientras exista una incidencia abierta en revisión administrativa.")

    adjustment.estado = target
    adjustment.respondido_en = ensure_utc_datetime(now or datetime.now(timezone.utc))
    adjustment.respuesta_uid = user.firebase_uid
    adjustment.comentario_respuesta = comentario

    reparto_info = None
    if target == "APROBADO":  # CA-05 y CA-07: solo la aprobación modifica el total acordado
        record.monto_total = to_money(adjustment.nuevo_total)
        record.monto_ajuste = to_money(adjustment.nuevo_total) - to_money(record.monto_base)
        if to_money(adjustment.diferencia) > Decimal("0.00"):
            metodo_elegido = metodo_pago_diferencia
            if not metodo_elegido and (getattr(record, "metodo_pago", "") or "").lower() == "efectivo":
                metodo_elegido = "efectivo"
            if metodo_elegido:
                reparto_info = register_complementary_payment(
                    db, record, adjustment,
                    metodo_pago_diferencia=metodo_elegido,
                    referencia_pasarela_diferencia=referencia_pasarela_diferencia,
                    now=now,
                )
    for observer in observers:
        observer.on_resolved(db, record, adjustment)
    notify_adjustment_responded(
        db,
        record,
        adjustment,
        approved=(target == "APROBADO"),
        now=adjustment.respondido_en,
    )
    db.commit()
    data = serialize_adjustment(adjustment)
    rate = configured_commission_rate(db)
    data["reparto_financiero"] = (
        reparto_info["reparto"] if reparto_info else calculate_payment_split(record, commission_rate=rate).to_dict()
    )
    return data


def get_adjustment(
    db: Session, solicitud_id: str, *, claims: dict, repository: AdjustmentRepository,
) -> dict:
    user = _require_user(db, claims)
    modalidad, record = _find_locked_request(db, solicitud_id)
    if record.cliente_id != user.firebase_uid and not _is_selected_technician(record, user):
        raise HTTPException(403, "No participas en esta solicitud.")
    adjustment = repository.get(db, modalidad, str(record.id))
    if adjustment is None:
        raise HTTPException(404, "La solicitud no tiene un ajuste registrado.")
    data = serialize_adjustment(adjustment)
    data["metodo_pago_base"] = (getattr(record, "metodo_pago", None) or "efectivo").lower()
    data["reparto_financiero"] = calculate_payment_split(
        record, commission_rate=configured_commission_rate(db)
    ).to_dict()
    return data