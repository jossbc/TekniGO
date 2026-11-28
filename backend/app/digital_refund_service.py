"""HU-38 ST-05: Devolución íntegra reintentable, conciliación de fallos y observador OCP de cancelación."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cancellation_service import CancellationObserver
from app.digital_payments_service import (
    DigitalPaymentForbiddenError,
    DigitalPaymentNotFoundError,
    DigitalPaymentRepository,
    SqlAlchemyDigitalPaymentRepository,
    normalize_modalidad,
)
from app.financial_settlement import (
    RefundOnCancellationObserver,
    record_ledger_entry_once,
    serialize_refund,
)
from app.models import (
    DigitalPaymentIntent,
    PaymentRefund,
    RequestCancellation,
    User,
    ensure_utc_datetime,
)
from app.payment_attempt_state import (
    PaymentAttemptStatus,
    PaymentConcept,
    PaymentRetentionStatus,
)
from app.payment_gateway_adapters import get_payment_gateway
from app.payment_gateway_port import (
    CreateRefundRequest,
    GatewayRefundStatus,
    HNL_CURRENCY,
    PaymentGatewayError,
    PaymentGatewayPort,
    to_hnl_money,
)


def _mark_attempts_refunded(
    attempts: list[DigitalPaymentIntent],
    moment: datetime,
) -> None:
    for row in attempts:
        if row.estado == PaymentAttemptStatus.CONFIRMADO.value:
            row.estado = PaymentAttemptStatus.REEMBOLSADO.value
            row.estado_retencion = PaymentRetentionStatus.REEMBOLSADO.value
            row.reembolsado_en = row.reembolsado_en or moment
            row.actualizado_en = moment


class DigitalRefundService:
    """
    Servicio de devolución íntegra y reintentable ante cancelación antes de COMPLETADA (CA-06, CA-07).
    Depende únicamente de las interfaces DigitalPaymentRepository y PaymentGatewayPort (DIP).
    """

    def __init__(
        self,
        db: Session,
        payment_repo: DigitalPaymentRepository,
        gateway: PaymentGatewayPort,
    ) -> None:
        self._db = db
        self._payment_repo = payment_repo
        self._gateway = gateway

    def _confirmed_digital_attempts(
        self,
        modalidad: str,
        solicitud_id: str,
    ) -> list[DigitalPaymentIntent]:
        rows = self._payment_repo.list_by_service(modalidad, solicitud_id)
        return [row for row in rows if row.estado == PaymentAttemptStatus.CONFIRMADO.value]

    def _split_confirmed_amounts(
        self,
        attempts: list[DigitalPaymentIntent],
    ) -> tuple[Decimal, Decimal, Decimal, list[str]]:
        base_total = Decimal("0.00")
        adj_total = Decimal("0.00")
        origin_refs: list[str] = []
        for row in attempts:
            amt = to_hnl_money(row.monto)
            if row.concepto == PaymentConcept.BASE.value:
                base_total = to_hnl_money(base_total + amt)
            elif row.concepto == PaymentConcept.AJUSTE.value:
                adj_total = to_hnl_money(adj_total + amt)
            ref = (row.payment_intent_id or row.referencia_pasarela or "").strip()
            if ref and ref not in origin_refs:
                origin_refs.append(ref)
        return base_total, adj_total, to_hnl_money(base_total + adj_total), origin_refs

    def initiate_full_refund(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        cliente_uid: str,
        moment: datetime | None = None,
    ) -> dict[str, Any] | None:
        """
        Solicita devolución íntegra (100%, comisión 0.00) de todos los cobros digitales confirmados (BASE y AJUSTE).
        Si la pasarela falla, registra PENDIENTE_CONCILIACION sin declarar el dinero devuelto (CA-07).
        """
        norm_mod = normalize_modalidad(modalidad)
        eval_now = ensure_utc_datetime(moment or datetime.now(timezone.utc))
        assert eval_now is not None

        existing = self._db.scalar(
            select(PaymentRefund)
            .where(
                PaymentRefund.modalidad == norm_mod,
                PaymentRefund.solicitud_id == str(solicitud_id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            data = serialize_refund(existing)
            data["reintento_idempotente"] = True
            return data

        confirmed = self._confirmed_digital_attempts(norm_mod, str(solicitud_id))
        if not confirmed:
            return None

        base_digital, ajuste_digital, total_digital, origin_refs = self._split_confirmed_amounts(confirmed)
        if total_digital <= Decimal("0.00"):
            return None

        refund_refs, target_state, last_error = self._execute_gateway_refunds(
            norm_mod,
            str(solicitud_id),
            confirmed,
            attempt_number=1,
        )
        consolidated_refund_ref = (
            ",".join(refund_refs)[:120]
            if refund_refs
            else f"PENDIENTE-{norm_mod[:3]}-{str(solicitud_id)[:18]}"
        )
        refund = PaymentRefund(
            modalidad=norm_mod,
            solicitud_id=str(solicitud_id),
            cliente_uid=cliente_uid,
            monto_base_reembolsado=base_digital,
            monto_ajuste_reembolsado=ajuste_digital,
            monto_reembolso=total_digital,
            monto_comision=Decimal("0.00"),
            referencia_pasarela_origen=",".join(origin_refs)[:220],
            referencia_reembolso_pasarela=consolidated_refund_ref,
            estado_pasarela=target_state,
            intentos=1,
            ultimo_error=last_error,
            iniciado_en=eval_now,
            confirmado_en=eval_now if target_state == "CONFIRMADO_PASARELA" else None,
            actualizado_en=eval_now,
        )
        self._db.add(refund)
        self._db.flush()

        record_ledger_entry_once(
            self._db,
            clave_idempotencia=f"REEMBOLSO-{norm_mod}-{solicitud_id}",
            modalidad=norm_mod,
            solicitud_id=str(solicitud_id),
            tipo_movimiento="DEVOLUCION_CANCELACION",
            monto_base=base_digital,
            monto_ajuste=ajuste_digital,
            monto_total=total_digital,
            tasa_comision=Decimal("0.0000"),
            monto_comision=Decimal("0.00"),
            metodo_pago="tarjeta",
            referencia_pasarela=refund.referencia_reembolso_pasarela,
            detalle=serialize_refund(refund),
            now=eval_now,
        )

        if target_state == "CONFIRMADO_PASARELA":
            _mark_attempts_refunded(confirmed, eval_now)
            record_ledger_entry_once(
                self._db,
                clave_idempotencia=f"REEMBOLSO-CONFIRMADO-{norm_mod}-{solicitud_id}",
                modalidad=norm_mod,
                solicitud_id=str(solicitud_id),
                tipo_movimiento="DEVOLUCION_CONFIRMADA_PASARELA",
                monto_base=base_digital,
                monto_ajuste=ajuste_digital,
                monto_total=total_digital,
                tasa_comision=Decimal("0.0000"),
                monto_comision=Decimal("0.00"),
                metodo_pago="tarjeta",
                referencia_pasarela=refund.referencia_reembolso_pasarela,
                detalle=serialize_refund(refund),
                now=eval_now,
            )

        data = serialize_refund(refund)
        data["reintento_idempotente"] = False
        return data

    def _execute_gateway_refunds(
        self,
        modalidad: str,
        solicitud_id: str,
        attempts: list[DigitalPaymentIntent],
        *,
        attempt_number: int,
    ) -> tuple[list[str], str, str | None]:
        refund_refs: list[str] = []
        statuses: list[GatewayRefundStatus] = []
        errors: list[str] = []

        for row in attempts:
            pay_ref = (row.payment_intent_id or row.referencia_pasarela or "").strip()
            if not pay_ref:
                errors.append(f"Intento {row.concepto} sin referencia externa de pasarela.")
                statuses.append(GatewayRefundStatus.FALLIDO)
                continue
            idem_key = f"RFND-{modalidad}-{solicitud_id}-{row.concepto}-A{attempt_number}"
            try:
                res = self._gateway.create_refund(
                    CreateRefundRequest(
                        payment_reference=pay_ref,
                        amount_hnl=to_hnl_money(row.monto),
                        idempotency_key=idem_key,
                        reason="requested_by_customer",
                        currency=HNL_CURRENCY,
                        metadata={
                            "modalidad": modalidad,
                            "solicitud_id": solicitud_id,
                            "concepto": row.concepto,
                        },
                    )
                )
                if res.refund_reference:
                    refund_refs.append(res.refund_reference)
                statuses.append(res.status)
                if res.status == GatewayRefundStatus.FALLIDO:
                    errors.append(res.failure_reason or f"Devolución rechazada para cobro {row.concepto}.")
            except PaymentGatewayError as exc:
                statuses.append(GatewayRefundStatus.FALLIDO)
                errors.append(str(exc))

        if not statuses or any(s == GatewayRefundStatus.FALLIDO for s in statuses):
            return refund_refs, "PENDIENTE_CONCILIACION", " | ".join(errors) or "Fallo al solicitar devolución."
        if all(s == GatewayRefundStatus.CONFIRMADO for s in statuses):
            return refund_refs, "CONFIRMADO_PASARELA", None
        return refund_refs, "EN_PROCESO_PASARELA", None

    def get_refund_status(
        self,
        user: User,
        modalidad: str,
        solicitud_id: str,
    ) -> dict[str, Any]:
        norm_mod = normalize_modalidad(modalidad)
        refund = self._db.scalar(
            select(PaymentRefund).where(
                PaymentRefund.modalidad == norm_mod,
                PaymentRefund.solicitud_id == str(solicitud_id),
            )
        )
        if refund is None:
            raise DigitalPaymentNotFoundError("La solicitud no tiene una devolución digital registrada.")
        self._assert_access(user, refund)
        return serialize_refund(refund)

    def retry_or_reconcile_refund(
        self,
        user: User,
        modalidad: str,
        solicitud_id: str,
        *,
        moment: datetime | None = None,
    ) -> dict[str, Any]:
        """
        CA-06 / CA-07:
        - Si ya está CONFIRMADO_PASARELA: respuesta idempotente.
        - Si está EN_PROCESO_PASARELA: consulta el estado real en la pasarela.
        - Si está PENDIENTE_CONCILIACION: reintenta la devolución de los cobros pendientes sin duplicar.
        """
        norm_mod = normalize_modalidad(modalidad)
        eval_now = ensure_utc_datetime(moment or datetime.now(timezone.utc))
        assert eval_now is not None

        refund = self._db.scalar(
            select(PaymentRefund)
            .where(
                PaymentRefund.modalidad == norm_mod,
                PaymentRefund.solicitud_id == str(solicitud_id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if refund is None:
            raise DigitalPaymentNotFoundError("La solicitud no tiene una devolución digital registrada.")
        self._assert_access(user, refund)

        if refund.estado_pasarela == "CONFIRMADO_PASARELA":
            data = serialize_refund(refund)
            data["reintento_idempotente"] = True
            return data

        confirmed_attempts = self._confirmed_digital_attempts(norm_mod, str(solicitud_id))

        # 1. Si estaba en proceso con referencias reales de pasarela, concilia primero con GET
        if (
            refund.estado_pasarela == "EN_PROCESO_PASARELA"
            and refund.referencia_reembolso_pasarela
            and not refund.referencia_reembolso_pasarela.startswith("PENDIENTE-")
        ):
            refs = [r.strip() for r in refund.referencia_reembolso_pasarela.split(",") if r.strip()]
            statuses: list[GatewayRefundStatus] = []
            errors: list[str] = []
            for ref in refs:
                try:
                    res = self._gateway.get_refund_status(ref)
                    statuses.append(res.status)
                    if res.status == GatewayRefundStatus.FALLIDO:
                        errors.append(res.failure_reason or f"Devolución {ref} reportada como fallida.")
                except PaymentGatewayError as exc:
                    statuses.append(GatewayRefundStatus.FALLIDO)
                    errors.append(str(exc))

            if statuses and all(s == GatewayRefundStatus.CONFIRMADO for s in statuses):
                refund.estado_pasarela = "CONFIRMADO_PASARELA"
                refund.confirmado_en = eval_now
                refund.ultimo_error = None
                refund.actualizado_en = eval_now
                _mark_attempts_refunded(confirmed_attempts, eval_now)
                record_ledger_entry_once(
                    self._db,
                    clave_idempotencia=f"REEMBOLSO-CONFIRMADO-{norm_mod}-{solicitud_id}",
                    modalidad=norm_mod,
                    solicitud_id=str(solicitud_id),
                    tipo_movimiento="DEVOLUCION_CONFIRMADA_PASARELA",
                    monto_base=to_hnl_money(refund.monto_base_reembolsado),
                    monto_ajuste=to_hnl_money(refund.monto_ajuste_reembolsado),
                    monto_total=to_hnl_money(refund.monto_reembolso),
                    tasa_comision=Decimal("0.0000"),
                    monto_comision=Decimal("0.00"),
                    metodo_pago="tarjeta",
                    referencia_pasarela=refund.referencia_reembolso_pasarela,
                    detalle=serialize_refund(refund),
                    now=eval_now,
                )
            elif any(s == GatewayRefundStatus.FALLIDO for s in statuses):
                refund.estado_pasarela = "PENDIENTE_CONCILIACION"
                refund.ultimo_error = " | ".join(errors) or "Fallo al conciliar devolución en pasarela."
                refund.actualizado_en = eval_now
            else:
                refund.actualizado_en = eval_now

            self._db.commit()
            self._db.refresh(refund)
            data = serialize_refund(refund)
            data["reintento_idempotente"] = False
            return data

        # 2. Reintento real de devolución para estado PENDIENTE_CONCILIACION (CA-07)
        next_attempt = int(refund.intentos or 1) + 1
        refund.intentos = next_attempt
        refund_refs, target_state, last_error = self._execute_gateway_refunds(
            norm_mod,
            str(solicitud_id),
            confirmed_attempts,
            attempt_number=next_attempt,
        )
        if refund_refs:
            refund.referencia_reembolso_pasarela = ",".join(refund_refs)[:120]
        refund.estado_pasarela = target_state
        refund.ultimo_error = last_error
        refund.actualizado_en = eval_now
        if target_state == "CONFIRMADO_PASARELA":
            refund.confirmado_en = eval_now
            _mark_attempts_refunded(confirmed_attempts, eval_now)
            record_ledger_entry_once(
                self._db,
                clave_idempotencia=f"REEMBOLSO-CONFIRMADO-{norm_mod}-{solicitud_id}",
                modalidad=norm_mod,
                solicitud_id=str(solicitud_id),
                tipo_movimiento="DEVOLUCION_CONFIRMADA_PASARELA",
                monto_base=to_hnl_money(refund.monto_base_reembolsado),
                monto_ajuste=to_hnl_money(refund.monto_ajuste_reembolsado),
                monto_total=to_hnl_money(refund.monto_reembolso),
                tasa_comision=Decimal("0.0000"),
                monto_comision=Decimal("0.00"),
                metodo_pago="tarjeta",
                referencia_pasarela=refund.referencia_reembolso_pasarela,
                detalle=serialize_refund(refund),
                now=eval_now,
            )

        self._db.commit()
        self._db.refresh(refund)
        data = serialize_refund(refund)
        data["reintento_idempotente"] = False
        return data

    def _assert_access(self, user: User, refund: PaymentRefund) -> None:
        is_admin = any(role.rol == "ADMIN" for role in user.roles)
        if not is_admin and refund.cliente_uid not in {user.firebase_uid, str(user.id)}:
            raise DigitalPaymentForbiddenError("No tienes permiso para gestionar esta devolución.")


class DigitalRefundOnCancellationObserver(CancellationObserver):
    """
    Observador OCP de cancelación (CA-06 / CA-07):
    - Si existen intentos digitales HU-38 confirmados, solicita devolución íntegra vía PaymentGatewayPort.
    - Si es una solicitud heredada sin intentos HU-38, delega a RefundOnCancellationObserver (compatibilidad HU-36).
    """

    def __init__(
        self,
        gateway: PaymentGatewayPort | None = None,
        fallback_observer: RefundOnCancellationObserver | None = None,
    ) -> None:
        self._gateway = gateway
        self._fallback = fallback_observer or RefundOnCancellationObserver()

    def on_cancelled(self, db: Session, record, cancellation: RequestCancellation) -> None:
        tracking = dict(getattr(record, "seguimiento_recepcion", None) or {})
        if tracking:
            tracking["resenas_habilitadas"] = False
            record.seguimiento_recepcion = tracking

        repo = SqlAlchemyDigitalPaymentRepository(db)
        confirmed = [
            row
            for row in repo.list_by_service(cancellation.modalidad, cancellation.solicitud_id)
            if row.estado == PaymentAttemptStatus.CONFIRMADO.value
        ]
        if not confirmed:
            self._fallback.on_cancelled(db, record, cancellation)
            return

        gateway = self._gateway or get_payment_gateway()
        DigitalRefundService(db, repo, gateway).initiate_full_refund(
            cancellation.modalidad,
            cancellation.solicitud_id,
            cliente_uid=record.cliente_id,
            moment=cancellation.cancelada_en,
        )