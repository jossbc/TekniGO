"""HU-49 ST-02: Servicio de consulta de pendientes, contador y lectura idempotente (Repository + Strategy)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import TEGUCIGALPA_TZ_NAME, User, UserNotification, ensure_utc_datetime, format_tegucigalpa_iso
from app.notification_read_state import NotificationReadStatus
from app.notifications_consumer import serialize_notification
from app.notifications_realtime import (
    build_read_all_sync_event_frame,
    build_read_sync_event_frame,
)

DEFAULT_NOTIFICATION_LIMIT = 30
MAX_NOTIFICATION_LIMIT = 100


class NotificationInboxError(Exception):
    """Error base de dominio para operaciones de bandeja de notificaciones."""


class NotificationValidationError(NotificationInboxError):
    """Parámetros de consulta o filtro inválidos (HTTP 400)."""


class NotificationNotFoundError(NotificationInboxError):
    """Notificación inexistente o identificador inválido (HTTP 404)."""


class NotificationAccessDeniedError(NotificationInboxError):
    """Intento de leer o modificar una notificación de otra cuenta (HTTP 403)."""


class NotificationReadFilter(str, Enum):
    """Filtros soportados para la bandeja de notificaciones."""

    TODAS = "TODAS"
    PENDIENTE = "PENDIENTE"
    LEIDA = "LEIDA"


class NotificationFilterStrategy(ABC):
    """Estrategia para filtrar consultas de notificaciones por estado de lectura (OCP)."""

    @property
    @abstractmethod
    def filter_type(self) -> NotificationReadFilter:
        """Tipo de filtro que implementa la estrategia."""

    @abstractmethod
    def apply(self, query):
        """Aplica el predicado de estado de lectura a la consulta SQLAlchemy."""


class AllNotificationsFilter(NotificationFilterStrategy):
    @property
    def filter_type(self) -> NotificationReadFilter:
        return NotificationReadFilter.TODAS

    def apply(self, query):
        return query


class PendingNotificationsFilter(NotificationFilterStrategy):
    @property
    def filter_type(self) -> NotificationReadFilter:
        return NotificationReadFilter.PENDIENTE

    def apply(self, query):
        return query.where(
            UserNotification.estado_lectura == NotificationReadStatus.PENDIENTE.value,
            UserNotification.leida.is_(False),
        )


class ReadNotificationsFilter(NotificationFilterStrategy):
    @property
    def filter_type(self) -> NotificationReadFilter:
        return NotificationReadFilter.LEIDA

    def apply(self, query):
        return query.where(
            UserNotification.estado_lectura == NotificationReadStatus.LEIDA.value,
            UserNotification.leida.is_(True),
        )


_FILTER_STRATEGIES: dict[NotificationReadFilter, NotificationFilterStrategy] = {
    NotificationReadFilter.TODAS: AllNotificationsFilter(),
    NotificationReadFilter.PENDIENTE: PendingNotificationsFilter(),
    NotificationReadFilter.LEIDA: ReadNotificationsFilter(),
}


def resolve_notification_filter_strategy(
    *,
    estado: str | None = None,
    solo_no_leidas: bool = False,
) -> NotificationFilterStrategy:
    """Resuelve la estrategia de filtrado validando combinaciones de parámetros."""
    cleaned = estado.strip().upper() if isinstance(estado, str) and estado.strip() else None
    if cleaned is None:
        selected = NotificationReadFilter.PENDIENTE if solo_no_leidas else NotificationReadFilter.TODAS
        return _FILTER_STRATEGIES[selected]

    try:
        selected = NotificationReadFilter(cleaned)
    except ValueError:
        allowed = ", ".join(item.value for item in NotificationReadFilter)
        raise NotificationValidationError(
            f"Parámetro 'estado' inválido. Valores permitidos: {allowed}."
        ) from None

    if solo_no_leidas and selected == NotificationReadFilter.LEIDA:
        raise NotificationValidationError(
            "No puedes combinar 'solo_no_leidas=true' con 'estado=LEIDA'."
        )
    if solo_no_leidas:
        selected = NotificationReadFilter.PENDIENTE
    return _FILTER_STRATEGIES[selected]


def parse_iso_datetime(raw: str | None, *, field_name: str = "since") -> datetime | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        raise NotificationValidationError(
            f"Formato de fecha '{field_name}' inválido (usa ISO-8601)."
        ) from None
    return ensure_utc_datetime(parsed)


def parse_notification_uuid(raw_id: str | None) -> uuid.UUID:
    cleaned = (raw_id or "").strip()
    if not cleaned:
        raise NotificationNotFoundError("Notificación no encontrada.")
    try:
        return uuid.UUID(cleaned)
    except ValueError:
        raise NotificationNotFoundError("Notificación no encontrada.") from None


class NotificationInboxRepository(Protocol):
    """Puerto DIP para persistencia de bandeja de notificaciones."""

    def count_pending(self, usuario_id: uuid.UUID) -> int: ...

    def list_by_recipient(
        self,
        usuario_id: uuid.UUID,
        *,
        filter_strategy: NotificationFilterStrategy,
        limit: int,
        offset: int,
        after_id: str | None = None,
        since_dt: datetime | None = None,
    ) -> tuple[list[UserNotification], int]: ...

    def get_by_id(self, notification_id: uuid.UUID, *, lock: bool = False) -> UserNotification | None: ...

    def list_pending_for_update(self, usuario_id: uuid.UUID) -> list[UserNotification]: ...

    def commit(self) -> None: ...

    def refresh(self, row: UserNotification) -> None: ...


class SqlAlchemyNotificationInboxRepository:
    """Implementación SQLAlchemy del repositorio de bandeja."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def count_pending(self, usuario_id: uuid.UUID) -> int:
        total = int(
            self._db.scalar(
                select(func.count())
                .select_from(UserNotification)
                .where(
                    UserNotification.destinatario_id == usuario_id,
                    UserNotification.estado_lectura == NotificationReadStatus.PENDIENTE.value,
                    UserNotification.leida.is_(False),
                )
            )
            or 0
        )
        return max(0, total)

    def _apply_recovery_cursor(
        self,
        query,
        *,
        usuario_id: uuid.UUID,
        after_id: str | None,
        since_dt: datetime | None,
    ):
        if after_id and after_id.strip():
            try:
                ref_uuid = uuid.UUID(after_id.strip())
            except ValueError:
                raise NotificationValidationError(
                    "Identificador de cursor 'after_id' inválido."
                ) from None
            ref_row = self._db.scalar(
                select(UserNotification).where(
                    UserNotification.id == ref_uuid,
                    UserNotification.destinatario_id == usuario_id,
                )
            )
            if ref_row is not None:
                ref_dt = ensure_utc_datetime(ref_row.creada_en)
                assert ref_dt is not None
                return query.where(
                    or_(
                        UserNotification.creada_en > ref_dt,
                        (UserNotification.creada_en == ref_dt) & (UserNotification.id > ref_row.id),
                    )
                )
        if since_dt is not None:
            return query.where(UserNotification.creada_en > since_dt)
        return query

    def list_by_recipient(
        self,
        usuario_id: uuid.UUID,
        *,
        filter_strategy: NotificationFilterStrategy,
        limit: int,
        offset: int,
        after_id: str | None = None,
        since_dt: datetime | None = None,
    ) -> tuple[list[UserNotification], int]:
        has_cursor = bool((after_id and after_id.strip()) or since_dt is not None)
        base_query = select(UserNotification).where(UserNotification.destinatario_id == usuario_id)
        base_query = filter_strategy.apply(base_query)
        filtered_query = self._apply_recovery_cursor(
            base_query,
            usuario_id=usuario_id,
            after_id=after_id,
            since_dt=since_dt,
        )
        total = int(self._db.scalar(select(func.count()).select_from(filtered_query.subquery())) or 0)
        if has_cursor:
            ordered = filtered_query.order_by(UserNotification.creada_en.asc(), UserNotification.id.asc())
        else:
            ordered = filtered_query.order_by(UserNotification.creada_en.desc(), UserNotification.id.desc())
        rows = list(self._db.scalars(ordered.offset(offset).limit(limit)).all())
        return rows, max(0, total)

    def get_by_id(self, notification_id: uuid.UUID, *, lock: bool = False) -> UserNotification | None:
        query = select(UserNotification).where(UserNotification.id == notification_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def list_pending_for_update(self, usuario_id: uuid.UUID) -> list[UserNotification]:
        query = (
            select(UserNotification)
            .where(
                UserNotification.destinatario_id == usuario_id,
                UserNotification.estado_lectura == NotificationReadStatus.PENDIENTE.value,
                UserNotification.leida.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(self._db.scalars(query).all())

    def commit(self) -> None:
        self._db.commit()

    def refresh(self, row: UserNotification) -> None:
        self._db.refresh(row)


ReadSyncPublisher = Callable[[str, dict[str, Any]], None]


class NotificationInboxService:
    """Casos de uso de consulta, conteo y lectura idempotente de notificaciones (HU-49 ST-02)."""

    def __init__(
        self,
        repository: NotificationInboxRepository,
        *,
        publish_read_sync: ReadSyncPublisher | None = None,
    ) -> None:
        self._repo = repository
        self._publish_read_sync = publish_read_sync

    def _load_owned(self, notification_id: str, user: User, *, lock: bool = False) -> UserNotification:
        parsed_id = parse_notification_uuid(notification_id)
        row = self._repo.get_by_id(parsed_id, lock=lock)
        if row is None:
            raise NotificationNotFoundError("Notificación no encontrada.")
        if row.destinatario_id != user.id:
            raise NotificationAccessDeniedError("No tienes permiso para acceder a esta notificación.")
        return row

    def get_unread_count(self, user: User) -> dict[str, int]:
        pending = self._repo.count_pending(user.id)
        return {
            "no_leidas": pending,
            "pendientes": pending,
        }

    def list_notifications(
        self,
        user: User,
        *,
        limit: int = DEFAULT_NOTIFICATION_LIMIT,
        offset: int = 0,
        after_id: str | None = None,
        since: str | None = None,
        estado: str | None = None,
        solo_no_leidas: bool = False,
    ) -> dict[str, Any]:
        if limit < 1 or limit > MAX_NOTIFICATION_LIMIT:
            raise NotificationValidationError(
                f"El parámetro limit debe estar entre 1 y {MAX_NOTIFICATION_LIMIT}."
            )
        if offset < 0:
            raise NotificationValidationError("El parámetro offset no puede ser negativo.")

        since_dt = parse_iso_datetime(since, field_name="since")
        filter_strategy = resolve_notification_filter_strategy(
            estado=estado,
            solo_no_leidas=solo_no_leidas,
        )
        rows, total = self._repo.list_by_recipient(
            user.id,
            filter_strategy=filter_strategy,
            limit=limit,
            offset=offset,
            after_id=after_id,
            since_dt=since_dt,
        )
        pending = self._repo.count_pending(user.id)
        return {
            "total": total,
            "no_leidas": pending,
            "pendientes": pending,
            "filtro_estado": filter_strategy.filter_type.value,
            "offset": offset,
            "limit": limit,
            "zona_horaria": TEGUCIGALPA_TZ_NAME,
            "notificaciones": [serialize_notification(row) for row in rows],
        }

    def get_notification_detail(self, user: User, notification_id: str) -> dict[str, Any]:
        row = self._load_owned(notification_id, user, lock=False)
        return serialize_notification(row)

    def mark_as_read(
        self,
        user: User,
        notification_id: str,
        *,
        moment: datetime | None = None,
    ) -> dict[str, Any]:
        row = self._load_owned(notification_id, user, lock=True)
        changed = row.marcar_como_leida(moment)
        if changed:
            self._repo.commit()
            self._repo.refresh(row)

        pending = self._repo.count_pending(user.id)
        serialized = serialize_notification(row)

        if changed and self._publish_read_sync is not None:
            self._publish_read_sync(
                str(user.id),
                build_read_sync_event_frame(serialized, pending),
            )

        return {
            "notificacion": serialized,
            "no_leidas": pending,
            "pendientes": pending,
            "actualizada": changed,
            "ya_leida": not changed,
        }

    def mark_all_as_read(self, user: User, *, moment: datetime | None = None) -> dict[str, Any]:
        pending_rows = self._repo.list_pending_for_update(user.id)
        now = ensure_utc_datetime(moment) or datetime.now(timezone.utc)
        updated_count = 0
        for row in pending_rows:
            if row.marcar_como_leida(now):
                updated_count += 1

        if updated_count > 0:
            self._repo.commit()

        pending_after = self._repo.count_pending(user.id)
        if updated_count > 0 and self._publish_read_sync is not None:
            self._publish_read_sync(
                str(user.id),
                build_read_all_sync_event_frame(
                    pending_after,
                    read_at=now.isoformat(),
                    read_at_local=format_tegucigalpa_iso(now),
                ),
            )
            
        return {
            "actualizadas": updated_count,
            "no_leidas": pending_after,
            "pendientes": pending_after,
        }