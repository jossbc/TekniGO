"""HU-38 ST-04: Puente de ciclo de vida entre pagos digitales, aceptación, retención lógica y cierre."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.financial_settlement import register_complementary_payment
from app.models import (
    DigitalPaymentIntent,
    DirectRequest,
    OpenRequest,
    OpenRequestProposal,
    ServiceAdjustment,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.notifications_events import notify_adjustment_responded, notify_open_request_award
from app.payment_attempt_state import (
    PaymentAttemptStatus,
    PaymentConcept,
    PaymentRetentionStatus,
)
from app.payment_gateway_port import to_hnl_money
from app.payment_reconciliation_service import PaymentLifecycleObserver
from app.request_states import state_key, transition_request


def _lock_service_record(db: Session, modalidad: str, solicitud_id: str) -> Any | None:
    if modalidad == "DIRECTA":
        lookup_id = solicitud_id
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            try:
                lookup_id = str(uuid.UUID(solicitud_id))
            except ValueError:
                return None
        return db.scalar(
            select(DirectRequest)
            .where(DirectRequest.id == lookup_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return db.scalar(
        select(OpenRequest)
        .where(OpenRequest.id == solicitud_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _complete_open_request_award_after_payment(
    db: Session,
    record: OpenRequest,
    attempt: DigitalPaymentIntent,
    external_ref: str,
    moment: datetime,
) -> None:
    """Completa de forma idempotente la adjudicación (HU-31) solo tras pago digital CONFIRMADO (CA-01)."""
    current_state = state_key(record.estado or "")
    if current_state == "ACEPTADA":
        if not record.referencia_pasarela:
            record.referencia_pasarela = external_ref
        return
    if current_state not in {"PUBLICADA", "CON OFERTAS"}:
        return
    if not attempt.propuesta_id:
        record.metodo_pago = "tarjeta"
        record.referencia_pasarela = external_ref
        return

    all_proposals = list(
        db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    winning = next((p for p in all_proposals if str(p.id) == str(attempt.propuesta_id)), None)
    if winning is None:
        return

    agreed = to_hnl_money(winning.monto)
    ajuste_actual = to_hnl_money(record.monto_ajuste or Decimal("0.00"))
    record.tecnico_id = winning.tecnico_id
    record.presupuesto = agreed
    record.monto_base = agreed
    record.monto_total = to_hnl_money(agreed + ajuste_actual)
    record.metodo_pago = "tarjeta"
    record.referencia_pasarela = external_ref
    attempt.tecnico_uid = winning.tecnico_id
    transition_request(db, record, "ACEPTADA", role="CLIENTE", uid=attempt.cliente_uid, now=moment)

    winning.estado = "ACEPTADA"
    winning.activa = True
    winning.actualizada_en = moment

    discarded: list[OpenRequestProposal] = []
    for other in all_proposals:
        if str(other.id) == str(winning.id):
            continue
        other_state = (other.estado or "PENDIENTE").strip().upper()
        if other.activa or other_state == "PENDIENTE":
            other.estado = "RECHAZADA"
            other.activa = False
            other.actualizada_en = moment
            discarded.append(other)

    notify_open_request_award(db, record, winning, discarded, now=moment)


class ServicePaymentConfirmationObserver(PaymentLifecycleObserver):
    """
    Observer concreto (OCP por interfaz):
    - Al confirmarse un pago BASE: registra la referencia verificada y formaliza adjudicación abierta si aplica.
    - Al confirmarse un pago AJUSTE: registra el cobro complementario de la diferencia con tarjeta.
    - Ante fallo/abandono: no acepta la contratación ni acredita fondos (CA-02).
    """

    def on_payment_confirmed(
        self,
        db: Session,
        attempt: DigitalPaymentIntent,
        moment: datetime,
    ) -> None:
        record = _lock_service_record(db, attempt.modalidad, attempt.solicitud_id)
        if record is None:
            return
        external_ref = (attempt.payment_intent_id or attempt.referencia_pasarela or attempt.referencia_interna).strip()

        if attempt.concepto == PaymentConcept.BASE.value:
            record.metodo_pago = "tarjeta"
            record.referencia_pasarela = external_ref
            if attempt.modalidad == "ABIERTA" and attempt.propuesta_id:
                _complete_open_request_award_after_payment(db, record, attempt, external_ref, moment)
            return

        if attempt.concepto == PaymentConcept.AJUSTE.value:
            adjustment = db.scalar(
                select(ServiceAdjustment)
                .where(
                    ServiceAdjustment.modalidad == attempt.modalidad,
                    ServiceAdjustment.solicitud_id == str(record.id),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if adjustment is None:
                return
            if adjustment.estado == "PENDIENTE":
                adjustment.estado = "APROBADO"
                adjustment.respondido_en = moment
                adjustment.respuesta_uid = attempt.cliente_uid
                record.monto_total = to_hnl_money(adjustment.nuevo_total)
                record.monto_ajuste = to_hnl_money(adjustment.nuevo_total) - to_hnl_money(record.monto_base)
                notify_adjustment_responded(
                    db,
                    record,
                    adjustment,
                    approved=True,
                    now=moment,
                )
            if adjustment.estado == "APROBADO":
                register_complementary_payment(
                    db,
                    record,
                    adjustment,
                    metodo_pago_diferencia="tarjeta",
                    referencia_pasarela_diferencia=external_ref,
                    now=moment,
                )
                
    def on_payment_failed_or_abandoned(
        self,
        db: Session,
        attempt: DigitalPaymentIntent,
        moment: datetime,
    ) -> None:
        # CA-02: fallo, pendiente o abandono nunca formaliza la contratación ni acredita fondos al técnico.
        return


def verify_confirmed_base_payment_for_acceptance(
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    record: Any,
) -> DigitalPaymentIntent | None:
    """
    CA-01 / CA-02: si la solicitud tiene intentos digitales HU-38 registrados, solo permite aceptar/adjudicar
    cuando exista un cobro BASE CONFIRMADO por la pasarela (rechaza PENDIENTE/RECHAZADO/ABANDONADO).
    """
    attempts = list(
        db.scalars(
            select(DigitalPaymentIntent).where(
                DigitalPaymentIntent.modalidad == modalidad,
                DigitalPaymentIntent.solicitud_id == str(solicitud_id),
                DigitalPaymentIntent.concepto == PaymentConcept.BASE.value,
            )
        ).all()
    )
    if not attempts:
        # Compatibilidad con pruebas heredadas de HU-23/HU-31 que no usaban tabla de intentos HU-38
        if not getattr(record, "referencia_pasarela", None):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Con tarjeta se requiere confirmación verificable de pago de la pasarela antes de aceptar (CA-01).",
            )
        return None

    confirmed = next(
        (
            row
            for row in attempts
            if row.estado == PaymentAttemptStatus.CONFIRMADO.value
            and row.estado_retencion in {PaymentRetentionStatus.RETENIDO.value, PaymentRetentionStatus.LIBERADO.value}
        ),
        None,
    )
    if confirmed is None:
        latest_state = attempts[0].estado
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"El pago digital inicial aún no está confirmado por la pasarela "
                f"(estado actual: {latest_state}). Completa o reintenta el pago antes de aceptar."
            ),
        )
    if not getattr(record, "referencia_pasarela", None):
        record.referencia_pasarela = confirmed.payment_intent_id or confirmed.referencia_pasarela
    return confirmed


def release_retained_digital_payments_on_completion(
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """
    CA-03: Al pasar el servicio a COMPLETADA, libera la retención lógica de todos los cobros digitales
    confirmados (BASE y, si existe, AJUSTE digital) de forma idempotente y devuelve sus referencias externas.
    """
    release_at = ensure_utc_datetime(moment or datetime.now(timezone.utc))
    assert release_at is not None

    confirmed_rows = list(
        db.scalars(
            select(DigitalPaymentIntent)
            .where(
                DigitalPaymentIntent.modalidad == modalidad,
                DigitalPaymentIntent.solicitud_id == str(solicitud_id),
                DigitalPaymentIntent.estado == PaymentAttemptStatus.CONFIRMADO.value,
            )
            .order_by(DigitalPaymentIntent.creado_en.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )

    refs: list[str] = []
    released_details: list[dict[str, Any]] = []
    total_released = Decimal("0.00")

    for row in confirmed_rows:
        if row.estado_retencion == PaymentRetentionStatus.RETENIDO.value:
            row.estado_retencion = PaymentRetentionStatus.LIBERADO.value
            row.liberado_en = row.liberado_en or release_at
            row.actualizado_en = release_at

        ext_ref = (row.payment_intent_id or row.referencia_pasarela or "").strip()
        if ext_ref and ext_ref not in refs:
            refs.append(ext_ref)
        total_released = to_hnl_money(total_released + to_hnl_money(row.monto))
        released_details.append(
            {
                "intento_id": row.id,
                "concepto": row.concepto,
                "monto": str(to_hnl_money(row.monto)),
                "referencia_interna": row.referencia_interna,
                "referencia_externa": ext_ref or None,
                "estado": row.estado,
                "estado_retencion": row.estado_retencion,
                "liberado_en": format_tegucigalpa_iso(row.liberado_en),
                "proveedor": row.proveedor,
                "entorno": row.entorno,
            }
        )

    return {
        "referencia_consolidada": ",".join(refs) if refs else None,
        "monto_liberado_total": str(total_released),
        "pagos": released_details,
    }


def retained_digital_balance_for_technician(db: Session, user: User) -> Decimal:
    """CA-03: Suma de montos digitales confirmados en retención lógica (NO disponibles en billetera aún)."""
    identities = (user.firebase_uid, str(user.id))
    total = db.scalar(
        select(func.coalesce(func.sum(DigitalPaymentIntent.monto), 0)).where(
            DigitalPaymentIntent.tecnico_uid.in_(identities),
            DigitalPaymentIntent.estado == PaymentAttemptStatus.CONFIRMADO.value,
            DigitalPaymentIntent.estado_retencion == PaymentRetentionStatus.RETENIDO.value,
        )
    )
    return to_hnl_money(total or Decimal("0.00"))