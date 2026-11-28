"""HU-38 ST-01: Contratos (interfaces ABC), objetos de valor y excepciones de pasarela de pago."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Mapping

CENT = Decimal("0.01")
HNL_CURRENCY = "HNL"


def to_hnl_money(value: Decimal | int | float | str) -> Decimal:
    """Política de redondeo TekniGO: lempiras (HNL), 2 decimales, ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def hnl_to_centavos(amount: Decimal | int | float | str) -> int:
    """Convierte un monto en lempiras a centavos enteros exactos sin aritmética flotante."""
    money = to_hnl_money(amount)
    return int((money * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def centavos_to_hnl(centavos: int) -> Decimal:
    """Convierte centavos enteros a Decimal en lempiras con 2 decimales."""
    return (Decimal(int(centavos)) / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)


class GatewayEnvironment(str, Enum):
    """Distingue explícitamente un sandbox real de un simulador local (CA-08)."""

    SANDBOX = "SANDBOX"
    SIMULADOR_LOCAL = "SIMULADOR_LOCAL"


class GatewayPaymentStatus(str, Enum):
    """Estados normalizados de un intento de pago digital."""

    PENDIENTE = "PENDIENTE"
    CONFIRMADO = "CONFIRMADO"
    RECHAZADO = "RECHAZADO"
    ABANDONADO = "ABANDONADO"
    EXPIRADO = "EXPIRADO"


class GatewayRefundStatus(str, Enum):
    """Estados normalizados de una devolución en pasarela."""

    EN_PROCESO = "EN_PROCESO"
    CONFIRMADO = "CONFIRMADO"
    FALLIDO = "FALLIDO"


class PaymentGatewayError(Exception):
    """Error base del puerto de pasarela de pago."""


class PaymentGatewayConfigError(PaymentGatewayError):
    """Configuración o secretos de pasarela ausentes o inválidos."""


class PaymentGatewayAuthenticationError(PaymentGatewayError):
    """Firma o autenticación de webhook/evento rechazada (CA-04)."""


class PaymentGatewayCommunicationError(PaymentGatewayError):
    """Fallo de red, tiempo de espera o respuesta inválida desde la pasarela."""


@dataclass(frozen=True)
class GatewayRuntimeInfo:
    """Metadatos visibles sobre el entorno activo de pasarela (CA-08)."""

    provider: str
    environment: GatewayEnvironment
    is_sandbox: bool
    is_local_simulator: bool
    display_label: str
    currency: str = HNL_CURRENCY

    def to_dict(self) -> dict[str, object]:
        return {
            "proveedor": self.provider,
            "entorno": self.environment.value,
            "es_sandbox": self.is_sandbox,
            "es_simulacion_local": self.is_local_simulator,
            "etiqueta": self.display_label,
            "moneda": self.currency,
        }


@dataclass(frozen=True)
class CreatePaymentRequest:
    """Orden inmutable para iniciar un intento de cobro digital en pasarela."""

    internal_reference: str
    amount_hnl: Decimal
    idempotency_key: str
    description: str
    success_url: str
    cancel_url: str
    currency: str = HNL_CURRENCY
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayPaymentResult:
    """Respuesta normalizada al crear o consultar un intento de cobro."""

    provider: str
    external_reference: str
    payment_intent_id: str | None
    checkout_url: str | None
    status: GatewayPaymentStatus
    amount_hnl: Decimal
    currency: str
    raw_status: str
    is_sandbox: bool
    is_local_simulator: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class GatewayWebhookEvent:
    """Evento autenticado y normalizado recibido desde la pasarela."""

    event_id: str
    event_type: str
    provider: str
    external_reference: str | None = None
    payment_intent_id: str | None = None
    refund_reference: str | None = None
    payment_status: GatewayPaymentStatus | None = None
    refund_status: GatewayRefundStatus | None = None
    amount_hnl: Decimal | None = None
    currency: str | None = None
    failure_reason: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CreateRefundRequest:
    """Orden inmutable para solicitar devolución íntegra de un cobro confirmado."""

    payment_reference: str
    amount_hnl: Decimal
    idempotency_key: str
    reason: str = "requested_by_customer"
    currency: str = HNL_CURRENCY
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayRefundResult:
    """Respuesta normalizada al solicitar o consultar una devolución."""

    provider: str
    refund_reference: str | None
    payment_reference: str
    status: GatewayRefundStatus
    amount_hnl: Decimal
    currency: str
    raw_status: str
    failure_reason: str | None = None


class PaymentChargePort(ABC):
    """Puerto segregado (ISP) para iniciar y conciliar intentos de cobro."""

    @abstractmethod
    def create_payment(self, request: CreatePaymentRequest) -> GatewayPaymentResult:
        """Crea un intento de cobro idempotente en la pasarela."""

    @abstractmethod
    def get_payment_status(self, external_reference: str) -> GatewayPaymentResult:
        """Consulta el estado autoritativo de un intento directamente en la pasarela."""


class PaymentWebhookVerifierPort(ABC):
    """Puerto segregado (ISP) para autenticar y parsear notificaciones de la pasarela."""

    @abstractmethod
    def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayWebhookEvent:
        """Valida la firma criptográfica y traduce el evento; lanza PaymentGatewayAuthenticationError si falla."""


class PaymentRefundPort(ABC):
    """Puerto segregado (ISP) para solicitar y conciliar devoluciones."""

    @abstractmethod
    def create_refund(self, request: CreateRefundRequest) -> GatewayRefundResult:
        """Solicita una devolución idempotente sobre un cobro confirmado."""

    @abstractmethod
    def get_refund_status(self, refund_reference: str) -> GatewayRefundResult:
        """Consulta el estado actual de una devolución en la pasarela."""


class PaymentGatewayPort(PaymentChargePort, PaymentWebhookVerifierPort, PaymentRefundPort, ABC):
    """Contrato completo de un adaptador de pasarela de pago (Adapter + DIP + OCP)."""

    @abstractmethod
    def runtime_info(self) -> GatewayRuntimeInfo:
        """Informa proveedor, modo sandbox o simulador local para transparencia en UI/auditoría."""