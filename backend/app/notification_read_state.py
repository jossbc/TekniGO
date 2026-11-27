"""HU-49 ST-01: Estados de lectura de notificaciones (State Pattern + Value Objects)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class NotificationReadStatus(str, Enum):
    """Valores permitidos para el estado de lectura de una notificación."""

    PENDIENTE = "PENDIENTE"
    LEIDA = "LEIDA"


@dataclass(frozen=True)
class NotificationReadTransitionResult:
    """Resultado inmutable de aplicar una transición de lectura sobre una notificación."""

    estado_lectura: NotificationReadStatus
    leida: bool
    leida_en: datetime | None
    changed: bool


def _normalize_utc(moment: datetime | None) -> datetime:
    """Normaliza un instante a UTC."""
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class NotificationReadState(ABC):
    """Contrato abstracto del estado de lectura (State Pattern)."""

    @property
    @abstractmethod
    def status(self) -> NotificationReadStatus:
        """Estado actual representado."""

    @property
    @abstractmethod
    def is_read(self) -> bool:
        """Indica si el estado representa una notificación leída."""

    @abstractmethod
    def mark_as_read(self, moment: datetime | None = None) -> NotificationReadTransitionResult:
        """Ejecuta la transición hacia LEIDA de forma idempotente."""


class PendingNotificationReadState(NotificationReadState):
    """Estado de notificación pendiente por leer."""

    @property
    def status(self) -> NotificationReadStatus:
        return NotificationReadStatus.PENDIENTE

    @property
    def is_read(self) -> bool:
        return False

    def mark_as_read(self, moment: datetime | None = None) -> NotificationReadTransitionResult:
        read_at = _normalize_utc(moment)
        return NotificationReadTransitionResult(
            estado_lectura=NotificationReadStatus.LEIDA,
            leida=True,
            leida_en=read_at,
            changed=True,
        )


class ReadNotificationReadState(NotificationReadState):
    """Estado de notificación ya leída; conserva la fecha de lectura original."""

    def __init__(self, leida_en: datetime | None) -> None:
        self._leida_en = _normalize_utc(leida_en) if leida_en is not None else None

    @property
    def status(self) -> NotificationReadStatus:
        return NotificationReadStatus.LEIDA

    @property
    def is_read(self) -> bool:
        return True

    def mark_as_read(self, moment: datetime | None = None) -> NotificationReadTransitionResult:
        preserved_read_at = self._leida_en if self._leida_en is not None else _normalize_utc(moment)
        return NotificationReadTransitionResult(
            estado_lectura=NotificationReadStatus.LEIDA,
            leida=True,
            leida_en=preserved_read_at,
            changed=False,
        )


def resolve_notification_read_state(
    *,
    estado_lectura: str | None = None,
    leida: bool = False,
    leida_en: datetime | None = None,
) -> NotificationReadState:
    """Resuelve el objeto de estado correspondiente a partir de los datos persistidos."""
    normalized = (estado_lectura or "").strip().upper()
    if normalized == NotificationReadStatus.LEIDA.value or bool(leida):
        return ReadNotificationReadState(leida_en=leida_en)
    return PendingNotificationReadState()
