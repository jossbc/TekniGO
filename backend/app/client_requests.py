"""HU-25: Consulta paginada y seguimiento de solicitudes/contrataciones del cliente."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import (
    DirectRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.request_states import client_actions, request_timeline, state_key, transition_request
from app.security import get_current_user

router = APIRouter(tags=["Contrataciones del Cliente"])

ALLOWED_MODALIDADES = {"TODOS", "DIRECTA", "ABIERTA"}
ALLOWED_ESTADOS = {
    "TODOS", "PUBLICADA", "CON OFERTAS", "PENDIENTE", "ACEPTADA",
    "EN PROCESO", "RECHAZADA", "CANCELADA", "COMPLETADA",
    "CON_OFERTAS", "EN_PROCESO", "TRABAJOS", "SOLICITUDES",
}


class ClientRequestActionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accion: str


def _require_client_user(claims: dict, db: Session) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")
    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if not any(role.rol == "CLIENTE" for role in user.roles):
        raise HTTPException(403, "Solo cuentas con rol de cliente pueden consultar sus contrataciones")
    return user


def _normalize_modalidad(modalidad: str | None) -> str:
    value = (modalidad or "TODOS").strip().upper()
    if value not in ALLOWED_MODALIDADES:
        raise HTTPException(
            400,
            "Modalidad inválida. Usa TODOS, DIRECTA o ABIERTA.",
        )
    return value


def _normalize_estado(estado: str | None) -> str:
    value = state_key(estado or "TODOS")
    if value not in ALLOWED_ESTADOS:
        raise HTTPException(
            400,
            "Estado de solicitud inválido.",
        )
    return value


JOB_STATES = ("ACEPTADA", "EN PROCESO", "COMPLETADA")


def _filter_request_state(query, column, state):
    if state == "TODOS":
        return query
    normalized = func.replace(func.upper(func.trim(column)), "_", " ")
    if state == "TRABAJOS":
        return query.where(normalized.in_(JOB_STATES))
    if state == "SOLICITUDES":
        return query.where(normalized.not_in(JOB_STATES))
    return query.where(normalized == state)


def _to_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _resolve_service_map(db: Session, raw_service_ids: set[Any]) -> dict[str, Service]:
    if not raw_service_ids:
        return {}
    uuid_ids: list[uuid.UUID] = []
    for raw_id in raw_service_ids:
        if isinstance(raw_id, uuid.UUID):
            uuid_ids.append(raw_id)
        elif isinstance(raw_id, str):
            try:
                uuid_ids.append(uuid.UUID(raw_id))
            except ValueError:
                continue
    if not uuid_ids:
        return {}
    services = db.scalars(select(Service).where(Service.id.in_(uuid_ids))).all()
    return {str(service.id): service for service in services}


def _resolve_technician_map(db: Session, technician_uids: set[str]) -> dict[str, User]:
    clean_uids = [uid for uid in technician_uids if uid]
    if not clean_uids:
        return {}
    users = db.scalars(select(User).where(User.firebase_uid.in_(clean_uids))).all()
    return {u.firebase_uid: u for u in users}


def _serialize_request_summary(
    record: Any,
    *,
    modalidad: str,
    services_by_id: dict[str, Service],
    technicians_by_uid: dict[str, User],
    review_flags: tuple[bool, bool] = (False, False),
) -> dict:
    raw_service_id = getattr(record, "servicio_id", None)
    service_key = str(raw_service_id) if raw_service_id is not None else ""
    service = services_by_id.get(service_key)

    tech_uid = getattr(record, "tecnico_id", None)
    tech_user = technicians_by_uid.get(tech_uid) if tech_uid else None

    monto_base = _to_float(getattr(record, "monto_base", None))
    presupuesto = _to_float(getattr(record, "presupuesto", None))
    if presupuesto is None:
        presupuesto = monto_base

    desglose = getattr(record, "desglose_medios", None)
    monto_ajuste_val = _to_float(getattr(record, "monto_ajuste", 0.0))
    monto_total_val = _to_float(getattr(record, "monto_total", None)) or (presupuesto + (monto_ajuste_val or 0.0))

    raw_estado = (getattr(record, "estado", None) or "PENDIENTE").strip()
    estado_normalizado = state_key(raw_estado)
    finalizacion_dt = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
    vencimiento_dt = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
    from app.service_reception import get_service_reception_tracking

    reception_tracking = get_service_reception_tracking(str(record.id), record=record)
    incidencia_abierta = bool(reception_tracking.get("incidencia_abierta"))
    es_en_proceso = estado_normalizado == "EN PROCESO"
    pendiente_recepcion = (
        es_en_proceso
        and not incidencia_abierta
        and (bool(getattr(record, "pendiente_recepcion", False)) or finalizacion_dt is not None)
    )

    return {
        "id": str(record.id),
        "modalidad": modalidad,
        "estado": "EN_PROCESO" if estado_normalizado == "EN PROCESO" else estado_normalizado,
        "fecha_hora": _iso(getattr(record, "fecha_hora", None)),
        "creada_en": _iso(getattr(record, "creada_en", None)),
        "finalizacion_reportada_en": _iso(finalizacion_dt),
        "vencimiento_confirmacion_en": _iso(vencimiento_dt),
        "finalizacion_reportada_local": format_tegucigalpa_iso(finalizacion_dt),
        "vencimiento_confirmacion_local": format_tegucigalpa_iso(vencimiento_dt),
        "pendiente_recepcion": pendiente_recepcion,
        "incidencia_abierta": incidencia_abierta,
        "presupuesto": presupuesto,
        "monto_base": monto_base if monto_base is not None else presupuesto,
        "monto_ajuste": monto_ajuste_val,
        "metodo_pago_ajuste": getattr(record, "metodo_pago_ajuste", None),
        "monto_total": monto_total_val,
        "referencia_pasarela": getattr(record, "referencia_pasarela", None),
        "desglose_medios": desglose,
        "cantidad": _to_float(getattr(record, "cantidad", None)),
        "tarifa_aplicada": _to_float(getattr(record, "tarifa_aplicada", None)),
        "unidad_cobro": getattr(record, "unidad_cobro", None),
        "metodo_pago": getattr(record, "metodo_pago", None),
        "municipio": getattr(record, "municipio", None),
        "sector": getattr(record, "sector", None),
        "direccion_exacta": getattr(record, "direccion_exacta", None),
        "descripcion": getattr(record, "descripcion", None),
        "servicio": {
            "id": str(service.id),
            "nombre": service.nombre,
            "especialidad_id": str(service.especialidad_id),
        }
        if service is not None
        else ({"id": service_key, "nombre": None, "especialidad_id": None} if service_key else None),
        "tecnico": {
            "usuario_id": str(tech_user.id) if tech_user is not None else None,
            "firebase_uid": tech_uid,
            "nombre_completo": tech_user.nombre_completo if tech_user is not None else None,
        }
        if tech_uid
        else None,
        "mi_resena_enviada": bool(review_flags[0]) if estado_normalizado == "COMPLETADA" else False,
        "evaluacion_cerrada": bool(review_flags[1]) if estado_normalizado == "COMPLETADA" else False,
    }


def list_client_requests(
    claims: dict,
    db: Session,
    *,
    modalidad: str | None = "TODOS",
    estado: str | None = "TODOS",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """HU-25 ST-01: Consulta paginada de solicitudes del cliente por modalidad y estado."""
    if offset < 0:
        raise HTTPException(400, "El parámetro offset no puede ser negativo")
    if limit < 1 or limit > 100:
        raise HTTPException(400, "El parámetro limit debe estar entre 1 y 100")

    user = _require_client_user(claims, db)
    norm_modalidad = _normalize_modalidad(modalidad)
    norm_estado = _normalize_estado(estado)

    open_request_model = getattr(models, "OpenRequest", None)

    # Caso 1: Solo solicitudes directas 
    if norm_modalidad == "DIRECTA" or (norm_modalidad == "TODOS" and open_request_model is None):
        query = select(DirectRequest).where(DirectRequest.cliente_id == user.firebase_uid)
        query = _filter_request_state(query, DirectRequest.estado, norm_estado)

        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(DirectRequest.creada_en.desc(), DirectRequest.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        services_map = _resolve_service_map(db, {r.servicio_id for r in rows if r.servicio_id})
        tech_map = _resolve_technician_map(db, {r.tecnico_id for r in rows if r.tecnico_id})
        from app.reviews import get_review_flags_map

        completed_meta = [
            ("DIRECTA", str(r.id))
            for r in rows
            if state_key(getattr(r, "estado", None) or "") == "COMPLETADA"
        ]
        review_flags_map = get_review_flags_map(db, completed_meta, viewer_role="CLIENTE")
        items = [
            _serialize_request_summary(
                row,
                modalidad="DIRECTA",
                services_by_id=services_map,
                technicians_by_uid=tech_map,
                review_flags=review_flags_map.get(("DIRECTA", str(row.id)), (False, False)),
            )
            for row in rows
        ]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "modalidad": norm_modalidad,
            "estado": norm_estado,
            "solicitudes": items,
        }

    # Caso 2:  solicitudes abiertas
    if norm_modalidad == "ABIERTA" and open_request_model is None:
        return {
            "total": 0,
            "offset": offset,
            "limit": limit,
            "modalidad": norm_modalidad,
            "estado": norm_estado,
            "solicitudes": [],
        }

    # Caso 3:todos combinando ambas modalidades
    combined: list[tuple[str, Any]] = []
    if norm_modalidad in {"TODOS", "DIRECTA"}:
        dq = select(DirectRequest).where(DirectRequest.cliente_id == user.firebase_uid)
        dq = _filter_request_state(dq, DirectRequest.estado, norm_estado)
        for row in db.scalars(dq).all():
            combined.append(("DIRECTA", row))

    if norm_modalidad in {"TODOS", "ABIERTA"} and open_request_model is not None:
        oq = select(open_request_model).where(open_request_model.cliente_id == user.firebase_uid)
        oq = _filter_request_state(oq, open_request_model.estado, norm_estado)
        for row in db.scalars(oq).all():
            combined.append(("ABIERTA", row))

    combined.sort(
        key=lambda item: (
            getattr(item[1], "creada_en", None) or datetime.min,
            str(getattr(item[1], "id", "")),
        ),
        reverse=True,
    )
    total = len(combined)
    page_slice = combined[offset : offset + limit]

    services_map = _resolve_service_map(
        db,
        {getattr(r, "servicio_id", None) for _, r in page_slice if getattr(r, "servicio_id", None)},
    )
    tech_map = _resolve_technician_map(
        db,
        {getattr(r, "tecnico_id", None) for _, r in page_slice if getattr(r, "tecnico_id", None)},
    )
    from app.reviews import get_review_flags_map

    completed_meta = [
        (mod, str(r.id))
        for mod, r in page_slice
        if state_key(getattr(r, "estado", None) or "") == "COMPLETADA"
    ]
    review_flags_map = get_review_flags_map(db, completed_meta, viewer_role="CLIENTE")
    items = [
        _serialize_request_summary(
            row,
            modalidad=mod,
            services_by_id=services_map,
            technicians_by_uid=tech_map,
            review_flags=review_flags_map.get((mod, str(row.id)), (False, False)),
        )
        for mod, row in page_slice
    ]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "modalidad": norm_modalidad,
        "estado": norm_estado,
        "solicitudes": items,
    }


@router.get("/users/me/requests")
@router.get("/direct-requests/me")
def list_my_requests_endpoint(
    response: Response,
    modalidad: str = Query("TODOS", description="Filtro por modalidad: TODOS, DIRECTA o ABIERTA"),
    estado: str = Query("TODOS", description="Filtro por estado de la solicitud"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_client_requests(
        claims,
        db,
        modalidad=modalidad,
        estado=estado,
        offset=offset,
        limit=limit,
    )


def _find_request_by_id(db: Session, solicitud_id: str, *, lock: bool = False) -> tuple[str, Any] | tuple[None, None]:
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        return None, None

    # Las solicitudes directas usan UUID en PostgreSQL, las abiertas usan texto.
    direct_id = clean_id
    if db.get_bind().dialect.name == "postgresql":
        try:
            direct_id = str(uuid.UUID(clean_id))
        except ValueError:
            direct_id = None

    direct_query = select(DirectRequest).where(DirectRequest.id == direct_id)
    if lock:
        direct_query = direct_query.with_for_update().execution_options(populate_existing=True)
    direct_row = db.scalar(direct_query) if direct_id is not None else None
    if direct_row is not None:
        return "DIRECTA", direct_row

    open_request_model = getattr(models, "OpenRequest", None)
    if open_request_model is not None:
        open_query = select(open_request_model).where(open_request_model.id == clean_id)
        if lock:
            open_query = open_query.with_for_update().execution_options(populate_existing=True)
        open_row = db.scalar(open_query)
        if open_row is not None:
            return "ABIERTA", open_row

    return None, None


def get_client_request_detail(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-25 ST-02: Obtiene el detalle de una solicitud verificando que pertenezca al cliente."""
    user = _require_client_user(claims, db)
    modalidad, record = _find_request_by_id(db, solicitud_id)
    if record is None or modalidad is None:
        raise HTTPException(404, "No se encontró la solicitud")

    if getattr(record, "cliente_id", None) != user.firebase_uid:
        raise HTTPException(403, "No tienes permiso para consultar esta solicitud")

    raw_service_id = getattr(record, "servicio_id", None)
    services_map = _resolve_service_map(db, {raw_service_id} if raw_service_id else set())
    tech_uid = getattr(record, "tecnico_id", None)
    tech_map = _resolve_technician_map(db, {tech_uid} if tech_uid else set())
    from app.reviews import get_request_review_flags, get_technician_reputation_stats

    detail_review_flags = (
        get_request_review_flags(db, modalidad, str(record.id), viewer_role="CLIENTE")
        if state_key(getattr(record, "estado", None) or "") == "COMPLETADA"
        else (False, False)
    )

    summary = _serialize_request_summary(
        record,
        modalidad=modalidad,
        services_by_id=services_map,
        technicians_by_uid=tech_map,
        review_flags=detail_review_flags,
    )

    if summary["tecnico"] is not None and tech_uid in tech_map:
        tech_user = tech_map[tech_uid]
        tech_profile = db.get(TechnicianProfile, tech_user.id)
        avg_tech_rating, _ = get_technician_reputation_stats(tech_user.id, db)
        summary["tecnico"]["estado_verificacion"] = (
            tech_profile.estado_verificacion if tech_profile is not None else None
        )
        summary["tecnico"]["calificacion_promedio"] = avg_tech_rating

    summary["propuesta"] = {
        "servicio_id": summary["servicio"]["id"] if summary["servicio"] else None,
        "servicio_nombre": summary["servicio"]["nombre"] if summary["servicio"] else None,
        "cantidad": summary["cantidad"],
        "tarifa_aplicada": summary["tarifa_aplicada"],
        "unidad_cobro": summary["unidad_cobro"],
        "monto_pactado": summary["monto_total"],
        "metodo_pago": summary["metodo_pago"],
        "desglose_medios": summary.get("desglose_medios"),
    }
    from app.service_reception import get_service_reception_tracking
    summary["linea_tiempo"] = request_timeline(db, record)
    summary["acciones_permitidas"] = [] if summary["incidencia_abierta"] else client_actions(record)
    summary["seguimiento_recepcion"] = get_service_reception_tracking(str(record.id), record=record)
    proposal_model = getattr(models, "OpenRequestProposal", None)
    from app.service_completion import get_completion_events

    completion_events = get_completion_events(str(record.id))
    if modalidad == "ABIERTA" and proposal_model is not None:
        from app.open_requests import (
            get_open_request_author_notices,
            get_open_request_award_audits,
            get_open_request_events,
            serialize_open_request_proposal,
        )

        proposals = db.scalars(
            select(proposal_model)
            .where(proposal_model.solicitud_id == str(record.id))
            .order_by(proposal_model.creada_en.desc(), proposal_model.id.desc())
        ).all()
        summary["propuestas_recibidas"] = [serialize_open_request_proposal(p, db) for p in proposals]
        summary["eventos"] = [*get_open_request_events(str(record.id)), *completion_events]
        summary["avisos_autores"] = get_open_request_author_notices(str(record.id))
        summary["auditoria_adjudicacion"] = get_open_request_award_audits(str(record.id))
    else:
        summary["propuestas_recibidas"] = []
        summary["eventos"] = list(completion_events)
        summary["avisos_autores"] = []
        summary["auditoria_adjudicacion"] = []

    return summary


