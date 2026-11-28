"""HU-22, HU-23, HU-24: Consulta y respuesta de solicitudes directas por parte del técnico."""
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from typing import Any

from app.database import get_db
from app.models import (
    DirectRequest,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.notifications_events import notify_direct_request_decision
from app.request_states import request_timeline, start_request_service, state_key, transition_request
from app.security import get_current_user
from app.service_reception import get_service_reception_tracking
from app.service_completion import has_pending_adjustment
from app.account_suspension_service import assert_technician_can_operate

router = APIRouter(prefix="/technicians", tags=["Solicitudes Directas del Técnico"])


class RequestTimelineItem(BaseModel):
    estado: str
    descripcion: str
    fecha: str | None
    responsable: str | None


class TechnicianDirectRequestOut(BaseModel):
    id: str
    cliente_nombre: str
    servicio_nombre: str
    fecha_hora: str | None
    descripcion: str
    estado: str
    modalidad: str | None = None
    total_estimado: float
    metodo_pago: str | None = None
    referencia_pasarela: str | None = None
    monto_ajuste: float | None = None
    metodo_pago_ajuste: str | None = None
    monto_total: float | None = None
    desglose_medios: dict | None = None
    municipio: str | None = None
    sector: str | None = None
    direccion_exacta: str | None = None
    cliente_telefono: str | None = None
    pendiente_recepcion: bool | None = None
    finalizacion_reportada_en: str | None = None
    vencimiento_confirmacion_en: str | None = None
    finalizacion_reportada_local: str | None = None
    vencimiento_confirmacion_local: str | None = None
    tiene_ajuste_pendiente: bool | None = None
    motivo_ajuste_pendiente: str | None = None
    incidencia_abierta: bool | None = None
    incidencia: dict | None = None
    mi_resena_enviada: bool = False
    evaluacion_cerrada: bool = False


class TechnicianDirectRequestDetail(TechnicianDirectRequestOut):
    modalidad: str
    metodo_pago: str
    linea_tiempo: list[RequestTimelineItem]


def _require_technician_user(claims: dict, db: Session) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completa tu registro en TekniGO")
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo las cuentas con rol de técnico pueden gestionar solicitudes directas.",
        )
    return user


def _resolve_related_maps(
    db: Session,
    rows: list[Any],
) -> tuple[dict[str, User], dict[str, Service], dict[str, Specialty]]:
    client_uids = {row.cliente_id for row in rows if getattr(row, "cliente_id", None)}
    clients_by_uid: dict[str, User] = {}
    if client_uids:
        clients = db.scalars(select(User).where(User.firebase_uid.in_(client_uids))).all()
        clients_by_uid = {client.firebase_uid: client for client in clients}

    service_uuids: list[uuid.UUID] = []
    specialty_uuids: list[uuid.UUID] = []
    for row in rows:
        raw_service_id = getattr(row, "servicio_id", None)
        if isinstance(raw_service_id, uuid.UUID):
            service_uuids.append(raw_service_id)
        elif isinstance(raw_service_id, str):
            try:
                service_uuids.append(uuid.UUID(raw_service_id))
            except ValueError:
                pass
        raw_spec_id = getattr(row, "especialidad_id", None)
        if isinstance(raw_spec_id, uuid.UUID):
            specialty_uuids.append(raw_spec_id)
        elif isinstance(raw_spec_id, str):
            try:
                specialty_uuids.append(uuid.UUID(raw_spec_id))
            except ValueError:
                pass

    services_by_id: dict[str, Service] = {}
    if service_uuids:
        services = db.scalars(select(Service).where(Service.id.in_(service_uuids))).all()
        services_by_id = {str(service.id): service for service in services}

    specialties_by_id: dict[str, Specialty] = {}
    if specialty_uuids:
        specs = db.scalars(select(Specialty).where(Specialty.id.in_(specialty_uuids))).all()
        specialties_by_id = {str(spec.id): spec for spec in specs}

    return clients_by_uid, services_by_id, specialties_by_id


