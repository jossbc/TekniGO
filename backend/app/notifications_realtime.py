"""HU-47 ST-04: Gestor WebSocket en vivo por usuario autenticado (Observer / Broker DIP)."""
import asyncio
from collections import defaultdict
import logging
from typing import Any, Protocol

from fastapi import WebSocket
from firebase_admin import auth as firebase_auth

from app.security import TOKEN_CLOCK_SKEW_SECONDS

logger = logging.getLogger(__name__)

WS_CLOSE_VALIDATION = 4000
WS_CLOSE_UNAUTHORIZED = 4001
WS_CLOSE_FORBIDDEN = 4003
WS_CLOSE_NOT_FOUND = 4004


class NotificationRealtimeError(Exception):
    """Error de autenticación o validación en el canal en vivo de notificaciones."""


class NotificationUnauthorizedError(NotificationRealtimeError):
    """Token ausente, inválido, vencido o revocado."""


class NotificationForbiddenError(NotificationRealtimeError):
    """Cuenta sin registro completo en TekniGO."""


def _clamp_unread_count(count: int | None) -> int:
    if count is None:
        return 0
    return max(0, int(count))


def build_notification_event_frame(
    item: dict[str, Any],
    unread_count: int | None = None,
) -> dict[str, Any]:
    """Construye la trama WS de nuevo aviso incluyendo contador autoritativo si está disponible."""
    frame: dict[str, Any] = {
        "type": "notification",
        "notificacion": item,
    }
    if unread_count is not None:
        safe_count = _clamp_unread_count(unread_count)
        frame["no_leidas"] = safe_count
        frame["pendientes"] = safe_count
    return frame


def build_read_sync_event_frame(
    notification: dict[str, Any],
    unread_count: int,
) -> dict[str, Any]:
    """Construye la trama WS de sincronización individual de lectura entre sesiones."""
    safe_count = _clamp_unread_count(unread_count)
    return {
        "type": "read_sync",
        "notification_id": str(notification["id"]),
        "estado_lectura": notification.get("estado_lectura") or "LEIDA",
        "leida": True,
        "leida_en": notification.get("leida_en"),
        "leida_en_local": notification.get("leida_en_local"),
        "no_leidas": safe_count,
        "pendientes": safe_count,
    }


def build_read_all_sync_event_frame(
    unread_count: int,
    *,
    read_at: str | None = None,
    read_at_local: str | None = None,
) -> dict[str, Any]:
    """Construye la trama WS de sincronización masiva de lectura entre sesiones."""
    safe_count = _clamp_unread_count(unread_count)
    return {
        "type": "read_all_sync",
        "estado_lectura": "LEIDA",
        "leida_en": read_at,
        "leida_en_local": read_at_local,
        "no_leidas": safe_count,
        "pendientes": safe_count,
    }


class NotificationBroadcaster(Protocol):
    """Puerto DIP para emitir avisos en tiempo real a destinatarios conectados."""

    def publish_notifications_sync(
        self,
        notifications: list[dict[str, Any]],
        unread_counts: dict[str, int] | None = None,
    ) -> None: ...

class NotificationConnectionManager:
    """Administra conexiones WebSocket por destinatario (`usuario_id` UUID)."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def key(usuario_id: str) -> str:
        return str(usuario_id).strip()

    def active_count(self, usuario_id: str) -> int:
        return len(self._connections.get(self.key(usuario_id), ()))

    async def register(self, usuario_id: str, websocket: WebSocket) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._connections[self.key(usuario_id)].add(websocket)

    def disconnect(self, usuario_id: str, websocket: WebSocket) -> None:
        user_key = self.key(usuario_id)
        sockets = self._connections.get(user_key)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_key, None)

    async def send_event(self, usuario_id: str, event: dict[str, Any]) -> int:
        user_key = self.key(usuario_id)
        sockets = list(self._connections.get(user_key, ()))
        if not sockets:
            return 0
        delivered = 0
        stale: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
                delivered += 1
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(user_key, ws)
        return delivered

    async def broadcast_notifications(
        self,
        notifications: list[dict[str, Any]],
        unread_counts: dict[str, int] | None = None,
    ) -> int:
        delivered = 0
        counts_map = unread_counts or {}
        for item in notifications:
            recipient_id = item.get("destinatario_id")
            if not recipient_id:
                continue
            recipient_key = str(recipient_id)
            unread = counts_map.get(recipient_key)
            delivered += await self.send_event(
                recipient_key,
                build_notification_event_frame(item, unread_count=unread),
            )
        return delivered

    def _schedule_coro(self, coro) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None and running_loop.is_running():
            running_loop.create_task(coro)
            return

        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return

        coro.close()

    def publish_notifications_sync(
        self,
        notifications: list[dict[str, Any]],
        unread_counts: dict[str, int] | None = None,
    ) -> None:
        if not notifications:
            return
        targets = [
            item
            for item in notifications
            if item.get("destinatario_id") and self.active_count(str(item["destinatario_id"])) > 0
        ]
        if not targets:
            return
        self._schedule_coro(self.broadcast_notifications(targets, unread_counts=unread_counts))

    def publish_read_sync(self, usuario_id: str, event: dict[str, Any]) -> None:
        if self.active_count(usuario_id) == 0:
            return
        self._schedule_coro(self.send_event(usuario_id, event))

    def reset(self) -> None:
        self._connections.clear()
        self._loop = None


notification_manager = NotificationConnectionManager()


def verify_notification_ws_token(token: str | None) -> dict:
    """Valida token Firebase Bearer para conexiones WebSocket de notificaciones."""
    cleaned = (token or "").strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    if not cleaned:
        raise NotificationUnauthorizedError("Se requiere un token Bearer para conectar a notificaciones.")
    try:
        return firebase_auth.verify_id_token(
            cleaned,
            check_revoked=True,
            clock_skew_seconds=TOKEN_CLOCK_SKEW_SECONDS,
        )
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.ExpiredIdTokenError,
        firebase_auth.RevokedIdTokenError,
        firebase_auth.UserDisabledError,
        ValueError,
    ):
        raise NotificationUnauthorizedError("Token inválido, vencido o revocado.") from None
