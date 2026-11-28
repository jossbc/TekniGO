"""HU-38 ST-03: Recepción autenticada de webhooks, despachador OCP y conciliación con pasarela."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.digital_payments_service import (
    DigitalPaymentConflictError,
    DigitalPaymentForbiddenError,
    DigitalPaymentNotFoundError,
    DigitalPaymentRepository,
    serialize_payment_attempt,
)
from app.financial_settlement import record_ledger_entry_once, serialize_refund
from app.models import (
    DigitalPaymentIntent,
    DigitalPaymentWebhookEvent,
    PaymentRefund,
    User,
    ensure_utc_datetime,
)
from app.payment_attempt_state import (
    PaymentAttemptStateRegistry,
    PaymentAttemptStatus,
    PaymentRetentionStatus,
    default_payment_state_registry,
)
from app.payment_gateway_port import (
    GatewayPaymentStatus,
    GatewayRefundStatus,
    GatewayWebhookEvent,
    HNL_CURRENCY,
    PaymentGatewayPort,
    to_hnl_money,
)


class PaymentLifecycleObserver(ABC):
    """Interfaz Observer (OCP/DIP): conecta efectos de negocio (ST-04/ST-05) al confirmarse o fallar un pago."""

    @abstractmethod
    def on_payment_confirmed(
        self,
        db: Session,
        attempt: DigitalPaymentIntent,
        moment: datetime,
    ) -> None:
        """Se invoca dentro de la misma transacción cuando un intento pasa por primera vez a CONFIRMADO."""

    @abstractmethod
    def on_payment_failed_or_abandoned(
        self,
        db: Session,
        attempt: DigitalPaymentIntent,
        moment: datetime,
    ) -> None:
        """Se invoca cuando un intento pasa a RECHAZADO, ABANDONADO o EXPIRADO."""


class WebhookEventRepository(ABC):
    """Puerto Repository (DIP) para deduplicación persistente de webhooks (CA-04)."""

    @abstractmethod
    def get_by_provider_event_id(
        self,
        provider: str,
        external_event_id: str,
    ) -> DigitalPaymentWebhookEvent | None: ...

    @abstractmethod
    def record_once(
        self,
        *,
        provider: str,
        external_event_id: str,
        event_type: str,
        external_reference: str | None,
        payment_intent_id: str | None,
        payload_summary: dict[str, Any],
        now: datetime,
    ) -> tuple[DigitalPaymentWebhookEvent, bool]:
        """Inserta el evento de forma atómica; devuelve (fila, ya_existia)."""


class SqlAlchemyWebhookEventRepository(WebhookEventRepository):
    """Implementación SQLAlchemy de persistencia idempotente de webhooks."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_provider_event_id(
        self,
        provider: str,
        external_event_id: str,
    ) -> DigitalPaymentWebhookEvent | None:
        return self._db.scalar(
            select(DigitalPaymentWebhookEvent).where(
                DigitalPaymentWebhookEvent.proveedor == provider,
                DigitalPaymentWebhookEvent.evento_externo_id == external_event_id,
            )
        )

    def record_once(
        self,
        *,
        provider: str,
        external_event_id: str,
        event_type: str,
        external_reference: str | None,
        payment_intent_id: str | None,
        payload_summary: dict[str, Any],
        now: datetime,
    ) -> tuple[DigitalPaymentWebhookEvent, bool]:
        existing = self.get_by_provider_event_id(provider, external_event_id)
        if existing is not None:
            return existing, True

        row = DigitalPaymentWebhookEvent(
            id=str(uuid.uuid4()),
            proveedor=provider,
            evento_externo_id=external_event_id,
            tipo_evento=event_type[:80],
            referencia_pasarela=external_reference,
            payment_intent_id=payment_intent_id,
            estado_procesamiento="PROCESADO",
            payload_resumen=dict(payload_summary),
            recibido_en=now,
        )
        try:
            with self._db.begin_nested():
                self._db.add(row)
                self._db.flush()
            return row, False
        except IntegrityError:
            duplicate = self.get_by_provider_event_id(provider, external_event_id)
            assert duplicate is not None
            return duplicate, True


