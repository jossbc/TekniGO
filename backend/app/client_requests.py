"""HU-25: Consulta paginada y seguimiento de solicitudes/contrataciones del cliente."""
from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import DirectRequest, Service, Specialty, TechnicianProfile, User
from app.security import get_current_user

router = APIRouter(tags=["Contrataciones del Cliente"])

ALLOWED_MODALIDADES = {"TODOS", "DIRECTA", "ABIERTA"}
ALLOWED_ESTADOS = {
    "TODOS",
    "PUBLICADA",
    "CON OFERTAS",
    "CON_OFERTAS",
    "PENDIENTE",
    "ACEPTADA",
    "RECHAZADA",
    "CANCELADA",
    "COMPLETADA",
}

STATE_TIMELINE_DESCRIPTIONS = {
    "PUBLICADA": "Solicitud abierta publicada y disponible para recibir propuestas.",
    "CON OFERTAS": "Licitación abierta con propuestas de técnicos pendientes de tu decisión.",
    "CON_OFERTAS": "Licitación abierta con propuestas de técnicos pendientes de tu decisión.",
    "PENDIENTE": "Solicitud registrada y pendiente de respuesta.",
    "ACEPTADA": "Propuesta aceptada y trabajo programado con el técnico.",
    "RECHAZADA": "Solicitud rechazada por el técnico.",
    "CANCELADA": "Solicitud cancelada.",
    "COMPLETADA": "Trabajo marcado como completado.",
}

ALLOWED_ACTIONS_BY_STATE: dict[str, list[str]] = {
    "PUBLICADA": ["CANCELAR"],
    "CON OFERTAS": ["CANCELAR"],
    "CON_OFERTAS": ["CANCELAR"],
    "PENDIENTE": ["CANCELAR"],
    "ACEPTADA": ["CANCELAR", "COMPLETAR"],
    "RECHAZADA": [],
    "CANCELADA": [],
    "COMPLETADA": [],
}

