"""HU-47 / HU-49 ST-02: endpoints REST y canal WebSocket de la bandeja de notificaciones."""
from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _get_user
from app.models import User
from app.notifications_inbox_service import (
    DEFAULT_NOTIFICATION_LIMIT,
    MAX_NOTIFICATION_LIMIT,
    NotificationAccessDeniedError,
    NotificationInboxError,
    NotificationInboxService,
    NotificationNotFoundError,
    NotificationReadFilter,
    NotificationValidationError,
    SqlAlchemyNotificationInboxRepository,
)
from app.notifications_realtime import (
    NotificationForbiddenError,
    NotificationRealtimeError,
    NotificationUnauthorizedError,
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHORIZED,
    WS_CLOSE_VALIDATION,
    notification_manager,
    verify_notification_ws_token,
)
from app.security import get_current_user

router = APIRouter(tags=["Bandeja de Notificaciones (HU-47 / HU-49)"])
NO_STORE = {"Cache-Control": "no-store, private"}


def _build_inbox_service(db: Session) -> NotificationInboxService:
    return NotificationInboxService(
        SqlAlchemyNotificationInboxRepository(db),
        publish_read_sync=notification_manager.publish_read_sync,
    )


def _raise_http_for_domain_error(exc: NotificationInboxError) -> None:
    if isinstance(exc, NotificationValidationError):
        raise HTTPException(400, str(exc)) from None
    if isinstance(exc, NotificationNotFoundError):
        raise HTTPException(404, str(exc)) from None
    if isinstance(exc, NotificationAccessDeniedError):
        raise HTTPException(403, str(exc)) from None
    raise HTTPException(500, "Error al procesar notificaciones.") from None


def unread_notifications_count(db: Session, usuario_id: uuid.UUID) -> int:
    return SqlAlchemyNotificationInboxRepository(db).count_pending(usuario_id)


def list_user_notifications(
    db: Session,
    user: User,
    *,
    limit: int = DEFAULT_NOTIFICATION_LIMIT,
    offset: int = 0,
    after_id: str | None = None,
    since: str | None = None,
    estado: str | None = None,
    solo_no_leidas: bool = False,
) -> dict[str, Any]:
    try:
        return _build_inbox_service(db).list_notifications(
            user,
            limit=limit,
            offset=offset,
            after_id=after_id,
            since=since,
            estado=estado,
            solo_no_leidas=solo_no_leidas,
        )
    except NotificationInboxError as exc:
        _raise_http_for_domain_error(exc)


def get_unread_notifications_count(db: Session, user: User) -> dict[str, int]:
    return _build_inbox_service(db).get_unread_count(user)


def get_user_notification(db: Session, notification_id: str, user: User) -> dict[str, Any]:
    try:
        return _build_inbox_service(db).get_notification_detail(user, notification_id)
    except NotificationInboxError as exc:
        _raise_http_for_domain_error(exc)


def mark_notification_as_read(db: Session, notification_id: str, user: User) -> dict[str, Any]:
    try:
        return _build_inbox_service(db).mark_as_read(user, notification_id)
    except NotificationInboxError as exc:
        _raise_http_for_domain_error(exc)


def mark_all_notifications_as_read(db: Session, user: User) -> dict[str, Any]:
    try:
        return _build_inbox_service(db).mark_all_as_read(user)
    except NotificationInboxError as exc:
        _raise_http_for_domain_error(exc)