def _map_gateway_status(status: GatewayPaymentStatus) -> PaymentAttemptStatus:
    return PaymentAttemptStatus(status.value)


def _apply_payment_status_update(
    db: Session,
    attempt: DigitalPaymentIntent,
    *,
    target_status: PaymentAttemptStatus,
    state_registry: PaymentAttemptStateRegistry,
    observers: Sequence[PaymentLifecycleObserver],
    external_reference: str | None = None,
    payment_intent_id: str | None = None,
    reported_amount: Decimal | None = None,
    reported_currency: str | None = None,
    failure_reason: str | None = None,
    moment: datetime,
) -> tuple[bool, bool]:
    """
    Aplica la máquina de estados al intento de pago y registra retención lógica si se confirma.
    Retorna (cambio_aplicado, es_reintento_idempotente).
    """
    if external_reference and not attempt.referencia_pasarela:
        attempt.referencia_pasarela = external_reference
    if payment_intent_id:
        attempt.payment_intent_id = payment_intent_id

    if target_status == PaymentAttemptStatus.CONFIRMADO:
        if reported_currency and reported_currency.strip().upper() != HNL_CURRENCY:
            raise DigitalPaymentConflictError("La moneda confirmada por la pasarela no coincide con HNL.")
        if reported_amount is not None and to_hnl_money(reported_amount) != to_hnl_money(attempt.monto):
            raise DigitalPaymentConflictError(
                "El monto confirmado por la pasarela no coincide con el monto acordado del intento."
            )

    current_state = state_registry.resolve(attempt.estado)
    current_retention = PaymentRetentionStatus(attempt.estado_retencion)
    transition = current_state.transition_to(target_status, current_retention=current_retention)

    if not transition.allowed:
        return False, True

    if transition.idempotent:
        attempt.actualizado_en = moment
        return False, True

    attempt.estado = transition.target_status.value
    attempt.estado_retencion = transition.target_retention.value
    attempt.actualizado_en = moment

    if transition.target_status == PaymentAttemptStatus.CONFIRMADO:
        attempt.confirmado_en = attempt.confirmado_en or moment
        attempt.motivo_resultado = None
        monto = to_hnl_money(attempt.monto)
        is_base = attempt.concepto == "BASE"
        record_ledger_entry_once(
            db,
            clave_idempotencia=f"COBRO-DIGITAL-{attempt.modalidad}-{attempt.solicitud_id}-{attempt.concepto}",
            modalidad=attempt.modalidad,
            solicitud_id=attempt.solicitud_id,
            tipo_movimiento="COBRO_DIGITAL_CONFIRMADO",
            monto_base=monto if is_base else Decimal("0.00"),
            monto_ajuste=Decimal("0.00") if is_base else monto,
            monto_total=monto,
            tasa_comision=Decimal("0.0000"),
            monto_comision=Decimal("0.00"),
            metodo_pago="tarjeta",
            referencia_pasarela=attempt.payment_intent_id or attempt.referencia_pasarela,
            detalle={
                "intento_id": attempt.id,
                "concepto": attempt.concepto,
                "referencia_interna": attempt.referencia_interna,
                "referencia_pasarela": attempt.referencia_pasarela,
                "payment_intent_id": attempt.payment_intent_id,
                "proveedor": attempt.proveedor,
                "entorno": attempt.entorno,
                "retencion_logica": True,
                "disponible_billetera": False,
                "cliente_uid": attempt.cliente_uid,
                "tecnico_uid": attempt.tecnico_uid,
            },
            now=moment,
        )
        for observer in observers:
            observer.on_payment_confirmed(db, attempt, moment)
        return True, False

    if transition.target_status in {
        PaymentAttemptStatus.RECHAZADO,
        PaymentAttemptStatus.ABANDONADO,
        PaymentAttemptStatus.EXPIRADO,
    }:
        attempt.motivo_resultado = (
            (failure_reason or "").strip()
            or "El pago no fue completado en la pasarela. Puedes intentarlo nuevamente."
        )
        for observer in observers:
            observer.on_payment_failed_or_abandoned(db, attempt, moment)
        return True, False

    return True, False