@router.get("/users/me/requests/{solicitud_id}")
@router.get("/direct-requests/me/{solicitud_id}")
def get_my_request_detail_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_client_request_detail(solicitud_id, claims, db)


def apply_client_request_action(
    solicitud_id: str,
    accion: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-25 ST-04: Ejecuta una acción permitida para el cliente según el estado actual en el backend."""
    user = _require_client_user(claims, db)
    norm_accion = (accion or "").strip().upper()
    if norm_accion not in {"CANCELAR", "COMPLETAR"}:
        raise HTTPException(400, "Acción de solicitud inválida.")

    modalidad, record = _find_request_by_id(db, solicitud_id, lock=True)
    if record is None or modalidad is None:
        raise HTTPException(404, "No se encontró la solicitud")

    if record.cliente_id != user.firebase_uid:
        raise HTTPException(403, "No tienes permiso para modificar esta solicitud")

    if norm_accion == "COMPLETAR":
        raise HTTPException(409, "El servicio se completa desde el flujo de confirmación de recepción.")
    if (record.seguimiento_recepcion or {}).get("incidencia_abierta"):
        raise HTTPException(409, "Una incidencia abierta requiere resolución administrativa.")
    transition_request(db, record, "CANCELADA", role="CLIENTE", uid=user.firebase_uid)
    db.commit()
    db.refresh(record)
    return get_client_request_detail(solicitud_id, claims, db)


@router.post("/users/me/requests/{solicitud_id}/actions")
@router.post("/direct-requests/me/{solicitud_id}/actions")
def execute_my_request_action_endpoint(
    solicitud_id: str,
    payload: ClientRequestActionSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return apply_client_request_action(solicitud_id, payload.accion, claims, db)
