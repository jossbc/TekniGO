"""HU-22, HU-23, HU-24: Consulta y respuesta de solicitudes directas por parte del técnico."""
from decimal import Decimal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DirectRequest, Service, User
from app.security import get_current_user

router = APIRouter(prefix="/technicians", tags=["Solicitudes Directas del Técnico"])


class TechnicianDirectRequestOut(BaseModel):
    id: str
    cliente_nombre: str
    servicio_nombre: str
    fecha_hora: str | None
    descripcion: str
    estado: str
    total_estimado: float


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
    rows: list[DirectRequest],
) -> tuple[dict[str, User], dict[str, Service]]:
    client_uids = {row.cliente_id for row in rows if row.cliente_id}
    clients_by_uid: dict[str, User] = {}
    if client_uids:
        clients = db.scalars(select(User).where(User.firebase_uid.in_(client_uids))).all()
        clients_by_uid = {client.firebase_uid: client for client in clients}

    service_uuids: list[uuid.UUID] = []
    for row in rows:
        raw_service_id = getattr(row, "servicio_id", None)
        if isinstance(raw_service_id, uuid.UUID):
            service_uuids.append(raw_service_id)
        elif isinstance(raw_service_id, str):
            try:
                service_uuids.append(uuid.UUID(raw_service_id))
            except ValueError:
                continue

    services_by_id: dict[str, Service] = {}
    if service_uuids:
        services = db.scalars(select(Service).where(Service.id.in_(service_uuids))).all()
        services_by_id = {str(service.id): service for service in services}

    return clients_by_uid, services_by_id


def _serialize_direct_request(
    row: DirectRequest,
    *,
    clients_by_uid: dict[str, User],
    services_by_id: dict[str, Service],
) -> dict:
    client = clients_by_uid.get(row.cliente_id)
    service_key = str(row.servicio_id) if row.servicio_id is not None else ""
    service = services_by_id.get(service_key)
    total = (
        float(Decimal(str(row.monto_base)).quantize(Decimal("0.01")))
        if row.monto_base is not None
        else 0.0
    )
    estado = (row.estado or "PENDIENTE").strip().upper()

    return {
        "id": str(row.id),
        "cliente_nombre": client.nombre_completo if client is not None else "Cliente",
        "servicio_nombre": service.nombre if service is not None else "Servicio",
        "fecha_hora": row.fecha_hora.isoformat() if row.fecha_hora is not None else None,
        "descripcion": row.descripcion or "",
        "estado": estado,
        "total_estimado": total,
    }


def list_technician_direct_requests(claims: dict, db: Session) -> list[dict]:
    """HU-22: Lista las solicitudes directas dirigidas al técnico autenticado."""
    user = _require_technician_user(claims, db)
    rows = list(
        db.scalars(
            select(DirectRequest)
            .where(DirectRequest.tecnico_id.in_((user.firebase_uid, str(user.id))))
            .order_by(DirectRequest.creada_en.desc(), DirectRequest.id.desc())
        ).all()
    )
    clients_by_uid, services_by_id = _resolve_related_maps(db, rows)
    return [
        _serialize_direct_request(row, clients_by_uid=clients_by_uid, services_by_id=services_by_id)
        for row in rows
    ]


def _get_owned_direct_request(solicitud_id: str, claims: dict, db: Session) -> DirectRequest:
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

    record = db.scalar(select(DirectRequest).where(DirectRequest.id == clean_id))
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
    clients_by_uid, services_by_id = _resolve_related_maps(db, [record])
    return _serialize_direct_request(record, clients_by_uid=clients_by_uid, services_by_id=services_by_id)


def respond_to_direct_request(
    solicitud_id: str,
    target_estado: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-23 / HU-24: Valida propiedad del técnico y actualiza una solicitud pendiente a ACEPTADA o RECHAZADA."""
    record = _get_owned_direct_request(solicitud_id, claims, db)

    current_state = (record.estado or "PENDIENTE").strip().upper()
    if current_state != "PENDIENTE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo puedes responder solicitudes en estado PENDIENTE (estado actual: {current_state}).",
        )

    record.estado = target_estado
    db.commit()
    db.refresh(record)

    clients_by_uid, services_by_id = _resolve_related_maps(db, [record])
    return _serialize_direct_request(record, clients_by_uid=clients_by_uid, services_by_id=services_by_id)


@router.get("/me/direct-requests", response_model=list[TechnicianDirectRequestOut], status_code=status.HTTP_200_OK)
def get_my_direct_requests_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_technician_direct_requests(claims, db)


@router.get("/me/direct-requests/{solicitud_id}", response_model=TechnicianDirectRequestOut, status_code=status.HTTP_200_OK)
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
    return respond_to_direct_request(solicitud_id, "RECHAZADA", claims, db)
