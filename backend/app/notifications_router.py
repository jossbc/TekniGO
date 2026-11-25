"""HU-47 ST-01/ST-03/ST-04: endpoints REST y canal WebSocket de la bandeja de notificaciones."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _get_user
from app.models import TEGUCIGALPA_TZ_NAME, User, UserNotification, ensure_utc_datetime
from app.notifications_consumer import serialize_notification
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

router = APIRouter(tags=["Bandeja de Notificaciones (HU-47)"])
NO_STORE = {"Cache-Control": "no-store, private"}
DEFAULT_NOTIFICATION_LIMIT = 30
MAX_NOTIFICATION_LIMIT = 100


def _parse_iso_datetime(raw: str | None, *, field_name: str = "since") -> datetime | None:
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
        raise HTTPException(400, f"Formato de fecha '{field_name}' inválido (usa ISO-8601).") from None
    return ensure_utc_datetime(parsed)


def _parse_notification_uuid(raw_id: str | None) -> uuid.UUID:
    cleaned = (raw_id or "").strip()
    if not cleaned:
        raise HTTPException(404, "Notificación no encontrada.")
    try:
        return uuid.UUID(cleaned)
    except ValueError:
        raise HTTPException(404, "Notificación no encontrada.") from None


def unread_notifications_count(db: Session, usuario_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(UserNotification)
            .where(
                UserNotification.destinatario_id == usuario_id,
                UserNotification.leida.is_(False),
            )
        )
        or 0
    )


def _apply_recovery_cursor(
    query,
    *,
    db: Session,
    usuario_id: uuid.UUID,
    after_id: str | None,
    since_dt: datetime | None,
):
    """Aplica cursor incremental usando after_id (prioritario) o since/after (CA-05)."""
    if after_id and after_id.strip():
        try:
            ref_uuid = uuid.UUID(after_id.strip())
        except ValueError:
            raise HTTPException(400, "Identificador de cursor 'after_id' inválido.") from None
        ref_row = db.scalar(
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


def list_user_notifications(
    db: Session,
    user: User,
    *,
    limit: int = DEFAULT_NOTIFICATION_LIMIT,
    offset: int = 0,
    after_id: str | None = None,
    since: str | None = None,
    solo_no_leidas: bool = False,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_NOTIFICATION_LIMIT:
        raise HTTPException(400, f"El parámetro limit debe estar entre 1 y {MAX_NOTIFICATION_LIMIT}.")
    if offset < 0:
        raise HTTPException(400, "El parámetro offset no puede ser negativo.")

    since_dt = _parse_iso_datetime(since, field_name="since")
    has_cursor = bool((after_id and after_id.strip()) or since_dt is not None)

    base_query = select(UserNotification).where(UserNotification.destinatario_id == user.id)
    if solo_no_leidas:
        base_query = base_query.where(UserNotification.leida.is_(False))

    filtered_query = _apply_recovery_cursor(
        base_query,
        db=db,
        usuario_id=user.id,
        after_id=after_id,
        since_dt=since_dt,
    )
    total = int(db.scalar(select(func.count()).select_from(filtered_query.subquery())) or 0)

    if has_cursor:
        ordered = filtered_query.order_by(UserNotification.creada_en.asc(), UserNotification.id.asc())
    else:
        ordered = filtered_query.order_by(UserNotification.creada_en.desc(), UserNotification.id.desc())

    rows = list(db.scalars(ordered.offset(offset).limit(limit)).all())
    unread = unread_notifications_count(db, user.id)
    return {
        "total": total,
        "no_leidas": unread,
        "offset": offset,
        "limit": limit,
        "zona_horaria": TEGUCIGALPA_TZ_NAME,
        "notificaciones": [serialize_notification(row) for row in rows],
    }


def _load_owned_notification(
    db: Session,
    notification_id: str,
    user: User,
    *,
    lock: bool = False,
) -> UserNotification:
    parsed_id = _parse_notification_uuid(notification_id)
    query = select(UserNotification).where(UserNotification.id == parsed_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Notificación no encontrada.")
    if row.destinatario_id != user.id:
        raise HTTPException(403, "No tienes permiso para acceder a esta notificación.")
    return row


def get_user_notification(db: Session, notification_id: str, user: User) -> dict[str, Any]:
    row = _load_owned_notification(db, notification_id, user)
    return serialize_notification(row)


def mark_notification_as_read(db: Session, notification_id: str, user: User) -> dict[str, Any]:
    row = _load_owned_notification(db, notification_id, user, lock=True)
    changed = False
    if not row.leida:
        row.leida = True
        row.leida_en = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        changed = True
    unread = unread_notifications_count(db, user.id)
    serialized = serialize_notification(row)
    if changed:
        notification_manager.publish_read_sync(
            str(user.id),
            {"type": "read_sync", "notification_id": str(row.id), "no_leidas": unread},
        )
    return {
        "notificacion": serialized,
        "no_leidas": unread,
    }


def mark_all_notifications_as_read(db: Session, user: User) -> dict[str, Any]:
    unread_rows = list(
        db.scalars(
            select(UserNotification).where(
                UserNotification.destinatario_id == user.id,
                UserNotification.leida.is_(False),
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for row in unread_rows:
        row.leida = True
        row.leida_en = now
    if unread_rows:
        db.commit()
        notification_manager.publish_read_sync(
            str(user.id),
            {"type": "read_all_sync", "no_leidas": 0},
        )
    return {
        "actualizadas": len(unread_rows),
        "no_leidas": 0,
    }


@router.get("/notifications")
def list_notifications_endpoint(
    response: Response,
    limit: int = Query(default=DEFAULT_NOTIFICATION_LIMIT, ge=1, le=MAX_NOTIFICATION_LIMIT),
    offset: int = Query(default=0, ge=0),
    after_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    solo_no_leidas: bool = Query(default=False),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _get_user(claims, db)
    effective_after_id = after_id or last_event_id
    effective_since = since or after
    return list_user_notifications(
        db,
        user,
        limit=limit,
        offset=offset,
        after_id=effective_after_id,
        since=effective_since,
        solo_no_leidas=solo_no_leidas,
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
                await websocket.send_json(
                    {"type": "pong", "no_leidas": unread_notifications_count(db, user.id)}
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
