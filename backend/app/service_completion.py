"""HU-34 ST-02: Endpoint idempotente para reportar trabajo finalizado y validación de ajustes pendientes."""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import (
    CONFIRMATION_WINDOW_HOURS,
    TEGUCIGALPA_TZ_NAME,
    DirectRequest,
    OpenRequest,
    User,
    calculate_confirmation_expiration,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.security import get_current_user

router = APIRouter(tags=["Finalización de Servicios (HU-34)"])

PENDING_ADJUSTMENT_STATES = {
    "PENDIENTE",
    "PENDIENTE_APROBACION",
    "PENDIENTE DE APROBACION",
    "PENDIENTE_DE_APROBACION",
    "EN_REVISION",
    "EN REVISION",
}

ACTIVE_INCIDENT_STATES = {
    "ABIERTA",
    "PENDIENTE",
    "EN_REVISION",
    "EN REVISION",
    "EN_DISPUTA",
    "EN DISPUTA",
    "ACTIVA",
}

_COMPLETION_EVENTS: dict[str, list[dict]] = defaultdict(list)
_PENDING_ADJUSTMENTS_REGISTRY: dict[str, list[dict]] = defaultdict(list)
_SERVICE_INCIDENTS_REGISTRY: dict[str, list[dict]] = defaultdict(list)
_AUTO_CLOSE_REQUEST_EVENTS: dict[str, list[dict]] = defaultdict(list)


class ReportServiceCompletionSchema(BaseModel):
    """Cuerpo opcional para reportar la finalización del servicio (permite reloj controlado en pruebas)."""

    model_config = ConfigDict(extra="forbid")

    comentario: str | None = None
    now: datetime | None = None


class ScheduledExpiredCheckSchema(BaseModel):
    """Cuerpo opcional para ejecutar la tarea programada con reloj controlado."""

    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None
    modalidad: str = "TODOS"
    solicitar_cierre_automatico: bool = True
    proceso_responsable: str = "TAREA_PROGRAMADA_REPORTES_VENCIDOS"


def _canonical_state_key(raw_estado: str | None) -> str:
    tokens = (raw_estado or "").strip().upper().replace("-", " ").replace("_", " ").split()
    return "_".join(tokens)


def is_in_progress_state(raw_estado: str | None) -> bool:
    return _canonical_state_key(raw_estado) == "EN_PROCESO"


def register_pending_adjustment(
    solicitud_id: str,
    *,
    ajuste_id: str | None = None,
    monto_solicitado: float | None = None,
    motivo: str | None = None,
    estado: str = "PENDIENTE",
) -> dict:
    """Registra un ajuste pendiente para una solicitud (soporte transversal con HU-36)."""
    clean_id = (solicitud_id or "").strip()
    entry = {
        "id": ajuste_id or str(uuid.uuid4()),
        "solicitud_id": clean_id,
        "monto_solicitado": monto_solicitado,
        "motivo": motivo or "Ajuste de presupuesto pendiente de aprobación del cliente",
        "estado": (estado or "PENDIENTE").strip().upper(),
        "activo": True,
    }
    _PENDING_ADJUSTMENTS_REGISTRY[clean_id].append(entry)
    return entry


def resolve_pending_adjustments(solicitud_id: str, *, nuevo_estado: str = "APROBADO") -> None:
    """Marca como resueltos los ajustes en memoria de una solicitud."""
    clean_id = (solicitud_id or "").strip()
    for item in _PENDING_ADJUSTMENTS_REGISTRY.get(clean_id, []):
        item["estado"] = (nuevo_estado or "APROBADO").strip().upper()
        item["activo"] = False


def clear_pending_adjustments(solicitud_id: str | None = None) -> None:
    if solicitud_id is None:
        _PENDING_ADJUSTMENTS_REGISTRY.clear()
    else:
        _PENDING_ADJUSTMENTS_REGISTRY.pop(str(solicitud_id).strip(), None)


def get_completion_events(solicitud_id: str) -> list[dict]:
    return list(_COMPLETION_EVENTS.get(str(solicitud_id).strip(), []))


def clear_completion_events(solicitud_id: str | None = None) -> None:
    if solicitud_id is None:
        _COMPLETION_EVENTS.clear()
        _AUTO_CLOSE_REQUEST_EVENTS.clear()
    else:
        clean_id = str(solicitud_id).strip()
        _COMPLETION_EVENTS.pop(clean_id, None)
        _AUTO_CLOSE_REQUEST_EVENTS.pop(clean_id, None)


def get_auto_close_request_events(solicitud_id: str | None = None) -> list[dict]:
    if solicitud_id is None:
        all_events: list[dict] = []
        for items in _AUTO_CLOSE_REQUEST_EVENTS.values():
            all_events.extend(items)
        return all_events
    return list(_AUTO_CLOSE_REQUEST_EVENTS.get(str(solicitud_id).strip(), []))


def register_service_incident(
    solicitud_id: str,
    *,
    incidencia_id: str | None = None,
    motivo: str | None = None,
    estado: str = "ABIERTA",
) -> dict:
    """Registra una incidencia abierta asociada al servicio (soporte para CA-06 y HU-35)."""
    clean_id = (solicitud_id or "").strip()
    entry = {
        "id": incidencia_id or str(uuid.uuid4()),
        "solicitud_id": clean_id,
        "motivo": motivo or "Incidencia reportada por el cliente dentro del plazo de 48 horas",
        "estado": (estado or "ABIERTA").strip().upper(),
        "activa": True,
    }
    _SERVICE_INCIDENTS_REGISTRY[clean_id].append(entry)
    return entry


def resolve_service_incidents(solicitud_id: str, *, nuevo_estado: str = "RESUELTA") -> None:
    clean_id = (solicitud_id or "").strip()
    for item in _SERVICE_INCIDENTS_REGISTRY.get(clean_id, []):
        item["estado"] = (nuevo_estado or "RESUELTA").strip().upper()
        item["activa"] = False


def clear_service_incidents(solicitud_id: str | None = None) -> None:
    if solicitud_id is None:
        _SERVICE_INCIDENTS_REGISTRY.clear()
    else:
        _SERVICE_INCIDENTS_REGISTRY.pop(str(solicitud_id).strip(), None)


def _incident_item_is_active(item: Any) -> bool:
    if isinstance(item, dict):
        if item.get("activa") is False or item.get("activo") is False:
            return False
        raw_state = str(item.get("estado") or "ABIERTA").strip().upper()
        return raw_state in ACTIVE_INCIDENT_STATES
    if getattr(item, "activa", True) is False or getattr(item, "activo", True) is False:
        return False
    raw_state = str(getattr(item, "estado", None) or "ABIERTA").strip().upper()
    return raw_state in ACTIVE_INCIDENT_STATES


def has_active_incident(record: Any, db: Session | None = None) -> tuple[bool, str | None]:
    """CA-06: Comprueba si existe una incidencia abierta o activa sobre el servicio antes de solicitar cierre automático."""
    solicitud_id = str(getattr(record, "id", "") or "").strip()

    # 1. Bandera booleana directa en la entidad
    for flag_attr in ("tiene_incidencia_abierta", "tiene_incidencia", "incidencia_activa", "en_disputa"):
        if bool(getattr(record, flag_attr, False)):
            return True, "El servicio presenta una incidencia abierta."

    # 2. Estado de incidencia directo en la entidad
    raw_inc_state = str(getattr(record, "estado_incidencia", "") or "").strip().upper()
    if raw_inc_state in ACTIVE_INCIDENT_STATES:
        return True, f"Existe una incidencia en estado {raw_inc_state}."

    # 3. Colección de incidencias/disputas en la entidad
    for attr_name in ("incidencias", "disputas", "reclamos"):
        collection = getattr(record, attr_name, None)
        if isinstance(collection, (list, tuple, set)):
            for item in collection:
                if _incident_item_is_active(item):
                    return True, "Existe una incidencia abierta asociada al servicio."

    # 4. Registro de incidencias en memoria
    for item in _SERVICE_INCIDENTS_REGISTRY.get(solicitud_id, []):
        if _incident_item_is_active(item):
            return True, item.get("motivo") or "Existe una incidencia abierta."

    # 5. Modelo persistente de incidencias en base de datos si está definido
    if db is not None and solicitud_id:
        for model_name in ("ServiceIncident", "IncidenciaServicio", "Incident", "Incidencia"):
            inc_model = getattr(models, model_name, None)
            if inc_model is not None and hasattr(inc_model, "solicitud_id"):
                rows = db.scalars(
                    select(inc_model).where(inc_model.solicitud_id == solicitud_id)
                ).all()
                for row in rows:
                    if _incident_item_is_active(row):
                        return True, "Existe una incidencia abierta en base de datos."

    return False, None


def _adjustment_item_is_pending(item: Any) -> bool:
    if isinstance(item, dict):
        if item.get("activo") is False:
            return False
        raw_state = str(item.get("estado") or "PENDIENTE").strip().upper()
        return raw_state in PENDING_ADJUSTMENT_STATES
    if getattr(item, "activo", True) is False:
        return False
    raw_state = str(getattr(item, "estado", None) or "PENDIENTE").strip().upper()
    return raw_state in PENDING_ADJUSTMENT_STATES


def has_pending_adjustment(record: Any, db: Session | None = None) -> tuple[bool, str | None]:
    """CA-05: Verifica si existe un ajuste pendiente de aprobación antes de permitir reportar finalización."""
    solicitud_id = str(getattr(record, "id", "") or "").strip()

    # 1. Bandera booleana directa en la instancia
    if bool(getattr(record, "tiene_ajuste_pendiente", False)) or bool(
        getattr(record, "ajuste_pendiente", False)
    ):
        return True, "Existe un ajuste pendiente de aprobación en el servicio."

    # 2. Estado de ajuste directo en la instancia
    raw_adj_state = str(getattr(record, "estado_ajuste", "") or "").strip().upper()
    if raw_adj_state in PENDING_ADJUSTMENT_STATES:
        return True, f"El ajuste asociado se encuentra en estado {raw_adj_state}."

    # 3. Colección de ajustes asociada a la instancia
    for attr_name in ("ajustes", "ajustes_servicio", "ajustes_presupuesto"):
        collection = getattr(record, attr_name, None)
        if isinstance(collection, (list, tuple, set)):
            for item in collection:
                if _adjustment_item_is_pending(item):
                    return True, "Existe un ajuste pendiente de aprobación por el cliente."

    # 4. Registro de ajustes pendientes (HU-36 / pruebas)
    for item in _PENDING_ADJUSTMENTS_REGISTRY.get(solicitud_id, []):
        if _adjustment_item_is_pending(item):
            return True, item.get("motivo") or "Existe un ajuste pendiente de aprobación."

    # 5. Modelo persistente de ajustes en base de datos si está definido (HU-36)
    if db is not None and solicitud_id:
        for model_name in ("ServiceAdjustment", "AjusteServicio", "RequestAdjustment"):
            adj_model = getattr(models, model_name, None)
            if adj_model is not None and hasattr(adj_model, "solicitud_id"):
                rows = db.scalars(
                    select(adj_model).where(adj_model.solicitud_id == solicitud_id)
                ).all()
                for row in rows:
                    if _adjustment_item_is_pending(row):
                        return True, "Existe un ajuste pendiente de aprobación en base de datos."

    return False, None


def _require_technician_user(claims: dict, db: Session) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida o expirada",
        )
    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Completa tu registro en TekniGO",
        )
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo las cuentas con rol de técnico pueden reportar trabajo finalizado.",
        )
    return user


