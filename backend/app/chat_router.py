"""HU-48 ST-01 & ST-02: endpoints REST y canal WebSocket en vivo del chat operativo de contratación."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat as rules
from app.chat_realtime import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_NOT_FOUND,
    WS_CLOSE_STATE_DISABLED,
    WS_CLOSE_UNAUTHORIZED,
    WS_CLOSE_VALIDATION,
    chat_manager,
)
from app.database import get_db
from app.identity_documents import _get_user
from app.models import User
from app.security import get_current_user

router = APIRouter(tags=["Chat de Contratación (HU-48)"])
NO_STORE = {"Cache-Control": "no-store, private"}


class ChatMessageCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contenido: str
    client_message_id: str | None = None


def _raise_http_for_chat_error(exc: rules.ChatError) -> None:
    if isinstance(exc, rules.ChatValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if isinstance(exc, rules.ChatUnauthorizedError):
        raise HTTPException(status_code=401, detail=str(exc)) from None
    if isinstance(exc, rules.ChatNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if isinstance(exc, rules.ChatForbiddenError):
        raise HTTPException(status_code=403, detail=str(exc)) from None
    if isinstance(exc, rules.ChatStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    raise HTTPException(status_code=400, detail=str(exc)) from None


def _ws_close_code_for_error(exc: rules.ChatError) -> int:
    if isinstance(exc, rules.ChatUnauthorizedError):
        return WS_CLOSE_UNAUTHORIZED
    if isinstance(exc, rules.ChatForbiddenError):
        return WS_CLOSE_FORBIDDEN
    if isinstance(exc, rules.ChatNotFoundError):
        return WS_CLOSE_NOT_FOUND
    if isinstance(exc, rules.ChatStateError):
        return WS_CLOSE_STATE_DISABLED
    return WS_CLOSE_VALIDATION


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        raise rules.ChatValidationError("Formato de fecha 'after' inválido (usa ISO-8601).") from None


@router.get("/chat/solicitudes/{modalidad}/{solicitud_id}/mensajes")
def get_conversation_messages(
    modalidad: str,
    solicitud_id: str,
    response: Response,
    after: datetime | None = Query(default=None),
    limit: int = Query(default=rules.DEFAULT_MESSAGE_LIMIT, ge=1, le=rules.MAX_MESSAGE_LIMIT),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    try:
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
        messages = rules.list_conversation_messages(
            db,
            conversation=conversation,
            after=after,
            limit=limit,
        )
    except rules.ChatError as exc:
        _raise_http_for_chat_error(exc)

    return {
        "modalidad": conversation.modalidad,
        "solicitud_id": conversation.solicitud_id,
        "estado": conversation.estado,
        "rol_usuario": conversation.rol_usuario,
        "habilitado": True,
        "mensajes": [rules.serialize_chat_message(msg) for msg in messages],
    }


@router.post(
    "/chat/solicitudes/{modalidad}/{solicitud_id}/mensajes",
    status_code=status.HTTP_201_CREATED,
)
def post_conversation_message(
    modalidad: str,
    solicitud_id: str,
    payload: ChatMessageCreateInput,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    try:
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
        message, created = rules.create_chat_message(
            db,
            conversation=conversation,
            contenido=payload.contenido,
            client_message_id=payload.client_message_id,
        )
    except rules.ChatError as exc:
        _raise_http_for_chat_error(exc)

    serialized = rules.serialize_chat_message(message)
    if created:
        response.status_code = status.HTTP_201_CREATED
        chat_manager.broadcast_message_sync(
            conversation.modalidad,
            conversation.solicitud_id,
            serialized,
        )
    else:
        response.status_code = status.HTTP_200_OK

    return {
        "modalidad": conversation.modalidad,
        "solicitud_id": conversation.solicitud_id,
        "estado": conversation.estado,
        "duplicado": not created,
        "mensaje": serialized,
    }


def _extract_ws_token(websocket: WebSocket, query_token: str | None) -> str | None:
    auth_header = websocket.headers.get("authorization") if getattr(websocket, "headers", None) else None
    if auth_header:
        return auth_header
    return query_token


async def _handle_ws_send(
    websocket: WebSocket,
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    user: User,
    frame: dict[str, Any],
) -> bool:
    """Procesa un envío por WebSocket revalidando estado en BD. Devuelve False si debe cerrarse el canal."""
    client_msg_id = frame.get("client_message_id")
    db.expire_all()
    try:
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
    except rules.ChatStateError as exc:
        await websocket.send_json(
            {
                "type": "chat_closed",
                "modalidad": modalidad,
                "solicitud_id": solicitud_id,
                "motivo": str(exc),
            }
        )
        await websocket.close(code=WS_CLOSE_STATE_DISABLED, reason=str(exc)[:120])
        return False
    except rules.ChatError as exc:
        await websocket.close(code=_ws_close_code_for_error(exc), reason=str(exc)[:120])
        return False

    try:
        message, created = rules.create_chat_message(
            db,
            conversation=conversation,
            contenido=frame.get("contenido"),
            client_message_id=client_msg_id,
        )
    except rules.ChatValidationError as exc:
        await websocket.send_json(
            {
                "type": "error",
                "code": "validation_error",
                "detail": str(exc),
                "client_message_id": client_msg_id,
            }
        )
        return True

    serialized = rules.serialize_chat_message(message)
    if created:
        await chat_manager.broadcast_message(
            conversation.modalidad,
            conversation.solicitud_id,
            serialized,
        )
    else:
        await websocket.send_json(
            {
                "type": "message",
                "modalidad": conversation.modalidad,
                "solicitud_id": conversation.solicitud_id,
                "duplicado": True,
                "mensaje": serialized,
            }
        )
    return True


async def _handle_ws_sync(
    websocket: WebSocket,
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    user: User,
    after_raw: str | None,
) -> bool:
    """Revalida estado y entrega mensajes persistidos posteriores a 'after' tras reconexión (CA-04/CA-05)."""
    db.expire_all()
    try:
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
        after_dt = _parse_iso_datetime(after_raw)
        messages = rules.list_conversation_messages(
            db,
            conversation=conversation,
            after=after_dt,
        )
    except rules.ChatStateError as exc:
        await websocket.send_json(
            {
                "type": "chat_closed",
                "modalidad": modalidad,
                "solicitud_id": solicitud_id,
                "motivo": str(exc),
            }
        )
        await websocket.close(code=WS_CLOSE_STATE_DISABLED, reason=str(exc)[:120])
        return False
    except rules.ChatValidationError as exc:
        await websocket.send_json(
            {"type": "error", "code": "validation_error", "detail": str(exc)}
        )
        return True
    except rules.ChatError as exc:
        await websocket.close(code=_ws_close_code_for_error(exc), reason=str(exc)[:120])
        return False

    await websocket.send_json(
        {
            "type": "sync",
            "modalidad": conversation.modalidad,
            "solicitud_id": conversation.solicitud_id,
            "estado": conversation.estado,
            "mensajes": [rules.serialize_chat_message(m) for m in messages],
        }
    )
    return True


async def _handle_ws_ping(
    websocket: WebSocket,
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    user: User,
) -> bool:
    """Revalida estado en BD durante heartbeat para cerrar sesiones inactivas al finalizar/cancelar (CA-05)."""
    db.expire_all()
    try:
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
    except rules.ChatStateError as exc:
        await websocket.send_json(
            {
                "type": "chat_closed",
                "modalidad": modalidad,
                "solicitud_id": solicitud_id,
                "motivo": str(exc),
            }
        )
        await websocket.close(code=WS_CLOSE_STATE_DISABLED, reason=str(exc)[:120])
        return False
    except rules.ChatError as exc:
        await websocket.close(code=_ws_close_code_for_error(exc), reason=str(exc)[:120])
        return False

    await websocket.send_json({"type": "pong", "estado": conversation.estado})
    return True


@router.websocket("/chat/solicitudes/{modalidad}/{solicitud_id}/ws")
async def chat_websocket_endpoint(
    websocket: WebSocket,
    modalidad: str,
    solicitud_id: str,
    token: str | None = Query(default=None),
    after: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    raw_token = _extract_ws_token(websocket, token)
    try:
        claims = rules.verify_websocket_token(raw_token)
        uid = claims.get("uid") if isinstance(claims, dict) else None
        if not uid:
            raise rules.ChatUnauthorizedError("Token inválido.")
        user = db.scalar(select(User).where(User.firebase_uid == uid))
        if user is None:
            raise rules.ChatForbiddenError("Completa tu registro en TekniGO.")
        conversation = rules.authorize_conversation(
            db,
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            user=user,
        )
        initial_after = _parse_iso_datetime(after)
    except rules.ChatError as exc:
        await websocket.close(code=_ws_close_code_for_error(exc), reason=str(exc)[:120])
        return

    await websocket.accept()
    await chat_manager.register(conversation.modalidad, conversation.solicitud_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "modalidad": conversation.modalidad,
                "solicitud_id": conversation.solicitud_id,
                "estado": conversation.estado,
                "rol_usuario": conversation.rol_usuario,
            }
        )
        if initial_after is not None:
            missed = rules.list_conversation_messages(
                db,
                conversation=conversation,
                after=initial_after,
            )
            await websocket.send_json(
                {
                    "type": "sync",
                    "modalidad": conversation.modalidad,
                    "solicitud_id": conversation.solicitud_id,
                    "estado": conversation.estado,
                    "mensajes": [rules.serialize_chat_message(m) for m in missed],
                }
            )

        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                await websocket.send_json(
                    {"type": "error", "code": "invalid_frame", "detail": "Mensaje JSON inválido."}
                )
                continue

            action = str(frame.get("type") or "").strip().lower()
            if action == "ping":
                keep_open = await _handle_ws_ping(
                    websocket,
                    db,
                    modalidad=conversation.modalidad,
                    solicitud_id=conversation.solicitud_id,
                    user=user,
                )
                if not keep_open:
                    break
                continue
            if action == "sync":
                keep_open = await _handle_ws_sync(
                    websocket,
                    db,
                    modalidad=conversation.modalidad,
                    solicitud_id=conversation.solicitud_id,
                    user=user,
                    after_raw=frame.get("after"),
                )
                if not keep_open:
                    break
                continue
            if action == "send":
                keep_open = await _handle_ws_send(
                    websocket,
                    db,
                    modalidad=conversation.modalidad,
                    solicitud_id=conversation.solicitud_id,
                    user=user,
                    frame=frame,
                )
                if not keep_open:
                    break
                continue

            await websocket.send_json(
                {"type": "error", "code": "unsupported_action", "detail": "Acción no soportada."}
            )
    except WebSocketDisconnect:
        pass
    finally:
        chat_manager.disconnect(conversation.modalidad, conversation.solicitud_id, websocket)
