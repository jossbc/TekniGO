"""HU-47 ST-02: consumidor persistente de eventos con idempotencia estricta y reintentos."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Callable
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    NotificationEvent,
    TEGUCIGALPA_TZ_NAME,
    UserNotification,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
    monotonic_uuid7,
)
from app.notification_read_state import NotificationReadStatus
from app.notifications_realtime import NotificationBroadcaster, notification_manager
from app.notifications_templates import (
    NotificationPrivacySanitizer,
    build_notification_for_recipient,
)

logger = logging.getLogger(__name__)
DEFAULT_MAX_RETRY_ATTEMPTS = 5


def serialize_notification(row: UserNotification) -> dict[str, Any]:
    """Serializa una notificación garantizando UTC, America/Tegucigalpa y sanitización final (CA-01/CA-06)."""
    created_utc = ensure_utc_datetime(row.creada_en)
    read_utc = ensure_utc_datetime(row.leida_en)
    return {
        "id": str(row.id),
        "evento_id": str(row.evento_id),
        "destinatario_id": str(row.destinatario_id),
        "clave_idempotencia": row.clave_idempotencia,
        "tipo_evento": row.tipo_evento,
        "titulo": NotificationPrivacySanitizer.sanitize_text(row.titulo, max_length=160),
        "resumen": NotificationPrivacySanitizer.sanitize_text(row.resumen, max_length=500),
        "enlace_ruta": NotificationPrivacySanitizer.validate_internal_route(row.enlace_ruta),
        "recurso_tipo": row.recurso_tipo,
        "recurso_id": row.recurso_id,
        "metadatos_seguros": NotificationPrivacySanitizer.sanitize_metadata(row.metadatos_seguros),
        "estado_lectura": row.estado_lectura
        or (NotificationReadStatus.LEIDA.value if row.leida else NotificationReadStatus.PENDIENTE.value),
        "leida": bool(row.leida),
        "leida_en": read_utc.isoformat() if read_utc is not None else None,
        "leida_en_local": format_tegucigalpa_iso(read_utc),
        "creada_en": created_utc.isoformat() if created_utc is not None else None,
        "creada_en_local": format_tegucigalpa_iso(created_utc),
        "zona_horaria": TEGUCIGALPA_TZ_NAME,
    }


def _find_existing_notification(
    db: Session,
    *,
    evento_id: uuid.UUID,
    destinatario_id: uuid.UUID,
    clave_idempotencia: str,
) -> UserNotification | None:
    return db.scalar(
        select(UserNotification).where(
            UserNotification.destinatario_id == destinatario_id,
            (UserNotification.clave_idempotencia == clave_idempotencia)
            | (UserNotification.evento_id == evento_id),
        )
    )


def _create_user_notification_once(
    db: Session,
    *,
    event: NotificationEvent,
    destinatario_id: uuid.UUID,
    recipient_role: str,
    safe_data: dict[str, Any],
    moment: datetime,
) -> UserNotification | None:
    """Inserta el aviso para un destinatario de forma idempotente (CA-04)."""
    idempotency_key = f"{event.clave_evento}:{destinatario_id}"[:200]
    existing = _find_existing_notification(
        db,
        evento_id=event.id,
        destinatario_id=destinatario_id,
        clave_idempotencia=idempotency_key,
    )
    if existing is not None:
        return None

    draft = build_notification_for_recipient(
        event.tipo_evento,
        recipient_role=recipient_role,
        safe_data=safe_data,
    )
    row = UserNotification(
        id=monotonic_uuid7(moment),
        evento_id=event.id,
        destinatario_id=destinatario_id,
        clave_idempotencia=idempotency_key,
        tipo_evento=event.tipo_evento,
        titulo=draft.titulo,
        resumen=draft.resumen,
        enlace_ruta=draft.enlace_ruta,
        recurso_tipo=draft.recurso_tipo,
        recurso_id=draft.recurso_id,
        metadatos_seguros=draft.metadatos_seguros,
        estado_lectura=NotificationReadStatus.PENDIENTE.value,
        leida=False,
        leida_en=None,
        creada_en=moment,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return None

def _count_pending_by_recipients(
    db: Session,
    recipient_ids: set[uuid.UUID],
) -> dict[str, int]:
    """Calcula en una sola consulta el contador real de pendientes por destinatario."""
    if not recipient_ids:
        return {}
    counts: dict[str, int] = {str(uid): 0 for uid in recipient_ids}
    rows = db.execute(
        select(UserNotification.destinatario_id, func.count())
        .where(
            UserNotification.destinatario_id.in_(recipient_ids),
            UserNotification.estado_lectura == NotificationReadStatus.PENDIENTE.value,
            UserNotification.leida.is_(False),
        )
        .group_by(UserNotification.destinatario_id)
    ).all()
    for destinatario_id, total in rows:
        counts[str(destinatario_id)] = max(0, int(total or 0))
    return counts

def process_notification_event(
    db: Session,
    event_id: uuid.UUID | str,
    *,
    broadcaster: NotificationBroadcaster | None = notification_manager,
    delivery_hook: Callable[[NotificationEvent, list[dict[str, Any]]], None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Procesa un evento de outbox creando avisos idempotentes por destinatario y marcando estado."""
    parsed_id = event_id if isinstance(event_id, uuid.UUID) else uuid.UUID(str(event_id))
    event = db.scalar(
        select(NotificationEvent)
        .where(NotificationEvent.id == parsed_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        return {"procesado": False, "omitido": True, "notificaciones_creadas": 0}
    if event.estado == "PROCESADO":
        return {"procesado": True, "omitido": True, "notificaciones_creadas": 0}

    moment = ensure_utc_datetime(now or event.creado_en or datetime.now(timezone.utc))
    assert moment is not None

    event.intentos = int(event.intentos or 0) + 1
    event.actualizado_en = moment

    try:
        payload = event.payload_seguro if isinstance(event.payload_seguro, dict) else {}
        destinatarios = payload.get("destinatarios") or []
        safe_data = payload.get("datos") if isinstance(payload.get("datos"), dict) else {}

        created_rows: list[UserNotification] = []
        for target in destinatarios:
            if not isinstance(target, dict):
                continue
            raw_uid = target.get("usuario_id")
            if not raw_uid:
                continue
            try:
                recipient_uuid = uuid.UUID(str(raw_uid))
            except ValueError:
                continue
            recipient_role = str(target.get("rol") or "CLIENTE").strip().upper()
            created = _create_user_notification_once(
                db,
                event=event,
                destinatario_id=recipient_uuid,
                recipient_role=recipient_role,
                safe_data=safe_data,
                moment=moment,
            )
            if created is not None:
                created_rows.append(created)

        serialized_list = [serialize_notification(row) for row in created_rows]
        if delivery_hook is not None:
            delivery_hook(event, serialized_list)

        event.estado = "PROCESADO"
        event.procesado_en = moment
        event.ultimo_error = None
        event.actualizado_en = moment
        db.commit()

        if broadcaster is not None and serialized_list:
            try:
                recipient_uuids = {row.destinatario_id for row in created_rows}
                unread_counts = _count_pending_by_recipients(db, recipient_uuids)
                broadcaster.publish_notifications_sync(serialized_list, unread_counts=unread_counts)
            except Exception as exc:
                logger.warning("No se pudo difundir notificación en vivo: %s", type(exc).__name__)

        return {
            "procesado": True,
            "omitido": False,
            "notificaciones_creadas": len(serialized_list),
            "notificaciones": serialized_list,
        }
    except Exception as exc:
        db.rollback()
        failed_event = db.get(NotificationEvent, parsed_id)
        if failed_event is not None and failed_event.estado != "PROCESADO":
            failed_event.estado = "FALLIDO"
            failed_event.intentos = max(int(failed_event.intentos or 0), 1)
            if failed_event.intentos == 0:
                failed_event.intentos = 1
            failed_event.ultimo_error = NotificationPrivacySanitizer.sanitize_text(
                f"{type(exc).__name__}: {exc}", max_length=500
            )
            failed_event.actualizado_en = moment
            db.commit()
        logger.warning("Fallo al procesar evento de notificación %s: %s", parsed_id, type(exc).__name__)
        return {
            "procesado": False,
            "omitido": False,
            "error": type(exc).__name__,
            "notificaciones_creadas": 0,
        }


def process_notification_events_by_ids(
    db: Session,
    event_ids: list[uuid.UUID | str],
    *,
    broadcaster: NotificationBroadcaster | None = notification_manager,
    delivery_hook: Callable[[NotificationEvent, list[dict[str, Any]]], None] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    procesados = 0
    fallidos = 0
    omitidos = 0
    notificaciones_creadas = 0
    for event_id in event_ids:
        res = process_notification_event(
            db,
            event_id,
            broadcaster=broadcaster,
            delivery_hook=delivery_hook,
            now=now,
        )
        if res.get("omitido"):
            omitidos += 1
        elif res.get("procesado"):
            procesados += 1
            notificaciones_creadas += int(res.get("notificaciones_creadas") or 0)
        else:
            fallidos += 1
    return {
        "evaluados": len(event_ids),
        "procesados": procesados,
        "fallidos": fallidos,
        "omitidos": omitidos,
        "notificaciones_creadas": notificaciones_creadas,
    }


def process_pending_notification_events(
    db: Session,
    *,
    limit: int = 100,
    include_failed: bool = True,
    max_attempts: int = DEFAULT_MAX_RETRY_ATTEMPTS,
    broadcaster: NotificationBroadcaster | None = notification_manager,
    delivery_hook: Callable[[NotificationEvent, list[dict[str, Any]]], None] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Reprocesa eventos PENDIENTE o FALLIDO elegibles para reintento idempotente (ST-02)."""
    target_states = ("PENDIENTE", "FALLIDO") if include_failed else ("PENDIENTE",)
    pending_ids = list(
        db.scalars(
            select(NotificationEvent.id)
            .where(
                NotificationEvent.estado.in_(target_states),
                NotificationEvent.intentos < max_attempts,
            )
            .order_by(NotificationEvent.creado_en.asc(), NotificationEvent.id.asc())
            .limit(limit)
        ).all()
    )
    return process_notification_events_by_ids(
        db,
        pending_ids,
        broadcaster=broadcaster,
        delivery_hook=delivery_hook,
        now=now,
    )