def _find_request_for_update(
    db: Session,
    solicitud_id: str,
    *,
    modalidad_hint: str | None = None,
) -> tuple[str, Any] | tuple[None, None]:
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        return None, None

    hint = (modalidad_hint or "").strip().upper()
    bind = db.get_bind()
    is_postgres = bind is not None and bind.dialect.name == "postgresql"

    if hint in {"", "TODOS", "DIRECTA"}:
        lookup_id = clean_id
        can_query_direct = True
        if is_postgres:
            try:
                lookup_id = str(uuid.UUID(clean_id))
            except ValueError:
                can_query_direct = False
        if can_query_direct:
            direct_row = db.scalar(
                select(DirectRequest)
                .where(DirectRequest.id == lookup_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if direct_row is not None:
                return "DIRECTA", direct_row

    if hint in {"", "TODOS", "ABIERTA"}:
        open_model = getattr(models, "OpenRequest", OpenRequest)
        if open_model is not None:
            open_row = db.scalar(
                select(open_model)
                .where(open_model.id == clean_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if open_row is not None:
                return "ABIERTA", open_row

    return None, None


def _emit_completion_event_once(
    record: Any,
    *,
    modalidad: str,
    technician: User,
    reported_at: datetime,
    expires_at: datetime,
    comentario: str | None = None,
) -> dict:
    """CA-01, CA-03, CA-04: Registra el evento para el cliente una única vez sin duplicar en reintentos."""
    req_id = str(record.id)
    existing_events = _COMPLETION_EVENTS.get(req_id, [])
    if existing_events:
        return existing_events[0]

    reported_utc = ensure_utc_datetime(reported_at)
    expires_utc = ensure_utc_datetime(expires_at)
    assert reported_utc is not None and expires_utc is not None
    reported_local = format_tegucigalpa_iso(reported_utc)
    expires_local = format_tegucigalpa_iso(expires_utc)

    event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "FINALIZACION_REPORTADA",
        "solicitud_id": req_id,
        "modalidad": modalidad,
        "cliente_id": getattr(record, "cliente_id", None),
        "tecnico_id": getattr(record, "tecnico_id", None),
        "tecnico_nombre": technician.nombre_completo,
        "estado_solicitud": "EN_PROCESO",
        "pendiente_recepcion": True,
        "plazo_confirmacion_horas": CONFIRMATION_WINDOW_HOURS,
        "finalizacion_reportada_en": reported_utc.isoformat(),
        "vencimiento_confirmacion_en": expires_utc.isoformat(),
        "finalizacion_reportada_local": reported_local,
        "vencimiento_confirmacion_local": expires_local,
        "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
        "comentario": (comentario or "").strip() or None,
        "emitido_en": reported_utc.isoformat(),
        "mensaje": (
            f"{technician.nombre_completo} reportó la finalización del servicio. "
            f"Tienes {CONFIRMATION_WINDOW_HOURS} horas (hasta {expires_local}) "
            "para confirmar la recepción o abrir una incidencia."
        ),
    }
    _COMPLETION_EVENTS[req_id].append(event)
    return event


def report_service_completion(
    solicitud_id: str,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
    comentario: str | None = None,
    modalidad_hint: str | None = None,
) -> dict:
    """HU-34 ST-02 (CA-01 a CA-05): Reporta finalización de forma idempotente y valida ajustes pendientes."""
    technician = _require_technician_user(claims, db)
    modalidad, record = _find_request_for_update(
        db,
        solicitud_id,
        modalidad_hint=modalidad_hint,
    )
    if record is None or modalidad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró el servicio o solicitud especificada.",
        )

    # CA-01: Solo el técnico seleccionado puede reportar finalización
    assigned_tech = getattr(record, "tecnico_id", None)
    if not assigned_tech or assigned_tech not in (technician.firebase_uid, str(technician.id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el técnico seleccionado para el servicio puede reportar su finalización.",
        )

    # CA-01: Solo se puede reportar finalización desde el estado En proceso
    raw_estado = (getattr(record, "estado", None) or "").strip()
    if not is_in_progress_state(raw_estado):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Solo puedes reportar la finalización cuando el servicio está En proceso "
                f"(estado actual: {raw_estado or 'DESCONOCIDO'})."
            ),
        )

    # CA-05: Si existe un ajuste pendiente de aprobación, no se permite reportar como terminado hasta resolverlo
    pending_adj, pending_reason = has_pending_adjustment(record, db)
    if pending_adj:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No se permite reportar el servicio como terminado mientras exista un ajuste "
                f"pendiente de aprobación. {pending_reason or ''}".strip()
            ),
        )

    reference_now = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert reference_now is not None

    already_reported = (
        getattr(record, "finalizacion_reportada_en", None) is not None
        and getattr(record, "vencimiento_confirmacion_en", None) is not None
    )

    if already_reported:
        # CA-04: Reportar de nuevo no reinicia el plazo ni duplica eventos
        reported_dt = ensure_utc_datetime(record.finalizacion_reportada_en)
        expires_dt = ensure_utc_datetime(record.vencimiento_confirmacion_en)
        assert reported_dt is not None and expires_dt is not None
        if not getattr(record, "pendiente_recepcion", False):
            record.pendiente_recepcion = True
            db.commit()
            db.refresh(record)
        event = _emit_completion_event_once(
            record,
            modalidad=modalidad,
            technician=technician,
            reported_at=reported_dt,
            expires_at=expires_dt,
            comentario=comentario,
        )
        reintento_idempotente = True
    else:
        # CA-01, CA-02, CA-03: Registra instante, inicia plazo de 48h y conserva En proceso con pendiente_recepcion
        record.registrar_finalizacion_reportada(
            now=reference_now,
            window_hours=CONFIRMATION_WINDOW_HOURS,
        )
        db.commit()
        db.refresh(record)

        reported_dt = ensure_utc_datetime(record.finalizacion_reportada_en)
        expires_dt = ensure_utc_datetime(record.vencimiento_confirmacion_en)
        assert reported_dt is not None and expires_dt is not None
        event = _emit_completion_event_once(
            record,
            modalidad=modalidad,
            technician=technician,
            reported_at=reported_dt,
            expires_at=expires_dt,
            comentario=comentario,
        )
        reintento_idempotente = False

    reported_local = format_tegucigalpa_iso(reported_dt)
    expires_local = format_tegucigalpa_iso(expires_dt)
    events = get_completion_events(str(record.id))

    return {
        "id": str(record.id),
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado": "EN_PROCESO",
        "estado_etiqueta": "En proceso (Pendiente de recepción)",
        "pendiente_recepcion": True,
        "completada": False,
        "fondos_liberados": False,
        "estado_fondos": "RETENIDOS_PENDIENTE_CONFIRMACION",
        "plazo_confirmacion_horas": CONFIRMATION_WINDOW_HOURS,
        "finalizacion_reportada_en": reported_dt.isoformat(),
        "vencimiento_confirmacion_en": expires_dt.isoformat(),
        "finalizacion_reportada_local": reported_local,
        "vencimiento_confirmacion_local": expires_local,
        "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
        "cliente_id": getattr(record, "cliente_id", None),
        "tecnico_id": getattr(record, "tecnico_id", None),
        "notificacion_cliente": event,
        "evento": event,
        "eventos": events,
        "reintento_idempotente": reintento_idempotente,
        "mensaje": (
            "La finalización ya había sido reportada; se conserva el plazo original de confirmación."
            if reintento_idempotente
            else (
                "Finalización reportada exitosamente. El servicio permanece En proceso "
                "pendiente de recepción y se inició el plazo de 48 horas para confirmación."
            )
        ),
    }