def _serialize_direct_request(
    row: Any,
    *,
    clients_by_uid: dict[str, User],
    services_by_id: dict[str, Service],
    specialties_by_id: dict[str, Specialty] | None = None,
    review_flags: tuple[bool, bool] = (False, False),
) -> dict:
    client = clients_by_uid.get(row.cliente_id)
    service_key = str(row.servicio_id) if getattr(row, "servicio_id", None) is not None else ""
    service = services_by_id.get(service_key)
    spec_key = str(getattr(row, "especialidad_id", "")) if getattr(row, "especialidad_id", None) is not None else ""
    spec = (specialties_by_id or {}).get(spec_key)
    base_val = getattr(row, "monto_base", None) or getattr(row, "presupuesto", None)
    effective_total = getattr(row, "monto_total", None)
    if effective_total is None:
        effective_total = base_val
    total = (
        float(Decimal(str(effective_total)).quantize(Decimal("0.01")))
        if effective_total is not None
        else 0.0
    )
    estado = state_key(row.estado or "PENDIENTE").replace(" ", "_")
    service_label = (
        service.nombre
        if service is not None
        else (f"{spec.nombre} (Licitación abierta)" if spec is not None else "Servicio")
    )

    # Política de privacidad: Dirección exacta y teléfono solo revelados tras aceptar el trabajo
    revelar_contacto = estado in {"ACEPTADA", "EN_PROCESO", "COMPLETADA"}

    payload = {
        "id": str(row.id),
        "cliente_nombre": client.nombre_completo if client is not None else "Cliente",
        "servicio_nombre": service_label,
        "fecha_hora": row.fecha_hora.isoformat() if row.fecha_hora is not None else None,
        "descripcion": row.descripcion or "",
        "estado": estado,
        "modalidad": "ABIERTA" if isinstance(row, OpenRequest) else "DIRECTA",
        "total_estimado": total,
        "metodo_pago": row.metodo_pago,
        "referencia_pasarela": row.referencia_pasarela,
        "monto_ajuste": float(row.monto_ajuste or 0.0),
        "metodo_pago_ajuste": row.metodo_pago_ajuste,
        "monto_total": float(row.monto_total or base_val or 0.0),
        "desglose_medios": row.desglose_medios,
        "municipio": getattr(row, "municipio", None),
        "sector": getattr(row, "sector", None),
        "direccion_exacta": getattr(row, "direccion_exacta", None) if revelar_contacto else None,
        "cliente_telefono": (client.telefono if client is not None else None) if revelar_contacto else None,
    }
    if estado == "COMPLETADA":
        payload["mi_resena_enviada"] = bool(review_flags[0])
        payload["evaluacion_cerrada"] = bool(review_flags[1])

    reception_tracking = get_service_reception_tracking(str(row.id), record=row)
    incidencia_abierta = bool(reception_tracking.get("incidencia_abierta"))
    incidencia_data = reception_tracking.get("incidencia")

    finalizacion_dt = ensure_utc_datetime(getattr(row, "finalizacion_reportada_en", None))
    vencimiento_dt = ensure_utc_datetime(getattr(row, "vencimiento_confirmacion_en", None))
    pendiente_rec = (
        estado == "EN_PROCESO"
        and not incidencia_abierta
        and (bool(getattr(row, "pendiente_recepcion", False)) or finalizacion_dt is not None)
    )
    has_adj, adj_reason = has_pending_adjustment(row, Session.object_session(row))

    if incidencia_abierta or reception_tracking.get("tecnico_reporto_incidencia") or reception_tracking.get("cliente_reporto_incidencia"):
        payload["incidencia_abierta"] = True
        if incidencia_data is not None:
            payload["incidencia"] = reception_tracking.get("incidencia") or {
                key: incidencia_data.get(key)
                for key in ("incidencia_id", "motivo", "estado", "abierta_en_tegucigalpa")
            }
        payload["tecnico_reporto_incidencia"] = bool(reception_tracking.get("tecnico_reporto_incidencia"))
        payload["cliente_reporto_incidencia"] = bool(reception_tracking.get("cliente_reporto_incidencia"))
        payload["es_disputa_cruzada"] = bool(reception_tracking.get("es_disputa_cruzada"))
    if finalizacion_dt is not None or vencimiento_dt is not None or pendiente_rec or has_adj:
        payload["pendiente_recepcion"] = pendiente_rec
        payload["finalizacion_reportada_en"] = finalizacion_dt.isoformat() if finalizacion_dt else None
        payload["vencimiento_confirmacion_en"] = vencimiento_dt.isoformat() if vencimiento_dt else None
        payload["finalizacion_reportada_local"] = format_tegucigalpa_iso(finalizacion_dt)
        payload["vencimiento_confirmacion_local"] = format_tegucigalpa_iso(vencimiento_dt)
        payload["tiene_ajuste_pendiente"] = has_adj
        payload["motivo_ajuste_pendiente"] = adj_reason

    return payload


