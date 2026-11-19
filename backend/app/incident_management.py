"""HU-51: Gestión de incidencias, tickets bilaterales, evidencias privadas e historial inmutable de actuaciones."""
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import uuid
import copy

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import DirectRequest, IncidentTicketRecord,User
from app.security import get_current_user
import json 
from pathlib import Path
router = APIRouter(tags=["Gestión de Incidencias y Moderación (HU-51)"])

TEGUCIGALPA_TZ = timezone(timedelta(hours=-6), name="America/Tegucigalpa")
CONFIRMATION_WINDOW_HOURS = 48


def _ensure_utc(dt: datetime | None = None) -> datetime:
    target = dt or datetime.now(timezone.utc)
    if target.tzinfo is None:
        return target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc)


def _to_tegucigalpa_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _ensure_utc(dt).astimezone(TEGUCIGALPA_TZ).isoformat()


class TipoIncidenciaEnum(str, Enum):
    INCUMPLIMIENTO_SERVICIO = "INCUMPLIMIENTO_SERVICIO"
    CONDUCTA_INAPROPIADA = "CONDUCTA_INAPROPIADA"
    RIESGO_SEGURIDAD = "RIESGO_SEGURIDAD"
    DESACUERDO_AJUSTE = "DESACUERDO_AJUSTE"
    FUERZA_MAYOR = "FUERZA_MAYOR"
    OTRO = "OTRO"


class PrioridadTicketEnum(str, Enum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    NORMAL = "NORMAL"


class EstadoTicketEnum(str, Enum):
    ABIERTO = "ABIERTO"
    EN_REVISION = "EN_REVISION"
    RESUELTO = "RESUELTO"
    CERRADO = "CERRADO"


# Caché sincronizada con PostgreSQL (tabla tickets_incidencias)
_INCIDENT_TICKETS: dict[str, dict[str, Any]] = {}
_TICKETS_BY_SERVICE: dict[str, list[str]] = {}


def _save_persistent_incidents() -> None:
    """Compatibilidad: la persistencia real se realiza en PostgreSQL mediante _persist_ticket_to_db."""
    return None


def _persist_ticket_to_db(db: Session, ticket_data: dict[str, Any]) -> None:
    """Guarda o actualiza un ticket de incidencia completo en PostgreSQL."""
    tid = ticket_data["ticket_id"]
    row = db.get(IncidentTicketRecord, tid)
    payload_copy = copy.deepcopy(ticket_data)
    creado_dt = datetime.fromisoformat(ticket_data["creado_en"]) if ticket_data.get("creado_en") else datetime.now(timezone.utc)
    actualizado_dt = datetime.fromisoformat(ticket_data["actualizado_en"]) if ticket_data.get("actualizado_en") else datetime.now(timezone.utc)

    if row is None:
        row = IncidentTicketRecord(
            ticket_id=tid,
            solicitud_id=ticket_data["solicitud_id"],
            modalidad=ticket_data.get("modalidad", "DIRECTA"),
            reportante_uid=ticket_data["reportante_uid"],
            contraparte_uid=ticket_data.get("contraparte_uid"),
            rol_reportante=ticket_data["rol_reportante"],
            tipo=ticket_data["tipo"],
            prioridad=ticket_data.get("prioridad", PrioridadTicketEnum.ALTA.value),
            estado=ticket_data.get("estado", EstadoTicketEnum.ABIERTO.value),
            es_apelacion_justificada=bool(ticket_data.get("es_apelacion_justificada", False)),
            falta_computada=bool(ticket_data.get("falta_computada", True)),
            bloquea_cierre_automatico=bool(ticket_data.get("bloquea_cierre_automatico", True)),
            bloquea_liberacion_fondos=bool(ticket_data.get("bloquea_liberacion_fondos", True)),
            payload=payload_copy,
            creado_en=creado_dt,
            actualizado_en=actualizado_dt,
        )
        db.add(row)
    else:
        row.estado = ticket_data.get("estado", row.estado)
        row.prioridad = ticket_data.get("prioridad", row.prioridad)
        row.falta_computada = bool(ticket_data.get("falta_computada", row.falta_computada))
        row.bloquea_cierre_automatico = bool(ticket_data.get("bloquea_cierre_automatico", row.bloquea_cierre_automatico))
        row.bloquea_liberacion_fondos = bool(ticket_data.get("bloquea_liberacion_fondos", row.bloquea_liberacion_fondos))
        row.payload = payload_copy
        row.actualizado_en = actualizado_dt

    db.commit()


def _sync_tickets_from_db(db: Session) -> None:
    """Sincroniza en memoria los expedientes persistidos en PostgreSQL."""
    rows = db.scalars(select(IncidentTicketRecord)).all()
    if not rows:
        return
    for row in rows:
        data = copy.deepcopy(row.payload)
        _INCIDENT_TICKETS[row.ticket_id] = data
        sid = row.solicitud_id
        if sid not in _TICKETS_BY_SERVICE:
            _TICKETS_BY_SERVICE[sid] = []
        if row.ticket_id not in _TICKETS_BY_SERVICE[sid]:
            _TICKETS_BY_SERVICE[sid].append(row.ticket_id)


class CreateIncidentTicketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solicitud_id: str = Field(..., description="ID del servicio contratado objeto del reporte.")
    tipo: TipoIncidenciaEnum = Field(..., description="Clasificación de la incidencia según CA-01.")
    motivo: str = Field(..., min_length=5, max_length=1500, description="Descripción detallada de la situación.")
    evidencias: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Rutas o referencias a evidencias privadas cargadas (fotos, documentos, recibos).",
    )
    es_apelacion_justificada: bool = Field(
        default=False,
        description="Indica si el reporte es una apelación de fuerza mayor o conducta de contraparte (CA-05).",
    )


class AddActuationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observaciones: str = Field(..., min_length=5, max_length=2000, description="Detalle de la actuación registrada.")
    accion: str = Field(..., description="Acción de moderación realizada.")


def _find_request_participants(db: Session, solicitud_id: str) -> tuple[str, str | None, str | None, str]:
    """Obtiene modalidad, cliente_id, tecnico_id y estado actual del servicio."""
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        raise HTTPException(404, "No se encontró el servicio referenciado.")

    direct_row = db.scalar(select(DirectRequest).where(DirectRequest.id == clean_id))
    if direct_row is not None:
        return "DIRECTA", direct_row.cliente_id, direct_row.tecnico_id, str(direct_row.estado or "PENDIENTE").upper()

    open_model = getattr(models, "OpenRequest", None)
    if open_model is not None:
        open_row = db.scalar(select(open_model).where(open_model.id == clean_id))
        if open_row is not None:
            return "ABIERTA", open_row.cliente_id, open_row.tecnico_id, str(open_row.estado or "PENDIENTE").upper()

    raise HTTPException(404, "No se encontró el servicio referenciado.")


def create_incident_ticket(
    payload: CreateIncidentTicketSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-51 ST-01 (CA-01, CA-02, CA-03):
    - Cliente o técnico participante reportan un riesgo, conducta o incumplimiento bilateralmente.
    - Crea el ticket y su expediente privado con prioridad alta vinculada a la administración.
    - Inicializa el historial inmutable de actuaciones (Patrón Memento / Audit Trail).
    """
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completa tu registro en TekniGO")

    solicitud_id = payload.solicitud_id.strip()
    modalidad, cliente_id, tecnico_id, estado_servicio = _find_request_participants(db, solicitud_id)

        # CA-01: Solo los participantes pueden emitir el reporte bilateral (soporta firebase_uid e id interno)
    user_identifiers = {str(user.firebase_uid).strip(), str(user.id).strip()}
    es_cliente = str(cliente_id).strip() in user_identifiers
    es_tecnico = str(tecnico_id).strip() in user_identifiers
    if not (es_cliente or es_tecnico):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el cliente o el técnico participante pueden abrir un reporte sobre este servicio (CA-01).",
        )
    _sync_tickets_from_db(db)
    # Evitar múltiples reportes activos del mismo usuario sobre la misma contratación
    existing_ticket_ids = _TICKETS_BY_SERVICE.get(solicitud_id, [])
    for tid in existing_ticket_ids:
        existing = _INCIDENT_TICKETS.get(tid)
        if existing and existing.get("reportante_uid") == user.firebase_uid:
            if existing.get("estado") in {EstadoTicketEnum.ABIERTO.value, EstadoTicketEnum.EN_REVISION.value}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ya tienes un reporte de incidencia activo para esta solicitud en revisión administrativa.",
                )
    eval_now = _ensure_utc(now)
    ticket_id = f"TICK-{uuid.uuid4().hex[:10].upper()}"
    rol_reportante = "CLIENTE" if es_cliente else "TECNICO"
    contraparte_id = tecnico_id if es_cliente else cliente_id

    # CA-01: Se genera ticket prioritario en administración
    prioridad = PrioridadTicketEnum.ALTA

    actuacion_inicial = {
        "actuacion_id": str(uuid.uuid4()),
        "instante": eval_now.isoformat(),
        "instante_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "autor_uid": user.firebase_uid,
        "autor_rol": rol_reportante,
        "accion": "CREACION_TICKET",
        "observaciones": payload.motivo.strip(),
        "evidencias": list(payload.evidencias),
    }

    ticket_data = {
        "ticket_id": ticket_id,
        "solicitud_id": solicitud_id,
        "modalidad": modalidad,
        "estado_servicio_al_reportar": estado_servicio,
        "reportante_uid": user.firebase_uid,
        "reportante_nombre": user.nombre_completo,
        "rol_reportante": rol_reportante,
        "contraparte_uid": contraparte_id,
        "tipo": payload.tipo.value,
        "motivo": payload.motivo.strip(),
        "prioridad": prioridad.value,
        "estado": EstadoTicketEnum.ABIERTO.value,
        "es_apelacion_justificada": payload.es_apelacion_justificada,
        "falta_computada": not payload.es_apelacion_justificada,
        "bloquea_cierre_automatico": False,
        "bloquea_liberacion_fondos": False,
        "creado_en": eval_now.isoformat(),
        "creado_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "actualizado_en": eval_now.isoformat(),
        "evidencias_privadas": list(payload.evidencias),
        "historial_actuaciones": [actuacion_inicial],
        "resolucion_final": None,
    }

    # CA-03: Si el servicio está en proceso tras finalización reportada, bloquea cierre y fondos
    from app.service_reception import (
        _find_locked_request,
        _save_tracking,
        get_service_reception_tracking,
    )

    _, record = _find_locked_request(db, solicitud_id)
    reception = get_service_reception_tracking(solicitud_id, record=record)
    ticket_data["bloquea_cierre_automatico"] = True
    ticket_data["bloquea_liberacion_fondos"] = True
    reception["incidencia_abierta"] = True
    reception["cierre_automatico_detenido"] = True
    reception["liberacion_fondos_retenida"] = True

    if rol_reportante == "CLIENTE":
        reception["cliente_reporto_incidencia"] = True
    else:
        reception["tecnico_reporto_incidencia"] = True
    reception["es_disputa_cruzada"] = bool(
        reception.get("cliente_reporto_incidencia") and reception.get("tecnico_reporto_incidencia")
    )

    reception["incidencia"] = {
        "incidencia_id": ticket_id,
        "motivo": payload.motivo.strip(),
        "estado": "ABIERTA",
        "reportante_rol": rol_reportante,
        "reportante_uid": user.firebase_uid,
    }
    _save_tracking(record, reception)
    db.commit()

    _INCIDENT_TICKETS[ticket_id] = ticket_data
    if solicitud_id not in _TICKETS_BY_SERVICE:
        _TICKETS_BY_SERVICE[solicitud_id] = []
    _TICKETS_BY_SERVICE[solicitud_id].append(ticket_id)
    _persist_ticket_to_db(db, ticket_data)

    return ticket_data


def append_ticket_actuation(
    ticket_id: str,
    payload: AddActuationSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """HU-51 ST-01 / CA-04: Registra una actuación en el historial inmutable sin borrar las anteriores."""
    _sync_tickets_from_db(db)
    ticket = _INCIDENT_TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket de incidencia no encontrado.")

    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(401, "Sesión inválida o expirada")

    es_admin = any(role.rol == "ADMIN" for role in user.roles)
    es_participante = user.firebase_uid in {ticket["reportante_uid"], ticket["contraparte_uid"]}
    if not (es_admin or es_participante):
        raise HTTPException(403, "No tienes permiso para interactuar con este expediente.")

    eval_now = _ensure_utc(now)
    actuacion = {
        "actuacion_id": str(uuid.uuid4()),
        "instante": eval_now.isoformat(),
        "instante_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "autor_uid": user.firebase_uid,
        "autor_rol": "ADMIN" if es_admin else ("REPORTANTE" if user.firebase_uid == ticket["reportante_uid"] else "CONTRAPARTE"),
        "accion": payload.accion.strip().upper(),
        "observaciones": payload.observaciones.strip(),
    }
    ticket["historial_actuaciones"].append(actuacion)
    ticket["actualizado_en"] = eval_now.isoformat()
    _persist_ticket_to_db(db, ticket)
    return ticket


@router.post("/users/me/incident-tickets", status_code=201)
@router.post("/incident-tickets", status_code=201)
def create_incident_ticket_endpoint(
    payload: CreateIncidentTicketSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return create_incident_ticket(payload, claims, db)


@router.post("/admin/incident-tickets/{ticket_id}/actuations", status_code=200)
@router.post("/incident-tickets/{ticket_id}/actuations", status_code=200)
def append_actuation_endpoint(
    ticket_id: str,
    payload: AddActuationSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return append_ticket_actuation(ticket_id, payload, claims, db)

    # --- HU-51 ST-02: Bandeja administrativa priorizada y consulta segura para participantes (CA-01, CA-02) ---

PRIORITY_WEIGHTS = {
    PrioridadTicketEnum.ALTA.value: 3,
    PrioridadTicketEnum.MEDIA.value: 2,
    PrioridadTicketEnum.NORMAL.value: 1,
}


def list_admin_incident_inbox(
    claims: dict,
    db: Session,
    *,
    estado_filtro: str = "TODOS",
    prioridad_filtro: str = "TODAS",
) -> list[dict[str, Any]]:
    """
    HU-51 ST-02 (CA-01, CA-02):
    Bandeja exclusiva para administradores con ordenamiento determinista por:
    1. Prioridad (ALTA primero)
    2. Bloqueo de cierre automático activo
    3. Fecha de creación más reciente
    """
    _sync_tickets_from_db(db)
    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    if not any(role.rol == "ADMIN" for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Solo administradores autorizados pueden consultar la bandeja de moderación (CA-02).",
        )

    tickets = list(_INCIDENT_TICKETS.values())

    # Filtros opcionales
    if estado_filtro.upper() != "TODOS":
        tickets = [t for t in tickets if t["estado"] == estado_filtro.upper()]
    if prioridad_filtro.upper() != "TODAS":
        tickets = [t for t in tickets if t["prioridad"] == prioridad_filtro.upper()]

    # Ordenamiento por prioridad y criticidad
    tickets.sort(
        key=lambda item: (
            PRIORITY_WEIGHTS.get(item.get("prioridad"), 0),
            1 if item.get("bloquea_cierre_automatico") else 0,
            item.get("creado_en") or "",
        ),
        reverse=True,
    )
        # Ordenamiento por prioridad y criticidad
    tickets.sort(
        key=lambda item: (
            PRIORITY_WEIGHTS.get(item.get("prioridad"), 0),
            1 if item.get("bloquea_cierre_automatico") else 0,
            item.get("creado_en") or "",
        ),
        reverse=True,
    )

    # Detecta si la misma solicitud tiene reporte de ambas partes (Cliente y Técnico)
    for ticket in tickets:
        solicitud_id = ticket.get("solicitud_id")
        sibling_ids = _TICKETS_BY_SERVICE.get(solicitud_id, [])
        roles = {_INCIDENT_TICKETS[sid].get("rol_reportante") for sid in sibling_ids if sid in _INCIDENT_TICKETS}
        ticket["es_disputa_cruzada"] = ("CLIENTE" in roles and "TECNICO" in roles)

    return tickets


def get_incident_ticket_detail(
    ticket_id: str,
    claims: dict,
    db: Session,
) -> dict[str, Any]:
    """
    HU-51 ST-02 (CA-02):
    - Administrador autorizado: recibe el expediente completo, evidencias privadas y actuaciones.
    - Participantes (cliente o técnico del servicio): reciben el estado del ticket y resumen de actuaciones,
      protegiendo evidencias privadas de la contraparte si aplica.
    - Usuario ajeno: 403 Forbidden.
    """
    _sync_tickets_from_db(db)
    ticket = _INCIDENT_TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket de incidencia no encontrado.")

    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    es_admin = any(role.rol == "ADMIN" for role in user.roles)
    es_participante = user.firebase_uid in {ticket["reportante_uid"], ticket["contraparte_uid"]}

    if not (es_admin or es_participante):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Solo los participantes y la administración pueden consultar este reporte (CA-02).",
        )

    # Si es participante pero no admin, devuelve el estado sin exponer evidencias sensibles de terceros
    if not es_admin:
        sanitized = dict(ticket)
        if user.firebase_uid != ticket["reportante_uid"]:
            sanitized["evidencias_privadas"] = []
        return sanitized
    # Patrón Facade / Composite: Si es administrador, enlaza las dos caras de la moneda y unifica el historial
    solicitud_id = ticket.get("solicitud_id")
    sibling_ids = _TICKETS_BY_SERVICE.get(solicitud_id, [])
    sibling_tickets = [_INCIDENT_TICKETS[sid] for sid in sibling_ids if sid in _INCIDENT_TICKETS]

    reporte_cliente = next((t for t in sibling_tickets if t.get("rol_reportante") == "CLIENTE"), None)
    reporte_tecnico = next((t for t in sibling_tickets if t.get("rol_reportante") == "TECNICO"), None)

    actuaciones_unificadas: list[dict[str, Any]] = []
    seen_actuaciones: set[str] = set()
    for sibling in sibling_tickets:
        for act in sibling.get("historial_actuaciones", []):
            act_id = act.get("actuacion_id")
            if act_id and act_id in seen_actuaciones:
                continue
            if act_id:
                seen_actuaciones.add(act_id)
            actuaciones_unificadas.append(act)
    actuaciones_unificadas.sort(key=lambda a: a.get("instante") or "")

    full_ticket = dict(ticket)
    full_ticket["historial_actuaciones"] = actuaciones_unificadas
    full_ticket["es_disputa_cruzada"] = bool(reporte_cliente and reporte_tecnico)
    full_ticket["reporte_cliente"] = reporte_cliente
    full_ticket["reporte_tecnico"] = reporte_tecnico
    full_ticket["tickets_solicitud"] = sibling_tickets
    return full_ticket


def list_service_incident_tickets(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> list[dict[str, Any]]:
    """Devuelve los tickets vinculados a una contratación específica para sus participantes o admin."""
    _sync_tickets_from_db(db)
    clean_id = (solicitud_id or "").strip()
    ticket_ids = _TICKETS_BY_SERVICE.get(clean_id, [])
    return [get_incident_ticket_detail(tid, claims, db) for tid in ticket_ids]


@router.get("/admin/incident-tickets", status_code=200)
def get_admin_incident_inbox_endpoint(
    response: Response,
    estado: str = "TODOS",
    prioridad: str = "TODAS",
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_admin_incident_inbox(claims, db, estado_filtro=estado, prioridad_filtro=prioridad)


@router.get("/admin/incident-tickets/{ticket_id}", status_code=200)
@router.get("/incident-tickets/{ticket_id}", status_code=200)
def get_incident_ticket_endpoint(
    ticket_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_incident_ticket_detail(ticket_id, claims, db)


@router.get("/service-requests/{solicitud_id}/incident-tickets", status_code=200)
def get_service_incident_tickets_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_service_incident_tickets(solicitud_id, claims, db)

    # --- HU-51 ST-03: Resoluciones administrativas, permisos y aprobación de justificaciones (CA-04, CA-05, CA-06, CA-07) ---


class DecisionResolucionEnum(str, Enum):
    COMPLETAR_SERVICIO = "COMPLETAR_SERVICIO"
    DESBLOQUEAR_CONFIRMACION = "DESBLOQUEAR_CONFIRMACION"
    CANCELAR_SERVICIO = "CANCELAR_SERVICIO"
    SUSPENDER_USUARIO = "SUSPENDER_USUARIO"
    CONCILIACION_REEMBOLSO = "CONCILIACION_REEMBOLSO"


class AdminResolveTicketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionResolucionEnum = Field(..., description="Dictamen administrativo del expediente (CA-04).")
    observaciones: str = Field(..., min_length=5, max_length=2000, description="Fundamento de la resolución administrativa.")
    aprobar_justificacion_fuerza_mayor: bool = Field(
        default=False,
        description="Si es True, aprueba la apelación como justificada excluyendo la falta del cómputo sin borrar historial (CA-05).",
    )
    vincular_suspension: bool = Field(
        default=False,
        description="Enlaza a suspensión administrativa preventiva de la cuenta infractora (CA-07).",
    )
    conciliacion_reembolso: bool = Field(
        default=False,
        description="Registra acuerdo de conciliación sin declarar pagos bancarios no confirmados (CA-07).",
    )


# Patrón Strategy (SRP + OCP): Contrato para ejecutar cada tipo de resolución sin acoplar el flujo principal
def _apply_resolution_effect(
    decision: DecisionResolucionEnum,
    ticket: dict[str, Any],
    solicitud_id: str,
    admin_user: User,
    eval_now: datetime,
    db: Session,
) -> dict[str, Any]:
    from app.service_reception import (
        _find_locked_request,
        _save_tracking,
        get_service_reception_tracking,
        emit_and_process_idempotent_completion_event,
    )
    from app.service_completion import resolve_service_incidents

    modalidad, cliente_id, tecnico_id, estado_servicio = _find_request_participants(db, solicitud_id)

    # CA-06: Un reporte no modifica por sí solo un servicio ya Completado o Cancelado ni reescribe importes
    if estado_servicio in {"COMPLETADA", "CANCELADA"}:
        return {
            "servicio_modificado": False,
            "motivo": f"El servicio ya se encontraba en estado final {estado_servicio} y es inmutable (CA-06).",
        }

    # Desbloquear registro de incidencias cruzado
    resolve_service_incidents(solicitud_id, nuevo_estado="RESUELTA")

    # Estrategia 1: COMPLETAR_SERVICIO
    if decision == DecisionResolucionEnum.COMPLETAR_SERVICIO:
        try:
            _, record = _find_locked_request(db, solicitud_id)
            reception = get_service_reception_tracking(solicitud_id, record=record)
            record.estado = "Completada"
            if hasattr(record, "pendiente_recepcion"):
                record.pendiente_recepcion = False

            reception.update(
                {
                    "completada": True,
                    "incidencia_abierta": False,
                    "cierre_automatico_detenido": False,
                    "liberacion_fondos_retenida": False,
                    "fecha_cierre": eval_now.isoformat(),
                    "fecha_cierre_tegucigalpa": _to_tegucigalpa_iso(eval_now),
                    "autor_cierre": "ADMIN",
                    "autor_cierre_uid": admin_user.firebase_uid,
                    "acuerdo_inmutable": True,
                    "resenas_habilitadas": True,
                }
            )
            _save_tracking(record, reception)

            emit_and_process_idempotent_completion_event(
                record,
                modalidad=modalidad,
                autor_cierre="ADMIN",
                closed_at=eval_now,
            )
            db.commit()
            db.refresh(record)
            return {"servicio_modificado": True, "nuevo_estado": "COMPLETADA"}
        except HTTPException:
            return {"servicio_modificado": False, "motivo": "No se pudo bloquear la solicitud para cierre"}

    # Estrategia 2: CANCELAR_SERVICIO
    if decision == DecisionResolucionEnum.CANCELAR_SERVICIO:
        try:
            _, record = _find_locked_request(db, solicitud_id)
            reception = get_service_reception_tracking(solicitud_id, record=record)
            record.estado = "Cancelada"
            if hasattr(record, "pendiente_recepcion"):
                record.pendiente_recepcion = False

            reception.update(
                {
                    "completada": False,
                    "incidencia_abierta": False,
                    "cierre_automatico_detenido": True,
                    "liberacion_fondos_retenida": True,
                    "acuerdo_inmutable": True,
                    "resenas_habilitadas": False,
                }
            )
            _save_tracking(record, reception)
            db.commit()
            db.refresh(record)
            return {"servicio_modificado": True, "nuevo_estado": "CANCELADA"}
        except HTTPException:
            return {"servicio_modificado": False, "motivo": "No se pudo bloquear la solicitud para cancelación"}

    # Estrategia 3: DESBLOQUEAR_CONFIRMACION
    if decision == DecisionResolucionEnum.DESBLOQUEAR_CONFIRMACION:
        try:
            _, record = _find_locked_request(db, solicitud_id)
            reception = get_service_reception_tracking(solicitud_id, record=record)
            reception.update(
                {
                    "incidencia_abierta": False,
                    "cierre_automatico_detenido": False,
                    "liberacion_fondos_retenida": False,
                }
            )
            _save_tracking(record, reception)
            db.commit()
            return {"servicio_modificado": True, "nuevo_estado": "EN_PROCESO_DESBLOQUEADO"}
        except HTTPException:
            return {"servicio_modificado": False, "motivo": "No se pudo desbloquear la solicitud"}

    # Estrategias 4 y 5: SUSPENDER_USUARIO o CONCILIACION_REEMBOLSO (CA-07)
    try:
        _, record = _find_locked_request(db, solicitud_id)
        reception = get_service_reception_tracking(solicitud_id, record=record)
        incidencia_resuelta = dict(reception.get("incidencia") or {})
        incidencia_resuelta.update(
            {
                "estado": "RESUELTA",
                "decision_administrativa": decision.value,
                "resolucion_administrativa": (ticket.get("resolucion_final") or {}).get("observaciones"),
                "resuelta_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
            }
        )
        reception.update(
            {
                "incidencia_abierta": False,
                "tecnico_reporto_incidencia": False,
                "cliente_reporto_incidencia": False,
                "es_disputa_cruzada": False,
                "cierre_automatico_detenido": True,
                "liberacion_fondos_retenida": True,
                "incidencia": incidencia_resuelta,
            }
        )
        _save_tracking(record, reception)
        db.commit()
    except HTTPException:
        pass

    return {
        "servicio_modificado": False,
        "suspension_vinculada": decision == DecisionResolucionEnum.SUSPENDER_USUARIO,
        "conciliacion_reembolso": decision == DecisionResolucionEnum.CONCILIACION_REEMBOLSO,
    }


def resolve_incident_ticket(
    ticket_id: str,
    payload: AdminResolveTicketSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-51 ST-03 (CA-04, CA-05, CA-06, CA-07):
    - Dictamen administrativo con registro de decisión, observaciones, fecha inequívoca y responsable.
    - Patrón Memento: apendiza al historial inmutable sin borrar las actuaciones anteriores.
    - CA-05: Aprueba justificaciones de fuerza mayor excluyendo la falta sin borrar el evento original.
    - CA-07: Vincula suspensión preventiva o conciliación de reembolso sin declarar pagos bancarios no confirmados.
    """
    _sync_tickets_from_db(db)
    ticket = _INCIDENT_TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket de incidencia no encontrado.")

    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    if not any(role.rol == "ADMIN" for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores autorizados pueden emitir resoluciones sobre incidencias (CA-02, CA-04).",
        )

    eval_now = _ensure_utc(now)

    # CA-05: Excluir falta del cómputo si la justificación es aprobada, manteniendo el evento original en historial
    if payload.aprobar_justificacion_fuerza_mayor:
        ticket["falta_computada"] = False
        ticket["justificacion_aprobada"] = True
        ticket["justificacion_aprobada_en"] = eval_now.isoformat()

    resolucion_data = {
        "decision": payload.decision.value,
        "observaciones": payload.observaciones.strip(),
        "administrador_uid": user.firebase_uid,
        "administrador_nombre": user.nombre_completo,
        "resuelto_en": eval_now.isoformat(),
        "resuelto_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "justificacion_fuerza_mayor_aprobada": payload.aprobar_justificacion_fuerza_mayor,
        "suspension_vinculada": payload.vincular_suspension or (payload.decision == DecisionResolucionEnum.SUSPENDER_USUARIO),
        "conciliacion_reembolso": payload.conciliacion_reembolso or (payload.decision == DecisionResolucionEnum.CONCILIACION_REEMBOLSO),
    }

    # Patrón Memento / Audit Trail: Se apendiza la actuación final al historial inmutable
    actuacion_resolucion = {
        "actuacion_id": str(uuid.uuid4()),
        "instante": eval_now.isoformat(),
        "instante_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "autor_uid": user.firebase_uid,
        "autor_rol": "ADMIN",
        "accion": f"RESOLUCION_{payload.decision.value}",
        "observaciones": payload.observaciones.strip(),
    }

    ticket["historial_actuaciones"].append(actuacion_resolucion)
    ticket["resolucion_final"] = resolucion_data
    ticket["estado"] = EstadoTicketEnum.RESUELTO.value
    ticket["actualizado_en"] = eval_now.isoformat()
    ticket["bloquea_cierre_automatico"] = False
    ticket["bloquea_liberacion_fondos"] = False

    # Aplicar efecto sobre la contratación mediante Strategy
    efecto_servicio = _apply_resolution_effect(
        payload.decision,
        ticket,
        ticket["solicitud_id"],
        user,
        eval_now,
        db,
    )
    ticket["efecto_servicio"] = efecto_servicio
    # Sincroniza la resolución en todos los reportes de la misma solicitud si es disputa cruzada
    solicitud_id = ticket.get("solicitud_id")
    for sid in _TICKETS_BY_SERVICE.get(solicitud_id, []):
        other = _INCIDENT_TICKETS.get(sid)
        if other and other.get("estado") in {EstadoTicketEnum.ABIERTO.value, EstadoTicketEnum.EN_REVISION.value}:
            other["estado"] = EstadoTicketEnum.RESUELTO.value
            other["resolucion_final"] = resolucion_data
            other["bloquea_cierre_automatico"] = False
            other["bloquea_liberacion_fondos"] = False
    _persist_ticket_to_db(db, ticket)
    for sid in _TICKETS_BY_SERVICE.get(solicitud_id, []):
        if sid != ticket_id and sid in _INCIDENT_TICKETS:
            _persist_ticket_to_db(db, _INCIDENT_TICKETS[sid])
    return ticket


@router.post("/admin/incident-tickets/{ticket_id}/resolve", status_code=200)
@router.post("/incident-tickets/{ticket_id}/resolve", status_code=200)
def resolve_incident_ticket_endpoint(
    ticket_id: str,
    payload: AdminResolveTicketSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return resolve_incident_ticket(ticket_id, payload, claims, db)

    # --- HU-51 ST-04: Integrar bloqueo de cierre automático y cómputo de faltas (CA-03, CA-05) ---


def has_active_incident_ticket(solicitud_id: str) -> bool:
    """
    Patrón Interceptor / Predicate (HU-51 CA-03):
    Comprueba si existe al menos un ticket de incidencia ABIERTO o EN_REVISION que bloquee
    el cierre automático de 48h o la liberación de fondos para la contratación dada.
    """
    clean_id = (solicitud_id or "").strip()
    ticket_ids = _TICKETS_BY_SERVICE.get(clean_id, [])
    for tid in ticket_ids:
        ticket = _INCIDENT_TICKETS.get(tid)
        if ticket and ticket.get("estado") in {EstadoTicketEnum.ABIERTO.value, EstadoTicketEnum.EN_REVISION.value}:
            if ticket.get("bloquea_cierre_automatico") or ticket.get("bloquea_liberacion_fondos"):
                return True
    return False


def compute_user_incident_penalties(user_uid: str) -> dict[str, Any]:
    """
    Patrón Specification (HU-51 CA-05):
    Calcula el historial de faltas de un usuario excluyendo aquellas aprobadas como
    justificadas (fuerza mayor o conducta de contraparte), sin borrar el evento original.
    """
    clean_uid = (user_uid or "").strip()
    tickets_involucrados = [
        t for t in _INCIDENT_TICKETS.values()
        if t.get("contraparte_uid") == clean_uid or t.get("reportante_uid") == clean_uid
    ]

    faltas_vigentes: list[dict[str, Any]] = []
    justificaciones_excluidas: list[dict[str, Any]] = []

        # Patrón Specification (CA-05): discriminar faltas imputables vs justificaciones aprobadas
    for ticket in tickets_involucrados:
        if ticket.get("estado") != EstadoTicketEnum.RESUELTO.value:
            continue

        es_contraparte = ticket.get("contraparte_uid") == clean_uid
        es_apelante = ticket.get("reportante_uid") == clean_uid and ticket.get("es_apelacion_justificada")

        # Si la justificación fue aprobada (fuerza mayor / conducta contraparte), se excluye del cómputo
        if ticket.get("justificacion_aprobada"):
            if es_apelante or es_contraparte:
                justificaciones_excluidas.append({
                    "ticket_id": ticket["ticket_id"],
                    "tipo": ticket["tipo"],
                    "motivo_justificacion": "Aprobada por fuerza mayor / conducta de contraparte (CA-05)",
                    "fecha_aprobacion": ticket.get("justificacion_aprobada_en"),
                })
        elif es_contraparte and ticket.get("falta_computada", True):
            faltas_vigentes.append({
                "ticket_id": ticket["ticket_id"],
                "tipo": ticket["tipo"],
                "motivo": ticket["motivo"],
                "resolucion": ticket.get("resolucion_final", {}).get("decision"),
                "fecha": ticket.get("resolucion_final", {}).get("resuelto_en"),
            })

    return {
        "usuario_uid": clean_uid,
        "total_faltas_computables": len(faltas_vigentes),
        "faltas_computables": faltas_vigentes,
        "total_justificaciones_excluidas": len(justificaciones_excluidas),
        "justificaciones_excluidas": justificaciones_excluidas,
    }


@router.get("/users/{user_uid}/incident-penalties", status_code=200)
def get_user_incident_penalties_endpoint(
    user_uid: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    es_admin = any(role.rol == "ADMIN" for role in user.roles)
    if not (es_admin or user.firebase_uid == user_uid):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado a penalizaciones de otro usuario.")
    _sync_tickets_from_db(db)
    return compute_user_incident_penalties(user_uid)