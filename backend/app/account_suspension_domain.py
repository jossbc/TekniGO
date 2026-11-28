"""HU-52 ST-01: Dominio de suspensiones de cuenta, causas, vigencias, patrón State y dimensiones independientes (OCP)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Any


class SuspensionType(str, Enum):
    TEMPORAL = "TEMPORAL"
    DEFINITIVA = "DEFINITIVA"


class SuspensionCause(str, Enum):
    """Causas disciplinarias/de moderación. Nunca incluye restricción financiera (CA-07)."""

    CONDUCTA = "CONDUCTA"
    REINCIDENCIA_FALTAS = "REINCIDENCIA_FALTAS"
    INCUMPLIMIENTO_REGLAS = "INCUMPLIMIENTO_REGLAS"
    FRAUDE_IDENTIDAD = "FRAUDE_IDENTIDAD"
    INCIDENCIA_GRAVE = "INCIDENCIA_GRAVE"


class SuspensionStatus(str, Enum):
    ACTIVA = "ACTIVA"
    LEVANTADA = "LEVANTADA"
    EXPIRADA = "EXPIRADA"


class AccountModerationEvent(str, Enum):
    SUSPENSION_APLICADA = "SUSPENSION_APLICADA"
    SUSPENSION_LEVANTADA = "SUSPENSION_LEVANTADA"
    SUSPENSION_EXPIRADA = "SUSPENSION_EXPIRADA"
    ALERTA_REINCIDENCIA_ATENDIDA = "ALERTA_REINCIDENCIA_ATENDIDA"
    CIERRE_CUENTA_APLICADO = "CIERRE_CUENTA_APLICADO"
    BAJA_LOGICA_DOCUMENTAL = "BAJA_LOGICA_DOCUMENTAL"


class BlockedIdentityKind(str, Enum):
    DNI = "DNI"
    TELEFONO = "TELEFONO"


class BlockedIdentityReason(str, Enum):
    SUSPENSION_ACTIVA = "SUSPENSION_ACTIVA"
    CIERRE_CUENTA = "CIERRE_CUENTA"


class OperationalDimension(str, Enum):
    """Dimensiones independientes (CA-05, CA-07 y Condición transversal)."""

    CUENTA_ACTIVA = "CUENTA_ACTIVA"
    VERIFICACION = "VERIFICACION"
    DISPONIBILIDAD = "DISPONIBILIDAD"
    SUSPENSION_CONDUCTA = "SUSPENSION_CONDUCTA"
    RESTRICCION_FINANCIERA = "RESTRICCION_FINANCIERA"


class SuspensionDomainError(Exception):
    """Error base del dominio de suspensiones y moderación de cuentas."""


class SuspensionValidationError(SuspensionDomainError):
    """Datos inválidos para crear o gestionar una suspensión (HTTP 400)."""


class SuspensionForbiddenError(SuspensionDomainError):
    """Acción reservada exclusivamente para administración (HTTP 403)."""


class SuspensionNotFoundError(SuspensionDomainError):
    """Cuenta, suspensión o alerta no encontrada (HTTP 404)."""


class SuspensionConflictError(SuspensionDomainError):
    """Transición de estado o regla de negocio incompatible (HTTP 409)."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_suspension_type(raw: str) -> SuspensionType:
    cleaned = (raw or "").strip().upper()
    try:
        return SuspensionType(cleaned)
    except ValueError as exc:
        raise SuspensionValidationError(
            "El tipo de suspensión debe ser TEMPORAL o DEFINITIVA."
        ) from exc


def normalize_suspension_cause(raw: str) -> SuspensionCause:
    cleaned = (raw or "").strip().upper()
    if cleaned in {"RESTRICCION_FINANCIERA", "DEUDA", "SALDO"}:
        raise SuspensionValidationError(
            "La restricción financiera es una dimensión distinta y no puede registrarse como suspensión por conducta."
        )
    try:
        return SuspensionCause(cleaned)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SuspensionCause)
        raise SuspensionValidationError(
            f"Causa de suspensión inválida. Valores permitidos: {allowed}."
        ) from exc


def validate_suspension_schedule(
    tipo: SuspensionType,
    iniciada_en: datetime,
    vence_en: datetime | None,
) -> datetime | None:
    """Valida vigencia obligatoria para suspensión TEMPORAL y nula para DEFINITIVA (CA-01)."""
    start_utc = _utc(iniciada_en)
    assert start_utc is not None
    end_utc = _utc(vence_en)

    if tipo is SuspensionType.TEMPORAL:
        if end_utc is None:
            raise SuspensionValidationError(
                "Una suspensión temporal requiere fecha y hora de vencimiento."
            )
        if end_utc <= start_utc:
            raise SuspensionValidationError(
                "La fecha de vencimiento debe ser posterior al inicio de la suspensión."
            )
        return end_utc

    if end_utc is not None:
        raise SuspensionValidationError(
            "Una suspensión definitiva no debe incluir fecha de vencimiento."
        )
    return None