def list_technician_direct_requests(claims: dict, db: Session) -> list[dict]:
    """HU-22 / HU-31 / HU-34: Lista las solicitudes directas y abiertas adjudicadas al técnico autenticado."""
    user = _require_technician_user(claims, db)
    direct_rows = list(
        db.scalars(
            select(DirectRequest)
            .where(DirectRequest.tecnico_id.in_((user.firebase_uid, str(user.id))))
            .order_by(DirectRequest.creada_en.desc(), DirectRequest.id.desc())
        ).all()
    )
    open_rows = list(
        db.scalars(
            select(OpenRequest)
            .where(OpenRequest.tecnico_id.in_((user.firebase_uid, str(user.id))))
            .order_by(OpenRequest.creada_en.desc(), OpenRequest.id.desc())
        ).all()
    )
    rows: list[Any] = [*direct_rows, *open_rows]
    clients_by_uid, services_by_id, specialties_by_id = _resolve_related_maps(db, rows)
    from app.reviews import get_review_flags_map

    completed_meta = [
        ("ABIERTA" if isinstance(row, OpenRequest) else "DIRECTA", str(row.id))
        for row in rows
        if state_key(row.estado or "PENDIENTE").replace(" ", "_") == "COMPLETADA"
    ]
    review_flags_map = get_review_flags_map(db, completed_meta, viewer_role="TECNICO")
    return [
        _serialize_direct_request(
            row,
            clients_by_uid=clients_by_uid,
            services_by_id=services_by_id,
            specialties_by_id=specialties_by_id,
            review_flags=review_flags_map.get(
                ("ABIERTA" if isinstance(row, OpenRequest) else "DIRECTA", str(row.id)),
                (False, False),
            ),
        )
        for row in rows
    ]