@router.post("/technicians/me/requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
@router.patch("/technicians/me/requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
@router.post("/requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
def report_service_completion_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ReportServiceCompletionSchema | None = None,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return report_service_completion(
        solicitud_id,
        claims,
        db,
        now=payload.now if payload else None,
        comentario=payload.comentario if payload else None,
    )


@router.post("/technicians/me/direct-requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
@router.patch("/technicians/me/direct-requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
@router.post("/direct-requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
def report_direct_request_completion_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ReportServiceCompletionSchema | None = None,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return report_service_completion(
        solicitud_id,
        claims,
        db,
        now=payload.now if payload else None,
        comentario=payload.comentario if payload else None,
        modalidad_hint="DIRECTA",
    )


@router.post("/open-requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
@router.patch("/open-requests/{solicitud_id}/report-completion", status_code=status.HTTP_200_OK)
def report_open_request_completion_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ReportServiceCompletionSchema | None = None,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return report_service_completion(
        solicitud_id,
        claims,
        db,
        now=payload.now if payload else None,
        comentario=payload.comentario if payload else None,
        modalidad_hint="ABIERTA",
    )


def _emit_auto_close_request_event_once(
    record: Any,
    *,
    modalidad: str,
    evaluated_at: datetime,
    proceso_responsable: str,
) -> tuple[dict, bool]:
    """Emite de forma idempotente la solicitud de cierre automático tras vencer las 48h sin incidencias."""
    req_id = str(record.id)
    existing = _AUTO_CLOSE_REQUEST_EVENTS.get(req_id, [])
    if existing:
        return existing[0], False

    eval_utc = ensure_utc_datetime(evaluated_at)
    reported_utc = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
    expires_utc = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
    assert eval_utc is not None
    event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "SOLICITUD_CIERRE_AUTOMATICO",
        "solicitud_id": req_id,
        "modalidad": modalidad,
        "cliente_id": getattr(record, "cliente_id", None),
        "tecnico_id": getattr(record, "tecnico_id", None),
        "estado_solicitud": "EN_PROCESO",
        "pendiente_recepcion": True,
        "tiene_incidencia_abierta": False,
        "tiene_ajuste_pendiente": False,
        "finalizacion_reportada_en": reported_utc.isoformat() if reported_utc else None,
        "vencimiento_confirmacion_en": expires_utc.isoformat() if expires_utc else None,
        "finalizacion_reportada_local": format_tegucigalpa_iso(reported_utc),
        "vencimiento_confirmacion_local": format_tegucigalpa_iso(expires_utc),
        "solicitado_en": eval_utc.isoformat(),
        "solicitado_en_local": format_tegucigalpa_iso(eval_utc),
        "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
        "proceso_responsable": proceso_responsable,
        "mensaje": (
            "Plazo de confirmación de 48 horas vencido sin incidencias activas. "
            "Se solicita el cierre automático del servicio."
        ),
    }
    _AUTO_CLOSE_REQUEST_EVENTS[req_id].append(event)
    return event, True


def _collect_reported_in_progress_candidates(
    db: Session,
    *,
    modalidad: str = "TODOS",
) -> list[tuple[str, Any]]:
    norm_mod = (modalidad or "TODOS").strip().upper()
    if norm_mod not in {"TODOS", "DIRECTA", "ABIERTA"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Modalidad inválida. Usa TODOS, DIRECTA o ABIERTA.",
        )

    candidates: list[tuple[str, Any]] = []
    if norm_mod in {"TODOS", "DIRECTA"}:
        direct_rows = db.scalars(
            select(DirectRequest)
            .where(
                DirectRequest.finalizacion_reportada_en.is_not(None),
                DirectRequest.vencimiento_confirmacion_en.is_not(None),
            )
            .order_by(DirectRequest.vencimiento_confirmacion_en.asc(), DirectRequest.id.asc())
        ).all()
        for row in direct_rows:
            if is_in_progress_state(getattr(row, "estado", None)):
                candidates.append(("DIRECTA", row))

    open_model = getattr(models, "OpenRequest", OpenRequest)
    if norm_mod in {"TODOS", "ABIERTA"} and open_model is not None and hasattr(open_model, "vencimiento_confirmacion_en"):
        open_rows = db.scalars(
            select(open_model)
            .where(
                open_model.finalizacion_reportada_en.is_not(None),
                open_model.vencimiento_confirmacion_en.is_not(None),
            )
            .order_by(open_model.vencimiento_confirmacion_en.asc(), open_model.id.asc())
        ).all()
        for row in open_rows:
            if is_in_progress_state(getattr(row, "estado", None)):
                candidates.append(("ABIERTA", row))

    candidates.sort(
        key=lambda item: (
            ensure_utc_datetime(getattr(item[1], "vencimiento_confirmacion_en", None))
            or datetime.min.replace(tzinfo=timezone.utc),
            str(getattr(item[1], "id", "")),
        )
    )
    return candidates


def query_expired_completion_reports(
    db: Session,
    *,
    now: datetime | None = None,
    modalidad: str = "TODOS",
    solo_elegibles_cierre: bool = False,
) -> list[dict]:
    """HU-34 ST-04 (CA-06): Identifica reportes con plazo de 48h vencido y comprueba incidencias activas."""
    reference_now = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert reference_now is not None

    candidates = _collect_reported_in_progress_candidates(db, modalidad=modalidad)
    expired_items: list[dict] = []

    for mod, record in candidates:
        reported_dt = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
        expires_dt = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
        if reported_dt is None or expires_dt is None:
            continue
        if expires_dt > reference_now:
            continue

        has_inc, inc_reason = has_active_incident(record, db)
        has_adj, adj_reason = has_pending_adjustment(record, db)
        eligible_for_auto_close = not has_inc and not has_adj

        if solo_elegibles_cierre and not eligible_for_auto_close:
            continue

        motivo_bloqueo: str | None = None
        if has_inc:
            motivo_bloqueo = inc_reason or "Incidencia abierta pendiente de resolución."
        elif has_adj:
            motivo_bloqueo = adj_reason or "Ajuste pendiente de aprobación."

        expired_items.append(
            {
                "id": str(record.id),
                "solicitud_id": str(record.id),
                "modalidad": mod,
                "cliente_id": getattr(record, "cliente_id", None),
                "tecnico_id": getattr(record, "tecnico_id", None),
                "estado": "EN_PROCESO",
                "pendiente_recepcion": True,
                "finalizacion_reportada_en": reported_dt.isoformat(),
                "vencimiento_confirmacion_en": expires_dt.isoformat(),
                "finalizacion_reportada_local": format_tegucigalpa_iso(reported_dt),
                "vencimiento_confirmacion_local": format_tegucigalpa_iso(expires_dt),
                "evaluado_en": reference_now.isoformat(),
                "evaluado_en_local": format_tegucigalpa_iso(reference_now),
                "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
                "plazo_vencido": True,
                "tiene_incidencia_abierta": has_inc,
                "motivo_incidencia": inc_reason,
                "tiene_ajuste_pendiente": has_adj,
                "motivo_ajuste": adj_reason,
                "bloqueado_por_incidencia": has_inc,
                "elegible_cierre_automatico": eligible_for_auto_close,
                "motivo_bloqueo_cierre": motivo_bloqueo,
            }
        )

    return expired_items


def run_scheduled_expired_completions_check(
    db: Session,
    *,
    now: datetime | None = None,
    modalidad: str = "TODOS",
    solicitar_cierre_automatico: bool = True,
    proceso_responsable: str = "TAREA_PROGRAMADA_REPORTES_VENCIDOS",
) -> dict:
    """HU-34 ST-04 (CA-06): Tarea programada que identifica reportes vencidos, comprueba incidencias y solicita cierre automático."""
    reference_now = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert reference_now is not None

    all_in_progress_reported = _collect_reported_in_progress_candidates(db, modalidad=modalidad)
    expired_reports = query_expired_completion_reports(
        db,
        now=reference_now,
        modalidad=modalidad,
        solo_elegibles_cierre=False,
    )

    records_by_id = {str(rec.id): (mod, rec) for mod, rec in all_in_progress_reported}
    elegibles: list[dict] = []
    bloqueados_incidencia: list[dict] = []
    solicitudes_cierre: list[dict] = []

    for item in expired_reports:
        req_id = item["solicitud_id"]
        if item["tiene_incidencia_abierta"]:
            bloqueados_incidencia.append(item)
            continue
        if not item["elegible_cierre_automatico"]:
            continue

        elegibles.append(item)
        if solicitar_cierre_automatico and req_id in records_by_id:
            mod, record = records_by_id[req_id]
            close_event, _ = _emit_auto_close_request_event_once(
                record,
                modalidad=mod,
                evaluated_at=reference_now,
                proceso_responsable=proceso_responsable,
            )
            item["evento_cierre_automatico"] = close_event
            solicitudes_cierre.append(close_event)

    return {
        "fecha_ejecucion": reference_now.isoformat(),
        "fecha_ejecucion_local": format_tegucigalpa_iso(reference_now),
        "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
        "proceso_responsable": proceso_responsable,
        "total_reportes_en_proceso_evaluados": len(all_in_progress_reported),
        "total_vencidos": len(expired_reports),
        "total_elegibles_cierre_automatico": len(elegibles),
        "total_bloqueados_por_incidencia": len(bloqueados_incidencia),
        "reportes_vencidos": expired_reports,
        "elegibles_cierre_automatico": elegibles,
        "bloqueados_por_incidencia": bloqueados_incidencia,
        "solicitudes_cierre_emitidas": solicitudes_cierre,
    }


@router.get("/requests/{solicitud_id}/completion-events", status_code=status.HTTP_200_OK)
def get_request_completion_events_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permite al cliente propietario o al técnico asignado consultar los eventos de finalización y cierre."""
    response.headers["Cache-Control"] = "no-store, private"
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completa tu registro en TekniGO")

    modalidad, record = _find_request_for_update(db, solicitud_id)
    if record is None or modalidad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud")

    is_owner = getattr(record, "cliente_id", None) == user.firebase_uid
    is_assigned_tech = getattr(record, "tecnico_id", None) in (user.firebase_uid, str(user.id))
    is_admin = any(role.rol == "ADMIN" for role in user.roles)
    if not (is_owner or is_assigned_tech or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para consultar los eventos de esta solicitud.",
        )

    reported_dt = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
    expires_dt = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
    return {
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado": _canonical_state_key(getattr(record, "estado", None)),
        "pendiente_recepcion": bool(getattr(record, "pendiente_recepcion", False)),
        "finalizacion_reportada_en": reported_dt.isoformat() if reported_dt else None,
        "vencimiento_confirmacion_en": expires_dt.isoformat() if expires_dt else None,
        "finalizacion_reportada_local": format_tegucigalpa_iso(reported_dt),
        "vencimiento_confirmacion_local": format_tegucigalpa_iso(expires_dt),
        "zona_horaria_usuario": TEGUCIGALPA_TZ_NAME,
        "eventos_finalizacion": get_completion_events(str(record.id)),
        "eventos_cierre_automatico": get_auto_close_request_events(str(record.id)),
    }


@router.post("/requests/completion-reports/scheduled-check", status_code=status.HTTP_200_OK)
@router.post("/technicians/completion-reports/scheduled-check", status_code=status.HTTP_200_OK)
def run_scheduled_expired_completions_check_endpoint(
    response: Response,
    payload: ScheduledExpiredCheckSchema | None = None,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")
    return run_scheduled_expired_completions_check(
        db,
        now=payload.now if payload else None,
        modalidad=payload.modalidad if payload else "TODOS",
        solicitar_cierre_automatico=payload.solicitar_cierre_automatico if payload else True,
        proceso_responsable=(
            payload.proceso_responsable if payload else "TAREA_PROGRAMADA_REPORTES_VENCIDOS"
        ),
    )