def normalize_identity_value(kind: BlockedIdentityKind, raw_value: str | None) -> str | None:
    """Normaliza DNI o teléfono hondureño para comparaciones deterministas (CA-06 / CA-08)."""
    if not raw_value:
        return None
    text = raw_value.strip()
    if not text or text.upper().startswith("X"):
        return None
    if kind is BlockedIdentityKind.DNI:
        digits = re.sub(r"[\s-]", "", text)
        return digits if re.fullmatch(r"[0-9]{13}", digits) else None
    phone = re.sub(r"[\s()-]", "", text)
    if re.fullmatch(r"[389][0-9]{7}", phone):
        return f"+504{phone}"
    if re.fullmatch(r"\+504[389][0-9]{7}", phone):
        return phone
    return None


def hash_identity_value(kind: BlockedIdentityKind, raw_value: str | None) -> str | None:
    """Calcula hash SHA-256 estable de DNI o teléfono para bloquear reutilización sin exponer PII."""
    normalized = normalize_identity_value(kind, raw_value)
    if not normalized:
        return None
    payload = f"{kind.value}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# --- Patrón State (OCP vía interfaz ABC) para el ciclo de vida de una suspensión ---

class AccountSuspensionState(ABC):
    """Estado de una suspensión de cuenta y sus transiciones válidas."""

    @property
    @abstractmethod
    def status(self) -> SuspensionStatus: ...

    @abstractmethod
    def is_blocking(self) -> bool: ...

    @abstractmethod
    def can_lift(self, suspension_type: SuspensionType) -> bool: ...

    @abstractmethod
    def can_expire(self, suspension_type: SuspensionType) -> bool: ...


class ActiveSuspensionState(AccountSuspensionState):
    @property
    def status(self) -> SuspensionStatus:
        return SuspensionStatus.ACTIVA

    def is_blocking(self) -> bool:
        return True

    def can_lift(self, suspension_type: SuspensionType) -> bool:
        return suspension_type is SuspensionType.TEMPORAL

    def can_expire(self, suspension_type: SuspensionType) -> bool:
        return suspension_type is SuspensionType.TEMPORAL


class LiftedSuspensionState(AccountSuspensionState):
    @property
    def status(self) -> SuspensionStatus:
        return SuspensionStatus.LEVANTADA

    def is_blocking(self) -> bool:
        return False

    def can_lift(self, suspension_type: SuspensionType) -> bool:
        return False

    def can_expire(self, suspension_type: SuspensionType) -> bool:
        return False


class ExpiredSuspensionState(AccountSuspensionState):
    @property
    def status(self) -> SuspensionStatus:
        return SuspensionStatus.EXPIRADA

    def is_blocking(self) -> bool:
        return False

    def can_lift(self, suspension_type: SuspensionType) -> bool:
        return False

    def can_expire(self, suspension_type: SuspensionType) -> bool:
        return False


class SuspensionStateRegistry:
    """Registro OCP de estados de suspensión (extensible sin modificar consumidores)."""

    def __init__(self, states: tuple[AccountSuspensionState, ...] = ()) -> None:
        self._states: dict[SuspensionStatus, AccountSuspensionState] = {}
        for state in states:
            self.register(state)

    def register(self, state: AccountSuspensionState) -> None:
        self._states[state.status] = state

    def resolve(self, raw_status: str | SuspensionStatus) -> AccountSuspensionState:
        key = raw_status if isinstance(raw_status, SuspensionStatus) else SuspensionStatus(str(raw_status).upper())
        state = self._states.get(key)
        if state is None:
            raise SuspensionConflictError(f"Estado de suspensión no registrado: {key.value}")
        return state


default_suspension_state_registry = SuspensionStateRegistry(
    (
        ActiveSuspensionState(),
        LiftedSuspensionState(),
        ExpiredSuspensionState(),
    )
)


# --- Contrato OCP (Strategy por interfaz ABC) para evaluar dimensiones independientes ---

@dataclass(frozen=True)
class DimensionEvaluationResult:
    dimension: OperationalDimension
    habilitada: bool
    causa_codigo: str | None = None
    motivo: str | None = None
    es_restriccion_financiera: bool = False
    es_suspension_conducta: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "habilitada": self.habilitada,
            "causa_codigo": self.causa_codigo,
            "motivo": self.motivo,
            "es_restriccion_financiera": self.es_restriccion_financiera,
            "es_suspension_conducta": self.es_suspension_conducta,
        }


class OperationalDimensionEvaluator(ABC):
    """Contrato (OCP vía interfaz): cada regla de habilitación vive en su propio evaluador."""

    @property
    @abstractmethod
    def dimension(self) -> OperationalDimension: ...

    @abstractmethod
    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult: ...