def _get_owned_direct_request(solicitud_id: str, claims: dict, db: Session, *, lock: bool = False) -> DirectRequest:
    user = _require_technician_user(claims, db)
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud directa.")

    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        try:
            clean_id = str(uuid.UUID(clean_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud directa.") from None

    query = select(DirectRequest).where(DirectRequest.id == clean_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    record = db.scalar(query)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud directa.")

    if record.tecnico_id not in (user.firebase_uid, str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para modificar esta solicitud.",
        )
    return record


def get_technician_direct_request_detail(solicitud_id: str, claims: dict, db: Session) -> dict:
    """HU-22: Obtiene el detalle de una solicitud directa dirigida al técnico autenticado."""
    record = _get_owned_direct_request(solicitud_id, claims, db)
    clients_by_uid, services_by_id, specialties_by_id = _resolve_related_maps(db, [record])
    from app.reviews import get_request_review_flags

    detail_review_flags = (
        get_request_review_flags(db, "DIRECTA", str(record.id), viewer_role="TECNICO")
        if state_key(record.estado or "PENDIENTE").replace(" ", "_") == "COMPLETADA"
        else (False, False)
    )
    detail = _serialize_direct_request(
        record,
        clients_by_uid=clients_by_uid,
        services_by_id=services_by_id,
        specialties_by_id=specialties_by_id,
        review_flags=detail_review_flags,
    )
    detail["linea_tiempo"] = request_timeline(db, record)
    return detail


@router.post("/me/direct-requests/{solicitud_id}/start", response_model=TechnicianDirectRequestDetail)
def start_my_direct_request_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    record = _get_owned_direct_request(solicitud_id, claims, db, lock=True)
    start_request_service(db, record, claims["uid"])
    return get_technician_direct_request_detail(solicitud_id, claims, db)


def respond_to_direct_request(
    solicitud_id: str,
    target_estado: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-23 / HU-24: Valida propiedad del técnico y actualiza una solicitud pendiente a ACEPTADA o CANCELADA."""
    record = _get_owned_direct_request(solicitud_id, claims, db, lock=True)

    current_state = state_key(record.estado)
    if current_state != "PENDIENTE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo puedes responder solicitudes en estado PENDIENTE (estado actual: {current_state}).",
        )
    # HU-37 CA-03: Con efectivo no se invoca cobro digital; con tarjeta se solicita pago antes de Aceptada
    if target_estado == "ACEPTADA":
        tech_user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]).with_for_update())
        profile = db.get(TechnicianProfile, tech_user.id, populate_existing=True) if tech_user else None
        if tech_user is not None:
            assert_technician_can_operate(db, tech_user, profile, action_label="aceptar trabajos")
        metodo = (record.metodo_pago or "").strip().lower()
        if metodo == "tarjeta":
            from app.payment_lifecycle_bridge import verify_confirmed_base_payment_for_acceptance

            verify_confirmed_base_payment_for_acceptance(
                db,
                modalidad="DIRECTA",
                solicitud_id=str(record.id),
                record=record,
            )
    transition_request(db, record, target_estado, role="TECNICO", uid=claims["uid"])
    notify_direct_request_decision(db, record, accepted=(target_estado == "ACEPTADA"))
    db.commit()
    db.refresh(record)

    clients_by_uid, services_by_id, specialties_by_id = _resolve_related_maps(db, [record])
    return _serialize_direct_request(
        record,
        clients_by_uid=clients_by_uid,
        services_by_id=services_by_id,
        specialties_by_id=specialties_by_id,
    )


def start_technician_request_execution(solicitud_id: str, claims: dict, db: Session) -> dict:
    """Pasa una solicitud directa o abierta adjudicada de ACEPTADA a EN_PROCESO para poder ejecutarla y reportar finalización."""
    user = _require_technician_user(claims, db)
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud.")

    bind = db.get_bind()
    is_postgres = bind is not None and bind.dialect.name == "postgresql"
    record: Any = None

    lookup_id = clean_id
    can_query_direct = True
    if is_postgres:
        try:
            lookup_id = str(uuid.UUID(clean_id))
        except ValueError:
            can_query_direct = False
    if can_query_direct:
        record = db.scalar(select(DirectRequest).where(DirectRequest.id == lookup_id).with_for_update().execution_options(populate_existing=True))

    if record is None:
        record = db.scalar(select(OpenRequest).where(OpenRequest.id == clean_id).with_for_update().execution_options(populate_existing=True))

    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró la solicitud.")

    if getattr(record, "tecnico_id", None) not in (user.firebase_uid, str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para modificar esta solicitud.",
        )

    current_state = (getattr(record, "estado", None) or "").strip().upper()
    if current_state in {"EN PROCESO", "EN_PROCESO"}:
        clients_by_uid, services_by_id, specialties_by_id = _resolve_related_maps(db, [record])
        return _serialize_direct_request(
            record,
            clients_by_uid=clients_by_uid,
            services_by_id=services_by_id,
            specialties_by_id=specialties_by_id,
        )

    if current_state not in {"ACEPTADA", "ASIGNADA"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo puedes iniciar la ejecución de una solicitud en estado ACEPTADA (estado actual: {current_state}).",
        )

    start_request_service(db, record, user.firebase_uid)
    db.refresh(record)

    clients_by_uid, services_by_id, specialties_by_id = _resolve_related_maps(db, [record])
    return _serialize_direct_request(
        record,
        clients_by_uid=clients_by_uid,
        services_by_id=services_by_id,
        specialties_by_id=specialties_by_id,
    )


@router.get("/me/direct-requests", response_model=list[TechnicianDirectRequestOut], status_code=status.HTTP_200_OK)
def get_my_direct_requests_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_technician_direct_requests(claims, db)


@router.get("/me/direct-requests/{solicitud_id}", response_model=TechnicianDirectRequestDetail, status_code=status.HTTP_200_OK)
def get_my_direct_request_detail_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_technician_direct_request_detail(solicitud_id, claims, db)


@router.patch(
    "/me/direct-requests/{solicitud_id}/accept",
    response_model=TechnicianDirectRequestOut,
    status_code=status.HTTP_200_OK,
)
def accept_my_direct_request_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return respond_to_direct_request(solicitud_id, "ACEPTADA", claims, db)


@router.patch(
    "/me/direct-requests/{solicitud_id}/reject",
    response_model=TechnicianDirectRequestOut,
    status_code=status.HTTP_200_OK,
)
def reject_my_direct_request_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return respond_to_direct_request(solicitud_id, "CANCELADA", claims, db)


@router.patch(
    "/me/direct-requests/{solicitud_id}/start",
    response_model=TechnicianDirectRequestOut,
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/me/requests/{solicitud_id}/start",
    response_model=TechnicianDirectRequestOut,
    status_code=status.HTTP_200_OK,
)
def start_my_request_execution_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return start_technician_request_execution(solicitud_id, claims, db)


from app.service_completion import (  # noqa: E402, F401
    report_direct_request_completion_endpoint,
    report_service_completion,
    report_service_completion_endpoint,
)
