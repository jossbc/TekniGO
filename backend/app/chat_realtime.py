"""HU-48 ST-02: gestor de conexiones WebSocket en vivo por conversación y cierre inmediato por estado."""
import asyncio
from collections import defaultdict
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

WS_CLOSE_VALIDATION = 4000
WS_CLOSE_UNAUTHORIZED = 4001
WS_CLOSE_FORBIDDEN = 4003
WS_CLOSE_NOT_FOUND = 4004
WS_CLOSE_STATE_DISABLED = 4009

ConversationKey = tuple[str, str]


class ChatConnectionManager:
    """Administra sesiones WebSocket activas por conversación (modalidad, solicitud_id)."""

    def __init__(self) -> None:
        self._connections: dict[ConversationKey, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def key(modalidad: str, solicitud_id: str) -> ConversationKey:
        return (modalidad.strip().upper(), solicitud_id.strip())

    def active_count(self, modalidad: str, solicitud_id: str) -> int:
        return len(self._connections.get(self.key(modalidad, solicitud_id), ()))

    async def register(self, modalidad: str, solicitud_id: str, websocket: WebSocket) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._connections[self.key(modalidad, solicitud_id)].add(websocket)

    def disconnect(self, modalidad: str, solicitud_id: str, websocket: WebSocket) -> None:
        conv_key = self.key(modalidad, solicitud_id)
        sockets = self._connections.get(conv_key)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(conv_key, None)

    async def broadcast_message(
        self,
        modalidad: str,
        solicitud_id: str,
        message_payload: dict[str, Any],
    ) -> int:
        """Envía un mensaje ya persistido a todas las conexiones activas de la conversación (CA-04)."""
        conv_key = self.key(modalidad, solicitud_id)
        sockets = list(self._connections.get(conv_key, ()))
        if not sockets:
            return 0

        event = {
            "type": "message",
            "modalidad": conv_key[0],
            "solicitud_id": conv_key[1],
            "mensaje": message_payload,
        }
        delivered = 0
        stale: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
                delivered += 1
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(conv_key[0], conv_key[1], ws)
        return delivered

    async def close_conversation(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        estado: str,
        motivo: str | None = None,
    ) -> int:
        """Deshabilita y cierra inmediatamente todas las conexiones abiertas de una solicitud cerrada (CA-05)."""
        conv_key = self.key(modalidad, solicitud_id)
        sockets = list(self._connections.pop(conv_key, ()))
        if not sockets:
            return 0

        reason = motivo or (
            f"El chat operativo fue deshabilitado porque la solicitud pasó a {estado}."
        )
        event = {
            "type": "chat_closed",
            "modalidad": conv_key[0],
            "solicitud_id": conv_key[1],
            "estado": estado,
            "motivo": reason,
        }
        closed = 0
        for ws in sockets:
            try:
                await ws.send_json(event)
            except Exception:
                pass
            try:
                await ws.close(code=WS_CLOSE_STATE_DISABLED, reason=reason[:120])
            except Exception:
                pass
            closed += 1
        return closed

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

        # Sin loop activo (p. ej. pruebas unitarias síncronas sin sockets): cerrar corrutina limpia
        coro.close()

    def broadcast_message_sync(
        self,
        modalidad: str,
        solicitud_id: str,
        message_payload: dict[str, Any],
    ) -> None:
        if self.active_count(modalidad, solicitud_id) == 0:
            return
        self._schedule_coro(
            self.broadcast_message(modalidad, solicitud_id, message_payload)
        )

    def notify_conversation_closed_sync(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        estado: str,
        motivo: str | None = None,
    ) -> None:
        if self.active_count(modalidad, solicitud_id) == 0:
            return
        self._schedule_coro(
            self.close_conversation(
                modalidad,
                solicitud_id,
                estado=estado,
                motivo=motivo,
            )
        )

    def reset(self) -> None:
        self._connections.clear()
        self._loop = None


chat_manager = ChatConnectionManager()


def notify_conversation_closed_sync(
    modalidad: str,
    solicitud_id: str,
    *,
    estado: str,
    motivo: str | None = None,
) -> None:
    """Hook seguro para cerrar canales WebSocket cuando una solicitud pasa a COMPLETADA o CANCELADA."""
    try:
        chat_manager.notify_conversation_closed_sync(
            modalidad,
            solicitud_id,
            estado=estado,
            motivo=motivo,
        )
    except Exception as exc:
        logger.warning("No se pudo notificar el cierre del chat en vivo: %s", exc)