class WebhookEventHandler(ABC):
    """Interfaz OCP para manejar una categoría específica de eventos autenticados de pasarela."""

    @abstractmethod
    def can_handle(self, event: GatewayWebhookEvent) -> bool:
        """Indica si este handler procesa el evento normalizado."""

    @abstractmethod
    def handle(
        self,
        db: Session,
        event: GatewayWebhookEvent,
        *,
        payment_repo: DigitalPaymentRepository,
        state_registry: PaymentAttemptStateRegistry,
        observers: Sequence[PaymentLifecycleObserver],
        moment: datetime,
    ) -> dict[str, Any]:
        """Procesa el evento de forma idempotente dentro de la transacción actual."""


class PaymentStatusWebhookHandler(WebhookEventHandler):
    """Procesa eventos de confirmación, rechazo o abandono de intentos de pago (CA-01, CA-02, CA-03)."""

    def can_handle(self, event: GatewayWebhookEvent) -> bool:
        return event.payment_status is not None

    def handle(
        self,
        db: Session,
        event: GatewayWebhookEvent,
        *,
        payment_repo: DigitalPaymentRepository,
        state_registry: PaymentAttemptStateRegistry,
        observers: Sequence[PaymentLifecycleObserver],
        moment: datetime,
    ) -> dict[str, Any]:
        attempt = payment_repo.get_by_gateway_reference(
            external_reference=event.external_reference,
            payment_intent_id=event.payment_intent_id,
            lock=True,
        )
        if attempt is None and event.metadata.get("referencia_interna"):
            attempt = db.scalar(
                select(DigitalPaymentIntent)
                .where(DigitalPaymentIntent.referencia_interna == event.metadata["referencia_interna"])
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if attempt is None:
            return {"aplicado": False, "motivo": "Intento de pago no encontrado para la referencia del evento."}

        assert event.payment_status is not None
        changed, idempotent = _apply_payment_status_update(
            db,
            attempt,
            target_status=_map_gateway_status(event.payment_status),
            state_registry=state_registry,
            observers=observers,
            external_reference=event.external_reference,
            payment_intent_id=event.payment_intent_id,
            reported_amount=event.amount_hnl,
            reported_currency=event.currency,
            failure_reason=event.failure_reason,
            moment=moment,
        )
        return {
            "aplicado": changed,
            "reintento_idempotente": idempotent,
            "intento": serialize_payment_attempt(attempt),
        }


class RefundStatusWebhookHandler(WebhookEventHandler):
    """Procesa confirmaciones y fallos de devolución desde la pasarela (CA-06, CA-07)."""

    def can_handle(self, event: GatewayWebhookEvent) -> bool:
        return event.refund_status is not None

    def handle(
        self,
        db: Session,
        event: GatewayWebhookEvent,
        *,
        payment_repo: DigitalPaymentRepository,
        state_registry: PaymentAttemptStateRegistry,
        observers: Sequence[PaymentLifecycleObserver],
        moment: datetime,
    ) -> dict[str, Any]:
        predicates = []
        if event.refund_reference:
            predicates.append(PaymentRefund.referencia_reembolso_pasarela == event.refund_reference)
        if event.payment_intent_id:
            predicates.append(PaymentRefund.referencia_pasarela_origen.contains(event.payment_intent_id))
        if not predicates:
            return {"aplicado": False, "motivo": "Evento de reembolso sin referencia asociada."}

        refund = db.scalar(
            select(PaymentRefund).where(or_(*predicates)).with_for_update().execution_options(populate_existing=True)
        )
        if refund is None:
            return {"aplicado": False, "motivo": "Reembolso local no encontrado para la referencia externa."}

        if event.refund_status == GatewayRefundStatus.CONFIRMADO:
            if refund.estado_pasarela == "CONFIRMADO_PASARELA":
                return {"aplicado": False, "reintento_idempotente": True, "reembolso": serialize_refund(refund)}
            if event.refund_reference:
                refund.referencia_reembolso_pasarela = event.refund_reference
            refund.estado_pasarela = "CONFIRMADO_PASARELA"
            refund.confirmado_en = moment
            refund.ultimo_error = None
            refund.actualizado_en = moment

            # Marca como REEMBOLSADO cada intento digital confirmado de esa solicitud
            attempts = payment_repo.list_by_service(refund.modalidad, refund.solicitud_id)
            for row in attempts:
                if row.estado == PaymentAttemptStatus.CONFIRMADO.value:
                    row.estado = PaymentAttemptStatus.REEMBOLSADO.value
                    row.estado_retencion = PaymentRetentionStatus.REEMBOLSADO.value
                    row.reembolsado_en = moment
                    row.actualizado_en = moment

            record_ledger_entry_once(
                db,
                clave_idempotencia=f"REEMBOLSO-CONFIRMADO-{refund.modalidad}-{refund.solicitud_id}",
                modalidad=refund.modalidad,
                solicitud_id=refund.solicitud_id,
                tipo_movimiento="DEVOLUCION_CONFIRMADA_PASARELA",
                monto_base=to_hnl_money(refund.monto_base_reembolsado),
                monto_ajuste=to_hnl_money(refund.monto_ajuste_reembolsado),
                monto_total=to_hnl_money(refund.monto_reembolso),
                tasa_comision=Decimal("0.0000"),
                monto_comision=Decimal("0.00"),
                metodo_pago="tarjeta",
                referencia_pasarela=refund.referencia_reembolso_pasarela,
                detalle=serialize_refund(refund),
                now=moment,
            )
            return {"aplicado": True, "reintento_idempotente": False, "reembolso": serialize_refund(refund)}

        if event.refund_status == GatewayRefundStatus.FALLIDO:
            # CA-07: queda pendiente de conciliación y reintento sin declarar el dinero devuelto
            if refund.estado_pasarela != "CONFIRMADO_PASARELA":
                refund.estado_pasarela = "PENDIENTE_CONCILIACION"
                refund.ultimo_error = event.failure_reason or "La pasarela reportó un fallo en la devolución."
                refund.actualizado_en = moment
                return {"aplicado": True, "reintento_idempotente": False, "reembolso": serialize_refund(refund)}
            return {"aplicado": False, "reintento_idempotente": True, "reembolso": serialize_refund(refund)}

        return {"aplicado": False, "reintento_idempotente": True, "reembolso": serialize_refund(refund)}


class WebhookEventDispatcher:
    """Despachador OCP por interfaz: delega al primer WebhookEventHandler registrado que aplique."""

    def __init__(self, handlers: Sequence[WebhookEventHandler] = ()) -> None:
        self._handlers: list[WebhookEventHandler] = list(handlers)

    def register(self, handler: WebhookEventHandler) -> None:
        self._handlers.append(handler)

    def dispatch(
        self,
        db: Session,
        event: GatewayWebhookEvent,
        *,
        payment_repo: DigitalPaymentRepository,
        state_registry: PaymentAttemptStateRegistry,
        observers: Sequence[PaymentLifecycleObserver],
        moment: datetime,
    ) -> dict[str, Any]:
        for handler in self._handlers:
            if handler.can_handle(event):
                return handler.handle(
                    db,
                    event,
                    payment_repo=payment_repo,
                    state_registry=state_registry,
                    observers=observers,
                    moment=moment,
                )
        return {"aplicado": False, "motivo": f"Evento '{event.event_type}' registrado sin acción requerida."}


def build_default_webhook_dispatcher() -> WebhookEventDispatcher:
    return WebhookEventDispatcher(
        handlers=(
            PaymentStatusWebhookHandler(),
            RefundStatusWebhookHandler(),
        )
    )


class PaymentReconciliationService:
    """Coordina la recepción autenticada de webhooks y la conciliación directa con la pasarela (ST-03)."""

    def __init__(
        self,
        db: Session,
        payment_repo: DigitalPaymentRepository,
        webhook_repo: WebhookEventRepository,
        gateway: PaymentGatewayPort,
        *,
        dispatcher: WebhookEventDispatcher | None = None,
        state_registry: PaymentAttemptStateRegistry | None = None,
        observers: Sequence[PaymentLifecycleObserver] = (),
    ) -> None:
        self._db = db
        self._payment_repo = payment_repo
        self._webhook_repo = webhook_repo
        self._gateway = gateway
        self._dispatcher = dispatcher or build_default_webhook_dispatcher()
        self._states = state_registry or default_payment_state_registry
        self._observers = tuple(observers)

    def process_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        # 1. Autenticación estricta (CA-04): lanza PaymentGatewayAuthenticationError -> HTTP 401
        event = self._gateway.verify_and_parse_webhook(raw_body, headers)
        moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
        assert moment is not None

        # 2. Deduplicación persistente por (proveedor, evento_externo_id) (CA-04)
        _, already_processed = self._webhook_repo.record_once(
            provider=event.provider,
            external_event_id=event.event_id,
            event_type=event.event_type,
            external_reference=event.external_reference,
            payment_intent_id=event.payment_intent_id,
            payload_summary={
                "payment_status": event.payment_status.value if event.payment_status else None,
                "refund_status": event.refund_status.value if event.refund_status else None,
                "amount_hnl": str(event.amount_hnl) if event.amount_hnl is not None else None,
                "currency": event.currency,
            },
            now=moment,
        )
        if already_processed:
            self._db.commit()
            return {
                "procesado": False,
                "reintento_idempotente": True,
                "evento_id": event.event_id,
                "tipo_evento": event.event_type,
            }

        outcome = self._dispatcher.dispatch(
            self._db,
            event,
            payment_repo=self._payment_repo,
            state_registry=self._states,
            observers=self._observers,
            moment=moment,
        )
        self._db.commit()
        return {
            "procesado": True,
            "reintento_idempotente": False,
            "evento_id": event.event_id,
            "tipo_evento": event.event_type,
            "resultado": outcome,
        }

    def reconcile_attempt(
        self,
        user: User,
        attempt_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Consulta autoritativamente a la pasarela y sincroniza el estado local del intento."""
        attempt = self._payment_repo.get_by_id((attempt_id or "").strip(), lock=True)
        if attempt is None:
            raise DigitalPaymentNotFoundError("Intento de pago digital no encontrado.")
        self._assert_viewer_allowed(user, attempt)
        return self._reconcile_locked_attempt(attempt, now=now)

    def reconcile_by_gateway_reference(
        self,
        user: User,
        external_reference: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Concilia un intento usando la referencia externa devuelta al regresar del Checkout (ej. cs_test_...)."""
        clean_ref = (external_reference or "").strip()
        attempt = self._payment_repo.get_by_gateway_reference(
            external_reference=clean_ref,
            payment_intent_id=clean_ref,
            lock=True,
        )
        if attempt is None:
            raise DigitalPaymentNotFoundError("No se encontró un intento asociado a esa referencia de pasarela.")
        self._assert_viewer_allowed(user, attempt)
        return self._reconcile_locked_attempt(attempt, now=now)

    def _assert_viewer_allowed(self, user: User, attempt: DigitalPaymentIntent) -> None:
        is_admin = any(role.rol == "ADMIN" for role in user.roles)
        identities = {user.firebase_uid, str(user.id)}
        if not is_admin and attempt.cliente_uid not in identities and attempt.tecnico_uid not in identities:
            raise DigitalPaymentForbiddenError("No tienes permiso para conciliar este intento de pago.")

    def _reconcile_locked_attempt(
        self,
        attempt: DigitalPaymentIntent,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ref = attempt.referencia_pasarela or attempt.payment_intent_id
        if not ref:
            raise DigitalPaymentConflictError("El intento aún no tiene una referencia externa para conciliar.")

        gateway_status = self._gateway.get_payment_status(ref)
        moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
        assert moment is not None

        changed, idempotent = _apply_payment_status_update(
            self._db,
            attempt,
            target_status=_map_gateway_status(gateway_status.status),
            state_registry=self._states,
            observers=self._observers,
            external_reference=gateway_status.external_reference,
            payment_intent_id=gateway_status.payment_intent_id,
            reported_amount=gateway_status.amount_hnl,
            reported_currency=gateway_status.currency,
            failure_reason=gateway_status.failure_reason,
            moment=moment,
        )
        self._db.commit()
        self._db.refresh(attempt)
        return {
            "conciliado": True,
            "estado_actualizado": changed,
            "reintento_idempotente": idempotent,
            "intento": serialize_payment_attempt(attempt),
            "entorno_pasarela": self._gateway.runtime_info().to_dict(),
        }