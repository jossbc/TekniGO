"""HU-38 ST-02: Máquina de estados de intentos de pago digital (State Pattern + OCP por interfaces)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class PaymentConcept(str, Enum):
    """Distingue el cobro inicial del servicio frente al cobro complementario por ajuste (CA-05 / HU-39)."""

    BASE = "BASE"
    AJUSTE = "AJUSTE"


class PaymentAttemptStatus(str, Enum):
    """Estados persistidos de un intento de pago digital."""

    PENDIENTE = "PENDIENTE"
    CONFIRMADO = "CONFIRMADO"
    RECHAZADO = "RECHAZADO"
    ABANDONADO = "ABANDONADO"
    EXPIRADO = "EXPIRADO"
    REEMBOLSADO = "REEMBOLSADO"


class PaymentRetentionStatus(str, Enum):
    """Estado de retención lógica de fondos digitales hasta el cierre del servicio (CA-03)."""

    NO_APLICA = "NO_APLICA"
    RETENIDO = "RETENIDO"
    LIBERADO = "LIBERADO"
    REEMBOLSADO = "REEMBOLSADO"


@dataclass(frozen=True)
class PaymentStateTransition:
    """Resultado inmutable de evaluar una transición sobre un intento de pago."""

    allowed: bool
    idempotent: bool
    target_status: PaymentAttemptStatus
    target_retention: PaymentRetentionStatus
    reason: str = ""


class PaymentAttemptState(ABC):
    """Interfaz State Pattern (OCP): encapsula transiciones válidas y política de reintento por estado."""

    @property
    @abstractmethod
    def status(self) -> PaymentAttemptStatus:
        """Estado que representa esta clase."""

    @property
    @abstractmethod
    def allows_new_retry(self) -> bool:
        """Indica si el cliente puede iniciar un nuevo intento recuperable (CA-02)."""

    @property
    @abstractmethod
    def counts_as_confirmed_charge(self) -> bool:
        """Indica si este estado representa un cobro digital confirmado y bloquea cobros duplicados."""

    @abstractmethod
    def transition_to(
        self,
        target: PaymentAttemptStatus,
        *,
        current_retention: PaymentRetentionStatus,
    ) -> PaymentStateTransition:
        """Evalúa la transición hacia `target` devolviendo si es válida o idempotente."""


class PendingPaymentState(PaymentAttemptState):
    @property
    def status(self) -> PaymentAttemptStatus:
        return PaymentAttemptStatus.PENDIENTE

    @property
    def allows_new_retry(self) -> bool:
        return False

    @property
    def counts_as_confirmed_charge(self) -> bool:
        return False

    def transition_to(
        self,
        target: PaymentAttemptStatus,
        *,
        current_retention: PaymentRetentionStatus,
    ) -> PaymentStateTransition:
        if target == PaymentAttemptStatus.PENDIENTE:
            return PaymentStateTransition(True, True, target, PaymentRetentionStatus.NO_APLICA)
        if target == PaymentAttemptStatus.CONFIRMADO:
            return PaymentStateTransition(True, False, target, PaymentRetentionStatus.RETENIDO)
        if target in {
            PaymentAttemptStatus.RECHAZADO,
            PaymentAttemptStatus.ABANDONADO,
            PaymentAttemptStatus.EXPIRADO,
        }:
            return PaymentStateTransition(True, False, target, PaymentRetentionStatus.NO_APLICA)
        return PaymentStateTransition(
            False, False, target, current_retention, f"No se puede pasar de PENDIENTE a {target.value}."
        )


class ConfirmedPaymentState(PaymentAttemptState):
    @property
    def status(self) -> PaymentAttemptStatus:
        return PaymentAttemptStatus.CONFIRMADO

    @property
    def allows_new_retry(self) -> bool:
        return False

    @property
    def counts_as_confirmed_charge(self) -> bool:
        return True

    def transition_to(
        self,
        target: PaymentAttemptStatus,
        *,
        current_retention: PaymentRetentionStatus,
    ) -> PaymentStateTransition:
        if target == PaymentAttemptStatus.CONFIRMADO:
            retention = (
                current_retention
                if current_retention in {PaymentRetentionStatus.RETENIDO, PaymentRetentionStatus.LIBERADO}
                else PaymentRetentionStatus.RETENIDO
            )
            return PaymentStateTransition(True, True, target, retention)
        if target == PaymentAttemptStatus.REEMBOLSADO:
            return PaymentStateTransition(True, False, target, PaymentRetentionStatus.REEMBOLSADO)
        return PaymentStateTransition(
            False,
            False,
            target,
            current_retention,
            f"Un pago CONFIRMADO no puede retroceder a {target.value}.",
        )


class _RecoverableFailedPaymentState(PaymentAttemptState):
    """Base interna para estados fallidos/abandonados recuperables (CA-02)."""

    def __init__(self, state_status: PaymentAttemptStatus) -> None:
        self._status = state_status

    @property
    def status(self) -> PaymentAttemptStatus:
        return self._status

    @property
    def allows_new_retry(self) -> bool:
        return True

    @property
    def counts_as_confirmed_charge(self) -> bool:
        return False

    def transition_to(
        self,
        target: PaymentAttemptStatus,
        *,
        current_retention: PaymentRetentionStatus,
    ) -> PaymentStateTransition:
        if target == self._status:
            return PaymentStateTransition(True, True, target, PaymentRetentionStatus.NO_APLICA)
        # Si llega una confirmación tardía verificada de la pasarela antes de otro intento, permite conciliar.
        if target == PaymentAttemptStatus.CONFIRMADO:
            return PaymentStateTransition(True, False, target, PaymentRetentionStatus.RETENIDO)
        return PaymentStateTransition(
            False,
            False,
            target,
            PaymentRetentionStatus.NO_APLICA,
            f"Un intento {self._status.value} requiere un nuevo intento para reintentar el pago.",
        )


class RejectedPaymentState(_RecoverableFailedPaymentState):
    def __init__(self) -> None:
        super().__init__(PaymentAttemptStatus.RECHAZADO)


class AbandonedPaymentState(_RecoverableFailedPaymentState):
    def __init__(self) -> None:
        super().__init__(PaymentAttemptStatus.ABANDONADO)


class ExpiredPaymentState(_RecoverableFailedPaymentState):
    def __init__(self) -> None:
        super().__init__(PaymentAttemptStatus.EXPIRADO)


class RefundedPaymentState(PaymentAttemptState):
    @property
    def status(self) -> PaymentAttemptStatus:
        return PaymentAttemptStatus.REEMBOLSADO

    @property
    def allows_new_retry(self) -> bool:
        return False

    @property
    def counts_as_confirmed_charge(self) -> bool:
        return False

    def transition_to(
        self,
        target: PaymentAttemptStatus,
        *,
        current_retention: PaymentRetentionStatus,
    ) -> PaymentStateTransition:
        if target == PaymentAttemptStatus.REEMBOLSADO:
            return PaymentStateTransition(True, True, target, PaymentRetentionStatus.REEMBOLSADO)
        return PaymentStateTransition(
            False, False, target, PaymentRetentionStatus.REEMBOLSADO, "Un pago REEMBOLSADO es inmutable."
        )


class PaymentAttemptStateRegistry:
    """Registro OCP por interfaz para resolver la clase de estado sin condicionales."""

    def __init__(self) -> None:
        self._states: dict[PaymentAttemptStatus, PaymentAttemptState] = {}

    def register(self, state: PaymentAttemptState) -> None:
        self._states[state.status] = state

    def resolve(self, raw_status: PaymentAttemptStatus | str) -> PaymentAttemptState:
        key = raw_status if isinstance(raw_status, PaymentAttemptStatus) else PaymentAttemptStatus(str(raw_status).strip().upper())
        state = self._states.get(key)
        if state is None:
            raise ValueError(f"Estado de intento de pago no registrado: {key}")
        return state


def build_default_payment_state_registry() -> PaymentAttemptStateRegistry:
    registry = PaymentAttemptStateRegistry()
    for state in (
        PendingPaymentState(),
        ConfirmedPaymentState(),
        RejectedPaymentState(),
        AbandonedPaymentState(),
        ExpiredPaymentState(),
        RefundedPaymentState(),
    ):
        registry.register(state)
    return registry


default_payment_state_registry = build_default_payment_state_registry()