@router.get("/notifications")
def list_notifications_endpoint(
    response: Response,
    limit: int = Query(default=DEFAULT_NOTIFICATION_LIMIT, ge=1, le=MAX_NOTIFICATION_LIMIT),
    offset: int = Query(default=0, ge=0),
    after_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    estado: str | None = Query(default=None),
    solo_no_leidas: bool = Query(default=False),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    effective_after_id = after_id if isinstance(after_id, str) else None
    if not effective_after_id and isinstance(last_event_id, str):
        effective_after_id = last_event_id
    effective_since = (since if isinstance(since, str) else None) or (after if isinstance(after, str) else None)
    effective_estado = estado if isinstance(estado, str) else None
    effective_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_NOTIFICATION_LIMIT
    effective_offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else 0
    return list_user_notifications(
        db,
        user,
        limit=effective_limit,
        offset=effective_offset,
        after_id=effective_after_id,
        since=effective_since,
        estado=effective_estado,
        solo_no_leidas=bool(solo_no_leidas) if isinstance(solo_no_leidas, bool) else False,
    )


@router.get("/notifications/unread-count")
def get_unread_notifications_count_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    return get_unread_notifications_count(db, user)


@router.get("/notifications/pending")
def list_pending_notifications_endpoint(
    response: Response,
    limit: int = Query(default=DEFAULT_NOTIFICATION_LIMIT, ge=1, le=MAX_NOTIFICATION_LIMIT),
    offset: int = Query(default=0, ge=0),
    after_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    effective_after_id = after_id if isinstance(after_id, str) else None
    if not effective_after_id and isinstance(last_event_id, str):
        effective_after_id = last_event_id
    effective_since = (since if isinstance(since, str) else None) or (after if isinstance(after, str) else None)
    effective_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_NOTIFICATION_LIMIT
    effective_offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else 0
    return list_user_notifications(
        db,
        user,
        limit=effective_limit,
        offset=effective_offset,
        after_id=effective_after_id,
        since=effective_since,
        estado=NotificationReadFilter.PENDIENTE.value,
        solo_no_leidas=True,
    )


@router.get("/notifications/{notification_id}")
def get_notification_detail_endpoint(
    notification_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    return get_user_notification(db, notification_id, user)


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read_endpoint(
    notification_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    return mark_notification_as_read(db, notification_id, user)


@router.post("/notifications/read-all")
def mark_all_notifications_read_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    return mark_all_notifications_as_read(db, user)


def _extract_ws_token(websocket: WebSocket, query_token: str | None) -> str | None:
    headers = getattr(websocket, "headers", None) or {}
    auth_header = headers.get("authorization")
    if auth_header:
        return auth_header
    return query_token


def _ws_close_code_for_error(exc: NotificationRealtimeError) -> int:
    if isinstance(exc, NotificationUnauthorizedError):
        return WS_CLOSE_UNAUTHORIZED
    if isinstance(exc, NotificationForbiddenError):
        return WS_CLOSE_FORBIDDEN
    return WS_CLOSE_VALIDATION


def _collect_missed_notifications(
    db: Session,
    user: User,
    *,
    after_id: str | None,
    since_raw: str | None,
) -> dict[str, Any]:
    return list_user_notifications(
        db,
        user,
        limit=MAX_NOTIFICATION_LIMIT,
        offset=0,
        after_id=after_id,
        since=since_raw,
    )


@router.websocket("/notifications/ws")
async def notifications_websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    after_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    raw_token = _extract_ws_token(websocket, token)
    headers = getattr(websocket, "headers", None) or {}
    effective_after_id = after_id or headers.get("last-event-id")
    effective_since = since or after

    try:
        claims = verify_notification_ws_token(raw_token)
        uid = claims.get("uid") if isinstance(claims, dict) else None
        if not uid:
            raise NotificationUnauthorizedError("Token inválido.")
        user = db.scalar(select(User).where(User.firebase_uid == uid))
        if user is None:
            raise NotificationForbiddenError("Completa tu registro en TekniGO.")
    except NotificationRealtimeError as exc:
        await websocket.close(code=_ws_close_code_for_error(exc), reason=str(exc)[:120])
        return

    user_id_str = str(user.id)
    await websocket.accept()
    await notification_manager.register(user_id_str, websocket)
    try:
        unread = unread_notifications_count(db, user.id)
        await websocket.send_json(
            {
                "type": "connected",
                "usuario_id": user_id_str,
                "no_leidas": unread,
                "pendientes": unread,
            }
        )
        if effective_after_id or effective_since:
            try:
                missed = _collect_missed_notifications(
                    db,
                    user,
                    after_id=effective_after_id,
                    since_raw=effective_since,
                )
                await websocket.send_json(
                    {
                        "type": "sync",
                        "no_leidas": missed["no_leidas"],
                        "pendientes": missed["pendientes"],
                        "notificaciones": missed["notificaciones"],
                    }
                )
            except HTTPException as exc:
                await websocket.send_json(
                    {"type": "error", "code": "validation_error", "detail": str(exc.detail)}
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
                db.expire_all()
                current_unread = unread_notifications_count(db, user.id)
                await websocket.send_json(
                    {"type": "pong", "no_leidas": current_unread, "pendientes": current_unread}
                )
                continue
            if action == "unread_count":
                db.expire_all()
                current_unread = unread_notifications_count(db, user.id)
                await websocket.send_json(
                    {"type": "unread_count", "no_leidas": current_unread, "pendientes": current_unread}
                )
                continue
            if action == "sync":
                db.expire_all()
                try:
                    missed = _collect_missed_notifications(
                        db,
                        user,
                        after_id=frame.get("after_id"),
                        since_raw=frame.get("since") or frame.get("after"),
                    )
                    await websocket.send_json(
                        {
                            "type": "sync",
                            "no_leidas": missed["no_leidas"],
                            "pendientes": missed["pendientes"],
                            "notificaciones": missed["notificaciones"],
                        }
                    )
                except HTTPException as exc:
                    await websocket.send_json(
                        {"type": "error", "code": "validation_error", "detail": str(exc.detail)}
                    )
                continue

            await websocket.send_json(
                {"type": "error", "code": "unsupported_action", "detail": "Acción no soportada."}
            )
    except WebSocketDisconnect:
        pass
    finally:
        notification_manager.disconnect(user_id_str, websocket)