ACTION_TARGET_STATE: dict[str, str] = {
    "CANCELAR": "Cancelada",
    "COMPLETAR": "Completada",
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
    value = (estado or "TODOS").strip().upper()
    if value not in ALLOWED_ESTADOS:
        raise HTTPException(
            400,
            "Estado inválido. Usa TODOS, PENDIENTE, ACEPTADA, RECHAZADA, CANCELADA o COMPLETADA.",
        )
    return value


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

    raw_estado = (getattr(record, "estado", None) or "PENDIENTE").strip()
    estado_normalizado = raw_estado.upper()

    return {
        "id": str(record.id),
        "modalidad": modalidad,
        "estado": estado_normalizado,
        "fecha_hora": _iso(getattr(record, "fecha_hora", None)),
        "creada_en": _iso(getattr(record, "creada_en", None)),
        "presupuesto": presupuesto,
        "monto_base": monto_base if monto_base is not None else presupuesto,
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

    # Caso 1: Solo solicitudes directas (o todas cuando aún no existe modelo de abiertas)
    if norm_modalidad == "DIRECTA" or (norm_modalidad == "TODOS" and open_request_model is None):
        query = select(DirectRequest).where(DirectRequest.cliente_id == user.firebase_uid)
        if norm_estado != "TODOS":
            query = query.where(func.upper(DirectRequest.estado) == norm_estado)

        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(DirectRequest.creada_en.desc(), DirectRequest.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        services_map = _resolve_service_map(db, {r.servicio_id for r in rows if r.servicio_id})
        tech_map = _resolve_technician_map(db, {r.tecnico_id for r in rows if r.tecnico_id})
        items = [
            _serialize_request_summary(
                row,
                modalidad="DIRECTA",
                services_by_id=services_map,
                technicians_by_uid=tech_map,
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

    # Caso 2: Solo solicitudes abiertas cuando el modelo aún no está registrado
    if norm_modalidad == "ABIERTA" and open_request_model is None:
        return {
            "total": 0,
            "offset": offset,
            "limit": limit,
            "modalidad": norm_modalidad,
            "estado": norm_estado,
            "solicitudes": [],
        }

    # Caso 3: Existe modelo de solicitudes abiertas (ABIERTA o TODOS combinando ambas modalidades)
    combined: list[tuple[str, Any]] = []
    if norm_modalidad in {"TODOS", "DIRECTA"}:
        dq = select(DirectRequest).where(DirectRequest.cliente_id == user.firebase_uid)
        if norm_estado != "TODOS":
            dq = dq.where(func.upper(DirectRequest.estado) == norm_estado)
        for row in db.scalars(dq).all():
            combined.append(("DIRECTA", row))

    if norm_modalidad in {"TODOS", "ABIERTA"} and open_request_model is not None:
        oq = select(open_request_model).where(open_request_model.cliente_id == user.firebase_uid)
        if norm_estado != "TODOS":
            oq = oq.where(func.upper(open_request_model.estado) == norm_estado)
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
    items = [
        _serialize_request_summary(
            row,
            modalidad=mod,
            services_by_id=services_map,
            technicians_by_uid=tech_map,
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
    estado: str = Query("TODOS", description="Filtro por estado: TODOS, PENDIENTE, ACEPTADA, RECHAZADA, CANCELADA o COMPLETADA"),
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


def _find_request_by_id(db: Session, solicitud_id: str) -> tuple[str, Any] | tuple[None, None]:
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        return None, None

    direct_row = db.scalar(select(DirectRequest).where(DirectRequest.id == clean_id))
    if direct_row is not None:
        return "DIRECTA", direct_row

    open_request_model = getattr(models, "OpenRequest", None)
    if open_request_model is not None:
        open_row = db.scalar(select(open_request_model).where(open_request_model.id == clean_id))
        if open_row is not None:
            return "ABIERTA", open_row

    return None, None


def _build_request_timeline(record: Any, estado_normalizado: str) -> list[dict]:
    creada_en = _iso(getattr(record, "creada_en", None))
    custom_history = getattr(record, "historial", None) or getattr(record, "linea_tiempo", None)
    if isinstance(custom_history, list) and custom_history:
        return [
            {
                "estado": (getattr(ev, "estado", None) or (ev.get("estado") if isinstance(ev, dict) else "PENDIENTE")).strip().upper(),
                "descripcion": getattr(ev, "descripcion", None)
                or (ev.get("descripcion") if isinstance(ev, dict) else None)
                or STATE_TIMELINE_DESCRIPTIONS.get(
                    (getattr(ev, "estado", None) or (ev.get("estado") if isinstance(ev, dict) else "PENDIENTE")).strip().upper(),
                    "Cambio de estado registrado.",
                ),
                "fecha": _iso(getattr(ev, "creado_en", None) or getattr(ev, "fecha", None))
                if not isinstance(ev, dict)
                else ev.get("fecha"),
            }
            for ev in custom_history
        ]

    timeline = [
        {
            "estado": "PUBLICADA" if getattr(record, "modalidad", None) == "ABIERTA" else "PENDIENTE",
            "descripcion": (
                STATE_TIMELINE_DESCRIPTIONS["PUBLICADA"]
                if getattr(record, "modalidad", None) == "ABIERTA"
                else STATE_TIMELINE_DESCRIPTIONS["PENDIENTE"]
            ),
            "fecha": creada_en,
        }
    ]
    initial_state = timeline[0]["estado"]
    if estado_normalizado != initial_state:
        updated_at = _iso(getattr(record, "actualizada_en", None)) or creada_en
        timeline.append(
            {
                "estado": estado_normalizado,
                "descripcion": STATE_TIMELINE_DESCRIPTIONS.get(
                    estado_normalizado,
                    f"Estado actualizado a {estado_normalizado}.",
                ),
                "fecha": updated_at,
            }
        )
    return timeline


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

    summary = _serialize_request_summary(
        record,
        modalidad=modalidad,
        services_by_id=services_map,
        technicians_by_uid=tech_map,
    )

    if summary["tecnico"] is not None and tech_uid in tech_map:
        tech_user = tech_map[tech_uid]
        tech_profile = db.get(TechnicianProfile, tech_user.id)
        summary["tecnico"]["estado_verificacion"] = (
            tech_profile.estado_verificacion if tech_profile is not None else None
        )
        summary["tecnico"]["calificacion_promedio"] = (
            _to_float(tech_profile.calificacion_promedio) if tech_profile is not None else None
        )

    estado_normalizado = summary["estado"]
    summary["propuesta"] = {
        "servicio_id": summary["servicio"]["id"] if summary["servicio"] else None,
        "servicio_nombre": summary["servicio"]["nombre"] if summary["servicio"] else None,
        "cantidad": summary["cantidad"],
        "tarifa_aplicada": summary["tarifa_aplicada"],
        "unidad_cobro": summary["unidad_cobro"],
        "monto_pactado": summary["presupuesto"],
        "metodo_pago": summary["metodo_pago"],
    }
    summary["linea_tiempo"] = _build_request_timeline(record, estado_normalizado)
    summary["acciones_permitidas"] = list(ALLOWED_ACTIONS_BY_STATE.get(estado_normalizado, []))

    proposal_model = getattr(models, "OpenRequestProposal", None)
    if modalidad == "ABIERTA" and proposal_model is not None:
        from app.open_requests import get_open_request_events, serialize_open_request_proposal

        proposals = db.scalars(
            select(proposal_model)
            .where(proposal_model.solicitud_id == str(record.id))
            .order_by(proposal_model.creada_en.desc(), proposal_model.id.desc())
        ).all()
        summary["propuestas_recibidas"] = [serialize_open_request_proposal(p, db) for p in proposals]
        summary["eventos"] = get_open_request_events(str(record.id))
    else:
        summary["propuestas_recibidas"] = []
        summary["eventos"] = []

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
    if norm_accion not in ACTION_TARGET_STATE:
        raise HTTPException(400, "Acción inválida. Usa CANCELAR o COMPLETAR.")

    modalidad, record = _find_request_by_id(db, solicitud_id)
    if record is None or modalidad is None:
        raise HTTPException(404, "No se encontró la solicitud")

    if getattr(record, "cliente_id", None) != user.firebase_uid:
        raise HTTPException(403, "No tienes permiso para modificar esta solicitud")

    estado_actual = (getattr(record, "estado", None) or "PENDIENTE").strip().upper()
    permitidas = ALLOWED_ACTIONS_BY_STATE.get(estado_actual, [])
    if norm_accion not in permitidas:
        raise HTTPException(
            409,
            f"La acción '{norm_accion}' no está permitida cuando la solicitud está en estado {estado_actual}.",
        )

    record.estado = ACTION_TARGET_STATE[norm_accion]
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
