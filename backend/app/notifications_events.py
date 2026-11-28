"""HU-47 ST-01/ST-02: publicador Outbox transaccional y emisores de dominio (CA-02, CA-03)."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

from sqlalchemy import Engine, event, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import (
    NotificationEvent,
    User,
    UserRole,
    ensure_utc_datetime,
    monotonic_uuid7,
)
from app.notifications_consumer import process_notification_events_by_ids
from app.notifications_templates import (
    EVENT_AJUSTE_PRESUPUESTO_APROBADO,
    EVENT_AJUSTE_PRESUPUESTO_RECHAZADO,
    EVENT_AJUSTE_PRESUPUESTO_SOLICITADO,
    EVENT_CERTIFICACION_APROBADA,
    EVENT_CERTIFICACION_RECHAZADA,
    EVENT_CERTIFICACION_REVOCADA,
    EVENT_CIERRE_AUTOMATICO_48H,
    EVENT_DESEMBOLSO_PAGADO,
    EVENT_DESEMBOLSO_RECHAZADO,
    EVENT_DESEMBOLSO_SOLICITADO,
    EVENT_DOCUMENTACION_APROBADA,
    EVENT_FINALIZACION_REPORTADA,
    EVENT_INCIDENCIA_ABIERTA,
    EVENT_INCIDENCIA_RESUELTA,
    EVENT_LIQUIDACION_APROBADA,
    EVENT_LIQUIDACION_RECHAZADA,
    EVENT_LIQUIDACION_RECIBIDA,
    EVENT_NUEVA_CONTRAOFERTA_ABIERTA,
    EVENT_NUEVA_OFERTA_ABIERTA,
    EVENT_NUEVA_SOLICITUD_DIRECTA,
    EVENT_PROPUESTA_ADJUDICADA,
    EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION,
    EVENT_PROPUESTA_RECHAZADA,
    EVENT_RECEPCION_CONFIRMADA,
    EVENT_RESTRICCION_OPERATIVA,
    EVENT_SERVICIO_CANCELADO,
    EVENT_SERVICIO_INICIADO,
    EVENT_SOLICITUD_DIRECTA_ACEPTADA,
    EVENT_SOLICITUD_DIRECTA_RECHAZADA,
    EVENT_VERIFICACION_APROBADA,
    EVENT_VERIFICACION_RECHAZADA,
    NotificationPrivacySanitizer,
    is_supported_notification_event,
)

logger = logging.getLogger(__name__)
_PENDING_EVENTS_KEY = "_pending_notification_event_ids"


def resolve_user_uuid(db: Session, identifier: uuid.UUID | str | None) -> uuid.UUID | None:
    if identifier is None:
        return None
    if isinstance(identifier, uuid.UUID):
        return identifier
    raw = str(identifier).strip()
    if not raw:
        return None
    parsed_uuid: uuid.UUID | None = None
    try:
        parsed_uuid = uuid.UUID(raw)
    except ValueError:
        parsed_uuid = None

    if parsed_uuid is not None:
        user_id = db.scalar(
            select(User.id).where(or_(User.id == parsed_uuid, User.firebase_uid == raw))
        )
        return user_id
    return db.scalar(select(User.id).where(User.firebase_uid == raw))


def list_admin_user_ids(db: Session) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(UserRole.usuario_id).where(UserRole.rol == "ADMIN")
        ).all()
    )


def _normalize_recipients(
    db: Session,
    raw_recipients: list[tuple[uuid.UUID | str | None, str]],
) -> list[dict[str, str]]:
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for identifier, role in raw_recipients:
        resolved = resolve_user_uuid(db, identifier)
        if resolved is None:
            continue
        uid_str = str(resolved)
        if uid_str in seen:
            continue
        seen.add(uid_str)
        normalized.append({"usuario_id": uid_str, "rol": (role or "CLIENTE").strip().upper()})
    return normalized


def enqueue_notification_event(
    db: Session,
    *,
    clave_evento: str,
    tipo_evento: str,
    destinatarios: list[tuple[uuid.UUID | str | None, str]],
    payload_seguro: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> NotificationEvent | None:
    """Registra de forma idempotente un evento outbox dentro de la transacción actual (CA-04/ST-02)."""
    if not isinstance(db, Session):
        return None
    bind = db.get_bind()
    if bind is None:
        return None

    clean_type = (tipo_evento or "").strip().upper()
    if not is_supported_notification_event(clean_type):
        raise ValueError(f"Tipo de evento no soportado: {clean_type}")

    clean_key = (clave_evento or "").strip()[:180]
    if not clean_key:
        raise ValueError("clave_evento es obligatoria")

    normalized_targets = _normalize_recipients(db, destinatarios)
    if not normalized_targets:
        return None

    safe_datos = NotificationPrivacySanitizer.sanitize_metadata(payload_seguro)
    envelope = {
        "destinatarios": normalized_targets,
        "datos": safe_datos,
    }
    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert moment is not None

    try:
        existing = db.scalar(
            select(NotificationEvent).where(NotificationEvent.clave_evento == clean_key)
        )
        if existing is not None:
            if existing.estado != "PROCESADO":
                pending = db.info.setdefault(_PENDING_EVENTS_KEY, [])
                if existing.id not in pending:
                    pending.append(existing.id)
            return existing

        event_row = NotificationEvent(
            id=monotonic_uuid7(moment),
            clave_evento=clean_key,
            tipo_evento=clean_type,
            payload_seguro=envelope,
            estado="PENDIENTE",
            intentos=0,
            creado_en=moment,
            actualizado_en=moment,
        )
        with db.begin_nested():
            db.add(event_row)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(NotificationEvent).where(NotificationEvent.clave_evento == clean_key)
        )
        if existing is not None and existing.estado != "PROCESADO":
            pending = db.info.setdefault(_PENDING_EVENTS_KEY, [])
            if existing.id not in pending:
                pending.append(existing.id)
        return existing
    except SQLAlchemyError as exc:
        logger.warning("No se pudo encolar evento de notificación %s: %s", clean_key, type(exc).__name__)
        return None

    pending = db.info.setdefault(_PENDING_EVENTS_KEY, [])
    if event_row.id not in pending:
        pending.append(event_row.id)
    return event_row


@event.listens_for(Session, "after_commit")
def _dispatch_outbox_after_commit(session: Session) -> None:
    pending_ids = session.info.pop(_PENDING_EVENTS_KEY, None)
    if not pending_ids:
        return
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        return
    try:
        with Session(bind) as consumer_db:
            process_notification_events_by_ids(consumer_db, list(pending_ids))
    except Exception as exc:
        logger.warning("Error no bloqueante al despachar notificaciones post-commit: %s", type(exc).__name__)


@event.listens_for(Session, "after_rollback")
def _discard_outbox_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_EVENTS_KEY, None)


def _safe_service_name(db: Session, record: Any) -> str:
    from app.models import Service, Specialty

    raw_service_id = getattr(record, "servicio_id", None)
    if raw_service_id:
        try:
            srv = db.get(Service, raw_service_id if isinstance(raw_service_id, uuid.UUID) else uuid.UUID(str(raw_service_id)))
            if srv is not None and srv.nombre:
                return srv.nombre
        except (ValueError, SQLAlchemyError):
            pass
    raw_spec_id = getattr(record, "especialidad_id", None)
    if raw_spec_id:
        try:
            spec = db.get(Specialty, raw_spec_id if isinstance(raw_spec_id, uuid.UUID) else uuid.UUID(str(raw_spec_id)))
            if spec is not None and spec.nombre:
                return spec.nombre
        except (ValueError, SQLAlchemyError):
            pass
    return "servicio técnico"


# --- Emisores de dominio CA-02 ---


def notify_direct_request_created(db: Session, record: Any) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"solicitud_directa_creada:{record.id}",
        tipo_evento=EVENT_NUEVA_SOLICITUD_DIRECTA,
        destinatarios=[(getattr(record, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "solicitud_id": str(record.id),
            "modalidad": "DIRECTA",
            "municipio": getattr(record, "municipio", None),
            "sector": getattr(record, "sector", None),
            "servicio_nombre": _safe_service_name(db, record),
        },
        now=getattr(record, "creada_en", None),
    )


def notify_direct_request_decision(db: Session, record: Any, *, accepted: bool, now: datetime | None = None) -> None:
    event_type = EVENT_SOLICITUD_DIRECTA_ACEPTADA if accepted else EVENT_SOLICITUD_DIRECTA_RECHAZADA
    suffix = "aceptada" if accepted else "rechazada"
    enqueue_notification_event(
        db,
        clave_evento=f"solicitud_directa_{suffix}:{record.id}",
        tipo_evento=event_type,
        destinatarios=[(getattr(record, "cliente_id", None), "CLIENTE")],
        payload_seguro={
            "solicitud_id": str(record.id),
            "modalidad": "DIRECTA",
            "servicio_nombre": _safe_service_name(db, record),
        },
        now=now,
    )


def notify_open_request_proposal(
    db: Session,
    record: Any,
    proposal: Any,
    *,
    now: datetime | None = None,
) -> None:
    is_counter = (getattr(proposal, "tipo", "") or "").strip().upper() == "CONTRAOFERTA"
    event_type = EVENT_NUEVA_CONTRAOFERTA_ABIERTA if is_counter else EVENT_NUEVA_OFERTA_ABIERTA
    version_ts = ensure_utc_datetime(getattr(proposal, "actualizada_en", None) or getattr(proposal, "creada_en", None) or now)
    ts_token = version_ts.isoformat() if version_ts is not None else "v1"
    enqueue_notification_event(
        db,
        clave_evento=f"propuesta_abierta:{proposal.id}:{event_type}:{ts_token}",
        tipo_evento=event_type,
        destinatarios=[(getattr(record, "cliente_id", None), "CLIENTE")],
        payload_seguro={
            "solicitud_id": str(record.id),
            "propuesta_id": str(proposal.id),
            "modalidad": "ABIERTA",
            "monto": float(getattr(proposal, "monto", 0) or 0),
            "es_evento_propuesta_vivo": True,
        },
        now=now or version_ts,
    )


def notify_open_request_proposal_rejected(
    db: Session,
    record: Any,
    proposal: Any,
    *,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"propuesta_rechazada:{record.id}:{proposal.id}",
        tipo_evento=EVENT_PROPUESTA_RECHAZADA,
        destinatarios=[(getattr(proposal, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "solicitud_id": str(record.id),
            "propuesta_id": str(proposal.id),
            "modalidad": "ABIERTA",
            "es_evento_propuesta_vivo": True,
        },
        now=now,
    )


def notify_open_request_award(
    db: Session,
    record: Any,
    winning_proposal: Any,
    discarded_proposals: list[Any],
    *,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"adjudicacion_ganadora:{record.id}:{winning_proposal.id}",
        tipo_evento=EVENT_PROPUESTA_ADJUDICADA,
        destinatarios=[(getattr(winning_proposal, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "solicitud_id": str(record.id),
            "propuesta_id": str(winning_proposal.id),
            "modalidad": "ABIERTA",
            "monto": float(getattr(winning_proposal, "monto", 0) or 0),
            "es_evento_propuesta_vivo": True,
        },
        now=now,
    )

    for disc in discarded_proposals:
        enqueue_notification_event(
            db,
            clave_evento=f"adjudicacion_descarte:{record.id}:{disc.id}",
            tipo_evento=EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION,
            destinatarios=[(getattr(disc, "tecnico_id", None), "TECNICO")],
            payload_seguro={
                "solicitud_id": str(record.id),
                "propuesta_id": str(disc.id),
                "modalidad": "ABIERTA",
                "municipio": getattr(record, "municipio", None),
                "sector": getattr(record, "sector", None),
                "es_evento_propuesta_vivo": True,
            },
            now=now,
        )


def notify_service_started(db: Session, record: Any, *, modalidad: str, now: datetime | None = None) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"servicio_iniciado:{modalidad}:{record.id}",
        tipo_evento=EVENT_SERVICIO_INICIADO,
        destinatarios=[(getattr(record, "cliente_id", None), "CLIENTE")],
        payload_seguro={"solicitud_id": str(record.id), "modalidad": modalidad},
        now=now,
    )


def notify_service_completion_reported(
    db: Session,
    record: Any,
    *,
    modalidad: str,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"finalizacion_reportada:{modalidad}:{record.id}",
        tipo_evento=EVENT_FINALIZACION_REPORTADA,
        destinatarios=[(getattr(record, "cliente_id", None), "CLIENTE")],
        payload_seguro={"solicitud_id": str(record.id), "modalidad": modalidad},
        now=now,
    )


def notify_service_reception_confirmed(
    db: Session,
    record: Any,
    *,
    modalidad: str,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"recepcion_confirmada:{modalidad}:{record.id}",
        tipo_evento=EVENT_RECEPCION_CONFIRMADA,
        destinatarios=[(getattr(record, "tecnico_id", None), "TECNICO")],
        payload_seguro={"solicitud_id": str(record.id), "modalidad": modalidad},
        now=now,
    )


def notify_service_auto_completed(
    db: Session,
    record: Any,
    *,
    modalidad: str,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"cierre_automatico_48h:{modalidad}:{record.id}",
        tipo_evento=EVENT_CIERRE_AUTOMATICO_48H,
        destinatarios=[
            (getattr(record, "cliente_id", None), "CLIENTE"),
            (getattr(record, "tecnico_id", None), "TECNICO"),
        ],
        payload_seguro={"solicitud_id": str(record.id), "modalidad": modalidad},
        now=now,
    )


def notify_service_cancelled(
    db: Session,
    record: Any,
    *,
    modalidad: str,
    actor_role: str,
    now: datetime | None = None,
) -> None:
    targets: list[tuple[uuid.UUID | str | None, str]] = []
    norm_actor = (actor_role or "").strip().upper()
    if norm_actor != "CLIENTE":
        targets.append((getattr(record, "cliente_id", None), "CLIENTE"))
    if norm_actor != "TECNICO" and getattr(record, "tecnico_id", None):
        targets.append((getattr(record, "tecnico_id", None), "TECNICO"))
    if not targets:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"servicio_cancelado:{modalidad}:{record.id}",
        tipo_evento=EVENT_SERVICIO_CANCELADO,
        destinatarios=targets,
        payload_seguro={"solicitud_id": str(record.id), "modalidad": modalidad},
        now=now,
    )


def notify_incident_opened(
    db: Session,
    *,
    solicitud_id: str,
    modalidad: str,
    ticket_id: str,
    counterpart_uid: str | None,
    counterpart_role: str,
    now: datetime | None = None,
) -> None:
    targets: list[tuple[uuid.UUID | str | None, str]] = []
    if counterpart_uid:
        targets.append((counterpart_uid, counterpart_role))
    for admin_id in list_admin_user_ids(db):
        targets.append((admin_id, "ADMIN"))
    if not targets:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"incidencia_abierta:{solicitud_id}:{ticket_id}",
        tipo_evento=EVENT_INCIDENCIA_ABIERTA,
        destinatarios=targets,
        payload_seguro={
            "solicitud_id": str(solicitud_id),
            "modalidad": modalidad,
            "ticket_id": str(ticket_id),
        },
        now=now,
    )


def notify_incident_resolved(
    db: Session,
    *,
    solicitud_id: str,
    modalidad: str,
    cliente_uid: str | None,
    tecnico_uid: str | None,
    decision: str,
    resolution_key: str,
    now: datetime | None = None,
) -> None:
    targets: list[tuple[uuid.UUID | str | None, str]] = []
    if cliente_uid:
        targets.append((cliente_uid, "CLIENTE"))
    if tecnico_uid:
        targets.append((tecnico_uid, "TECNICO"))
    enqueue_notification_event(
        db,
        clave_evento=f"incidencia_resuelta:{solicitud_id}:{resolution_key}",
        tipo_evento=EVENT_INCIDENCIA_RESUELTA,
        destinatarios=targets,
        payload_seguro={
            "solicitud_id": str(solicitud_id),
            "modalidad": modalidad,
            "decision": decision,
        },
        now=now,
    )


def notify_adjustment_requested(
    db: Session,
    record: Any,
    adjustment: Any,
    *,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"ajuste_solicitado:{adjustment.modalidad}:{adjustment.solicitud_id}:{adjustment.id}",
        tipo_evento=EVENT_AJUSTE_PRESUPUESTO_SOLICITADO,
        destinatarios=[(getattr(record, "cliente_id", None), "CLIENTE")],
        payload_seguro={
            "solicitud_id": str(adjustment.solicitud_id),
            "modalidad": adjustment.modalidad,
            "nuevo_total": float(getattr(adjustment, "nuevo_total", 0) or 0),
        },
        now=now,
    )


def notify_adjustment_responded(
    db: Session,
    record: Any,
    adjustment: Any,
    *,
    approved: bool,
    now: datetime | None = None,
) -> None:
    event_type = EVENT_AJUSTE_PRESUPUESTO_APROBADO if approved else EVENT_AJUSTE_PRESUPUESTO_RECHAZADO
    suffix = "aprobado" if approved else "rechazado"
    enqueue_notification_event(
        db,
        clave_evento=f"ajuste_{suffix}:{adjustment.modalidad}:{adjustment.solicitud_id}:{adjustment.id}",
        tipo_evento=event_type,
        destinatarios=[(getattr(record, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "solicitud_id": str(adjustment.solicitud_id),
            "modalidad": adjustment.modalidad,
            "nuevo_total": float(getattr(adjustment, "nuevo_total", 0) or 0),
        },
        now=now,
    )


# --- Emisores de dominio CA-03 ---


def notify_verification_decision(
    db: Session,
    user: User,
    *,
    role: str,
    outcome: str,
    cycle_key: str,
    now: datetime | None = None,
) -> None:
    norm = (outcome or "").strip().upper()
    if norm == "VERIFICADO":
        event_type = EVENT_VERIFICACION_APROBADA
    elif norm == "DOCUMENTACION_APROBADA":
        event_type = EVENT_DOCUMENTACION_APROBADA
    else:
        event_type = EVENT_VERIFICACION_RECHAZADA
    enqueue_notification_event(
        db,
        clave_evento=f"verificacion:{user.id}:{norm}:{cycle_key}",
        tipo_evento=event_type,
        destinatarios=[(user.id, role)],
        payload_seguro={"estado": norm},
        now=now,
    )


def notify_certificate_decision(
    db: Session,
    cert: Any,
    *,
    now: datetime | None = None,
) -> None:
    state = (getattr(cert, "estado", "") or "").strip().upper()
    event_type = {
        "APROBADO": EVENT_CERTIFICACION_APROBADA,
        "RECHAZADO": EVENT_CERTIFICACION_RECHAZADA,
        "REVOCADO": EVENT_CERTIFICACION_REVOCADA,
    }.get(state)
    if event_type is None:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"certificacion:{cert.id}:{state}",
        tipo_evento=event_type,
        destinatarios=[(getattr(cert, "usuario_id", None), "TECNICO")],
        payload_seguro={
            "certificacion_id": str(cert.id),
            "titulo_certificado": getattr(cert, "titulo", None),
            "estado": state,
        },
        now=now,
    )


def notify_settlement_submitted(db: Session, settlement: Any, *, now: datetime | None = None) -> None:
    admins = [(admin_id, "ADMIN") for admin_id in list_admin_user_ids(db)]
    if not admins:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"liquidacion_recibida:{settlement.id}",
        tipo_evento=EVENT_LIQUIDACION_RECIBIDA,
        destinatarios=admins,
        payload_seguro={"liquidacion_id": str(settlement.id)},
        now=now,
    )


def notify_settlement_decision(
    db: Session,
    settlement: Any,
    *,
    unblocked: bool,
    still_restricted: bool,
    now: datetime | None = None,
) -> None:
    state = (getattr(settlement, "estado", "") or "").strip().upper()
    approved = state in {"APROBADA", "EN_CONCILIACION"}
    event_type = EVENT_LIQUIDACION_APROBADA if approved else EVENT_LIQUIDACION_RECHAZADA
    enqueue_notification_event(
        db,
        clave_evento=f"liquidacion_decision:{settlement.id}:{state}",
        tipo_evento=event_type,
        destinatarios=[(getattr(settlement, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "liquidacion_id": str(settlement.id),
            "estado": state,
            "importe_aplicado": float(getattr(settlement, "importe_aplicado", 0) or 0),
            "desbloqueado": unblocked,
        },
        now=now,
    )
    if still_restricted:
        notify_operational_restriction(
            db,
            getattr(settlement, "tecnico_id", None),
            motivo_codigo="DEUDA_COMISIONES",
            cycle_key=f"liquidacion:{settlement.id}:{state}",
            now=now,
        )


def notify_payout_requested(db: Session, order: Any, *, now: datetime | None = None) -> None:
    admins = [(admin_id, "ADMIN") for admin_id in list_admin_user_ids(db)]
    if not admins:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"desembolso_solicitado:{order.id}",
        tipo_evento=EVENT_DESEMBOLSO_SOLICITADO,
        destinatarios=admins,
        payload_seguro={
            "orden_id": str(order.id),
            "importe": float(getattr(order, "importe", 0) or 0),
        },
        now=now,
    )


def notify_payout_decision(db: Session, order: Any, *, now: datetime | None = None) -> None:
    state = (getattr(order, "estado", "") or "").strip().upper()
    if state not in {"PAGADA", "RECHAZADA"}:
        return
    event_type = EVENT_DESEMBOLSO_PAGADO if state == "PAGADA" else EVENT_DESEMBOLSO_RECHAZADO
    enqueue_notification_event(
        db,
        clave_evento=f"desembolso_decision:{order.id}:{state}",
        tipo_evento=event_type,
        destinatarios=[(getattr(order, "tecnico_id", None), "TECNICO")],
        payload_seguro={
            "orden_id": str(order.id),
            "estado": state,
            "importe": float(getattr(order, "importe", 0) or 0),
        },
        now=now,
    )


def notify_operational_restriction(
    db: Session,
    usuario_id: uuid.UUID | str | None,
    *,
    motivo_codigo: str,
    cycle_key: str,
    role: str = "TECNICO",
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"restriccion_operativa:{usuario_id}:{motivo_codigo}:{cycle_key}",
        tipo_evento=EVENT_RESTRICCION_OPERATIVA,
        destinatarios=[(usuario_id, role)],
        payload_seguro={"motivo_codigo": motivo_codigo, "requiere_accion": True},
        now=now,
    )


def _primary_user_role(user: Any) -> str:
    roles = {getattr(r, "rol", "") for r in getattr(user, "roles", [])}
    return "TECNICO" if "TECNICO" in roles else "CLIENTE"


def notify_third_fault_admin_alert(
    db: Session,
    *,
    responsible_uid: str | None,
    solicitud_id: str,
    now: datetime | None = None,
) -> None:
    if not responsible_uid:
        return
    recipients: list[tuple[uuid.UUID | str | None, str]] = [
        (admin_id, "ADMIN") for admin_id in list_admin_user_ids(db)
    ]
    affected = db.scalar(select(User).where(User.firebase_uid == responsible_uid))
    if affected is not None:
        recipients.append((affected.id, _primary_user_role(affected)))
    if not recipients:
        return
    enqueue_notification_event(
        db,
        clave_evento=f"alerta_tercera_falta:{responsible_uid}:{solicitud_id}",
        tipo_evento=EVENT_RESTRICCION_OPERATIVA,
        destinatarios=recipients,
        payload_seguro={"motivo_codigo": "ALERTA_TERCERA_FALTA", "solicitud_id": solicitud_id},
        now=now,
    )


def notify_account_suspended(
    db: Session,
    user: User,
    *,
    suspension_id: uuid.UUID | str,
    tipo: str,
    vence_en: datetime | None = None,
    now: datetime | None = None,
) -> None:
    from app.models import to_tegucigalpa_datetime

    is_temporal = (tipo or "").strip().upper() == "TEMPORAL"
    motivo_codigo = "CUENTA_SUSPENDIDA_TEMPORAL" if is_temporal else "CUENTA_SUSPENDIDA_DEFINITIVA"
    local_end = to_tegucigalpa_datetime(vence_en) if is_temporal and vence_en is not None else None
    fecha_fin_local = local_end.strftime("%d/%m/%Y a las %H:%M") if local_end is not None else None

    enqueue_notification_event(
        db,
        clave_evento=f"cuenta_suspendida:{suspension_id}",
        tipo_evento=EVENT_RESTRICCION_OPERATIVA,
        destinatarios=[(user.id, _primary_user_role(user))],
        payload_seguro={
            "motivo_codigo": motivo_codigo,
            "suspension_id": str(suspension_id),
            "fecha_fin_local": fecha_fin_local,
        },
        now=now,
    )


def notify_temporal_suspension_ended(
    db: Session,
    user: User,
    *,
    suspension_id: uuid.UUID | str,
    ended_by: str,
    operaciones_restituidas: bool,
    now: datetime | None = None,
) -> None:
    enqueue_notification_event(
        db,
        clave_evento=f"suspension_finalizada:{suspension_id}:{ended_by}",
        tipo_evento=EVENT_RESTRICCION_OPERATIVA,
        destinatarios=[(user.id, _primary_user_role(user))],
        payload_seguro={
            "motivo_codigo": "SUSPENSION_TEMPORAL_FINALIZADA",
            "suspension_id": str(suspension_id),
            "operaciones_restituidas": bool(operaciones_restituidas),
        },
        now=now,
    )
