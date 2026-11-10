"""HU-26: Publicación de solicitudes abiertas (licitaciones), rango sugerido y visibilidad por elegibilidad."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    CoverageZone,
    IdentityDocument,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    TechnicianSpecialty,
    User,
)
from app.permissions import require_enabled_client
from app.security import get_current_user
from app.services import VALID_UNITS
from app.technician_activation import is_operationally_enabled, require_operational_technician

router = APIRouter(prefix="/open-requests", tags=["Solicitudes Abiertas"])

VALID_PAYMENT_METHODS = {"efectivo", "tarjeta"}
MAX_BUDGET_AMOUNT = Decimal("999999.99")
DEFAULT_RANGE_MARGIN = Decimal("0.15")  # ±15% alrededor de la tarifa base promedio (parametrizable)


class CreateOpenRequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    especialidad_id: uuid.UUID
    descripcion: str
    municipio: str
    sector: str
    direccion_exacta: str
    fecha_hora: datetime
    metodo_pago: str
    presupuesto: Decimal

    @model_validator(mode="before")
    @classmethod
    def accept_monto_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "presupuesto" not in data:
            for alias in ("monto", "monto_ofrecido", "monto_base"):
                if alias in data:
                    copied = dict(data)
                    copied["presupuesto"] = copied.pop(alias)
                    return copied
        return data


def require_verified_unsuspended_client(claims: dict, db: Session) -> User:
    """CA-01: Solo un cliente verificado y no suspendido puede publicar una solicitud abierta."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(
        select(User)
        .where(User.firebase_uid == uid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    if not any(role.rol == "CLIENTE" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de cliente")

    tech_profile = db.get(TechnicianProfile, user.id)
    if getattr(user, "suspendido", False) or (tech_profile is not None and tech_profile.suspendido):
        raise HTTPException(403, "Tu cuenta presenta una suspensión activa y no puede publicar solicitudes")

    return require_enabled_client(user)


def validate_open_request_data(
    payload: CreateOpenRequestSchema,
    db: Session,
    *,
    now: datetime | None = None,
) -> tuple[Specialty, dict]:
    """CA-01 y CA-07: Valida los datos obligatorios antes de publicar la solicitud abierta."""
    specialty = db.get(Specialty, payload.especialidad_id)
    if specialty is None:
        raise HTTPException(400, "La especialidad seleccionada no existe")

    descripcion = (payload.descripcion or "").strip()
    if len(descripcion) < 10:
        raise HTTPException(400, "La descripción debe tener al menos 10 caracteres")
    if len(descripcion) > 2000:
        raise HTTPException(400, "La descripción no puede superar los 2000 caracteres")

    municipio = " ".join((payload.municipio or "").split())
    if not municipio:
        raise HTTPException(400, "El municipio es obligatorio")
    if len(municipio) > 80:
        raise HTTPException(400, "El municipio no puede superar los 80 caracteres")

    sector = " ".join((payload.sector or "").split())
    if not sector:
        raise HTTPException(400, "El sector es obligatorio")
    if len(sector) > 100:
        raise HTTPException(400, "El sector no puede superar los 100 caracteres")

    direccion_exacta = (payload.direccion_exacta or "").strip()
    if len(direccion_exacta) < 5:
        raise HTTPException(400, "La dirección exacta es obligatoria y debe tener al menos 5 caracteres")
    if len(direccion_exacta) > 500:
        raise HTTPException(400, "La dirección exacta no puede superar los 500 caracteres")

    fecha_hora = payload.fecha_hora
    if fecha_hora.tzinfo is None:
        fecha_hora = fecha_hora.replace(tzinfo=timezone.utc)
    reference_now = now or datetime.now(timezone.utc)
    if fecha_hora <= reference_now:
        raise HTTPException(400, "La fecha y hora deben ser futuras")

    metodo_pago = (payload.metodo_pago or "").strip().lower()
    if metodo_pago not in VALID_PAYMENT_METHODS:
        raise HTTPException(400, "Método de pago inválido. Elige efectivo o tarjeta")

    try:
        raw_amount = Decimal(str(payload.presupuesto))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "El monto ofrecido debe ser un número válido") from None

    if not raw_amount.is_finite() or raw_amount <= Decimal("0"):
        raise HTTPException(400, "El monto ofrecido debe ser mayor que cero")
    if raw_amount > MAX_BUDGET_AMOUNT:
        raise HTTPException(400, "El monto ofrecido no puede exceder L. 999,999.99")

    presupuesto = raw_amount.quantize(Decimal("0.01"))

    return specialty, {
        "especialidad_id": str(specialty.id),
        "descripcion": descripcion,
        "municipio": municipio,
        "sector": sector,
        "direccion_exacta": direccion_exacta,
        "fecha_hora": fecha_hora,
        "metodo_pago": metodo_pago,
        "presupuesto": presupuesto,
        "monto_base": presupuesto,
    }


def serialize_open_request_for_owner(record: OpenRequest, specialty: Specialty | None = None) -> dict:
    presupuesto_val = float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01")))
    return {
        "id": str(record.id),
        "solicitud_id": str(record.id),
        "modalidad": "ABIERTA",
        "estado": (record.estado or "Publicada").strip().upper(),
        "cliente_id": record.cliente_id,
        "tecnico_id": record.tecnico_id,
        "especialidad_id": str(record.especialidad_id),
        "especialidad": (
            {"id": str(specialty.id), "nombre": specialty.nombre}
            if specialty is not None
            else {"id": str(record.especialidad_id), "nombre": None}
        ),
        "descripcion": record.descripcion,
        "municipio": record.municipio,
        "sector": record.sector,
        "direccion_exacta": record.direccion_exacta,
        "fecha_hora": record.fecha_hora.isoformat() if record.fecha_hora else None,
        "metodo_pago": record.metodo_pago,
        "presupuesto": presupuesto_val,
        "monto_base": presupuesto_val,
        "creada_en": record.creada_en.isoformat() if record.creada_en else None,
    }


def publish_open_request(
    payload: CreateOpenRequestSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """CA-01, CA-02, CA-07: Publica una solicitud abierta en estado Publicada sin técnico adjudicado."""
    client = require_verified_unsuspended_client(claims, db)
    specialty, validated = validate_open_request_data(payload, db, now=now)

    record = OpenRequest(
        id=str(uuid.uuid4()),
        cliente_id=client.firebase_uid,
        tecnico_id=None,
        modalidad="ABIERTA",
        estado="Publicada",
        creada_en=now or datetime.now(timezone.utc),
        **validated,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    data = serialize_open_request_for_owner(record, specialty)
    data["mensaje"] = "Solicitud abierta publicada exitosamente"
    return data


@router.post("", status_code=201)
@router.post("/", status_code=201)
def publish_open_request_endpoint(
    payload: CreateOpenRequestSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return publish_open_request(payload, claims, db)


def _configured_min_comparables() -> int:
    raw = os.getenv("OPEN_REQUEST_MIN_COMPARABLES", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _configured_range_margin() -> Decimal:
    raw = os.getenv("OPEN_REQUEST_RANGE_MARGIN", str(DEFAULT_RANGE_MARGIN)).strip()
    try:
        val = Decimal(raw)
        if Decimal("0") <= val < Decimal("1"):
            return val
    except InvalidOperation:
        pass
    return DEFAULT_RANGE_MARGIN


def _compute_unit_stats(unit: str, tariffs: list[Decimal], margin: Decimal) -> dict:
    count = len(tariffs)
    avg = (sum(tariffs, Decimal("0")) / Decimal(count)).quantize(Decimal("0.01"))
    min_t = min(tariffs).quantize(Decimal("0.01"))
    max_t = max(tariffs).quantize(Decimal("0.01"))
    suggested_min = (avg * (Decimal("1") - margin)).quantize(Decimal("0.01"))
    suggested_max = (avg * (Decimal("1") + margin)).quantize(Decimal("0.01"))
    return {
        "unidad_cobro": unit,
        "tarifa_promedio": float(avg),
        "tarifa_minima": float(min_t),
        "tarifa_maxima": float(max_t),
        "rango_sugerido_min": float(suggested_min),
        "rango_sugerido_max": float(suggested_max),
        "cantidad_servicios": count,
    }


def calculate_suggested_range(
    especialidad_id: uuid.UUID,
    db: Session,
    *,
    unidad_cobro: str | None = None,
    min_comparables: int | None = None,
    margin: Decimal | None = None,
) -> dict:
    """HU-26 ST-03 (CA-05, CA-06): Calcula el rango sugerido basado en tarifas promedio por categoría y unidad."""
    specialty = db.get(Specialty, especialidad_id)
    if specialty is None:
        raise HTTPException(404, "No se encontró la especialidad solicitada")

    normalized_unit: str | None = None
    if unidad_cobro is not None and unidad_cobro.strip():
        normalized_unit = unidad_cobro.strip().upper()
        if normalized_unit not in VALID_UNITS:
            raise HTTPException(
                400,
                f"Unidad de cobro inválida. Opciones: {', '.join(VALID_UNITS)}",
            )

    required_count = min_comparables if min_comparables is not None else _configured_min_comparables()
    applied_margin = margin if margin is not None else _configured_range_margin()

    query = (
        select(Service)
        .outerjoin(TechnicianProfile, TechnicianProfile.usuario_id == Service.usuario_id)
        .where(
            Service.especialidad_id == especialidad_id,
            Service.activo.is_(True),
            Service.tarifa_base > 0,
        )
    )
    if normalized_unit:
        query = query.where(Service.unidad_cobro == normalized_unit)

    rows = db.scalars(query).all()
    by_unit: dict[str, list[Decimal]] = defaultdict(list)
    for srv in rows:
        # Excluir servicios de perfiles técnicos suspendidos
        profile = db.get(TechnicianProfile, srv.usuario_id)
        if profile is not None and profile.suspendido:
            continue
        try:
            tariff = Decimal(str(srv.tarifa_base))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if tariff > Decimal("0"):
            unit_key = (srv.unidad_cobro or "POR_HORA").strip().upper()
            by_unit[unit_key].append(tariff)

    eligible_units = {
        unit: tariffs for unit, tariffs in by_unit.items() if len(tariffs) >= required_count
    }

    if not eligible_units:
        return {
            "especialidad_id": str(specialty.id),
            "especialidad_nombre": specialty.nombre,
            "tiene_referencia": False,
            "unidad_cobro": normalized_unit,
            "tarifa_promedio": None,
            "tarifa_minima": None,
            "tarifa_maxima": None,
            "rango_sugerido_min": None,
            "rango_sugerido_max": None,
            "servicios_comparables": 0,
            "mensaje": "No hay referencia suficiente de tarifas comparables en esta categoría.",
            "unidades": [],
        }

    # Ordenar unidades por mayor cantidad de servicios comparables y luego alfabéticamente
    sorted_units = sorted(
        eligible_units.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    unit_summaries = [
        _compute_unit_stats(unit, tariffs, applied_margin)
        for unit, tariffs in sorted_units
    ]
    primary = unit_summaries[0]

    return {
        "especialidad_id": str(specialty.id),
        "especialidad_nombre": specialty.nombre,
        "tiene_referencia": True,
        "unidad_cobro": primary["unidad_cobro"],
        "tarifa_promedio": primary["tarifa_promedio"],
        "tarifa_minima": primary["tarifa_minima"],
        "tarifa_maxima": primary["tarifa_maxima"],
        "rango_sugerido_min": primary["rango_sugerido_min"],
        "rango_sugerido_max": primary["rango_sugerido_max"],
        "servicios_comparables": primary["cantidad_servicios"],
        "mensaje": (
            f"Rango sugerido calculado con {primary['cantidad_servicios']} "
            f"{'tarifa comparable' if primary['cantidad_servicios'] == 1 else 'tarifas comparables'} "
            f"en la unidad {primary['unidad_cobro']}."
        ),
        "unidades": unit_summaries,
    }


@router.get("/suggested-range")
def get_suggested_range_endpoint(
    response: Response,
    especialidad_id: uuid.UUID = Query(..., description="ID de la especialidad a consultar"),
    unidad_cobro: str | None = Query(None, description="Unidad de cobro opcional (POR_HORA, POR_SERVICIO, POR_DIA, POR_PROYECTO)"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return calculate_suggested_range(especialidad_id, db, unidad_cobro=unidad_cobro)


def _normalize_place(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def require_eligible_technician_for_bidding(user: User, db: Session) -> TechnicianProfile:
    """CA-04: Valida que el técnico esté activo, verificado, disponible y sin restricción operativa."""
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Solo cuentas de técnico pueden consultar ofertas abiertas para postular")

    profile = db.get(TechnicianProfile, user.id)
    if profile is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")

    has_docs = db.scalar(
        select(IdentityDocument.id).where(IdentityDocument.usuario_id == user.id).limit(1)
    )
    if has_docs is not None:
        return require_operational_technician(user, db, proceso_responsable="CONSULTA_LICITACIONES_ABIERTAS")

    if user.estado_identidad != "VERIFICADO" or profile.estado_verificacion != "VERIFICADO":
        raise HTTPException(403, "El técnico debe estar verificado para consultar ofertas abiertas")
    if not user.telefono_verificado:
        raise HTTPException(403, "Debes verificar tu número de teléfono para consultar ofertas abiertas")
    if not profile.disponible:
        raise HTTPException(403, "Debes estar en estado Disponible para ver ofertas abiertas")
    if profile.suspendido:
        raise HTTPException(403, "Tu perfil técnico presenta una suspensión activa")
    if profile.restriccion_financiera:
        raise HTTPException(403, "Tu perfil técnico presenta una restricción financiera activa")
    if not is_operationally_enabled(user, profile):
        raise HTTPException(403, "Tu perfil técnico no está habilitado operativamente")
    return profile


def _technician_compatible_specialty_ids(db: Session, usuario_id: uuid.UUID) -> set[str]:
    rows = db.scalars(
        select(TechnicianSpecialty.especialidad_id).where(TechnicianSpecialty.usuario_id == usuario_id)
    ).all()
    return {str(spec_id) for spec_id in rows}


def _technician_coverage_pairs(db: Session, usuario_id: uuid.UUID) -> set[tuple[str, str]]:
    zones = db.scalars(
        select(CoverageZone).where(CoverageZone.usuario_id == usuario_id)
    ).all()
    return {(_normalize_place(z.municipio), _normalize_place(z.sector)) for z in zones}


def is_technician_compatible_with_open_request(
    usuario_id: uuid.UUID,
    record: OpenRequest,
    db: Session,
) -> bool:
    """CA-04: Comprueba que la especialidad y la zona (municipio y sector) coincidan con el técnico."""
    compatible_specs = _technician_compatible_specialty_ids(db, usuario_id)
    if str(record.especialidad_id) not in compatible_specs:
        return False

    coverage_pairs = _technician_coverage_pairs(db, usuario_id)
    req_pair = (_normalize_place(record.municipio), _normalize_place(record.sector))
    return req_pair in coverage_pairs


def serialize_open_request_for_technician(
    record: OpenRequest,
    technician_user: User,
    db: Session,
) -> dict:
    """CA-03 y Condición transversal: Protege dirección exacta y teléfono antes de Aceptada."""
    estado_norm = (record.estado or "Publicada").strip().upper()
    is_awarded_technician = (
        bool(record.tecnico_id)
        and record.tecnico_id == technician_user.firebase_uid
        and estado_norm in {"ACEPTADA", "COMPLETADA"}
    )

    spec_uuid: uuid.UUID | None = None
    try:
        spec_uuid = uuid.UUID(str(record.especialidad_id))
    except ValueError:
        spec_uuid = None
    specialty = db.get(Specialty, spec_uuid) if spec_uuid else None
    client_user = db.scalar(select(User).where(User.firebase_uid == record.cliente_id))

    presupuesto_val = float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01")))
    payload = {
        "id": str(record.id),
        "solicitud_id": str(record.id),
        "modalidad": "ABIERTA",
        "estado": estado_norm,
        "especialidad_id": str(record.especialidad_id),
        "especialidad": (
            {"id": str(specialty.id), "nombre": specialty.nombre}
            if specialty is not None
            else {"id": str(record.especialidad_id), "nombre": None}
        ),
        "descripcion": record.descripcion,
        "municipio": record.municipio,
        "sector": record.sector,
        "fecha_hora": record.fecha_hora.isoformat() if record.fecha_hora else None,
        "metodo_pago": record.metodo_pago,
        "presupuesto": presupuesto_val,
        "monto_base": presupuesto_val,
        "creada_en": record.creada_en.isoformat() if record.creada_en else None,
        "cliente": {
            "nombre_completo": client_user.nombre_completo if client_user is not None else None,
        },
        "ubicacion_exacta_protegida": not is_awarded_technician,
    }

    # CA-03: Solo el técnico adjudicado en estado ACEPTADA (o COMPLETADA) recibe dirección exacta y teléfono
    if is_awarded_technician:
        payload["direccion_exacta"] = record.direccion_exacta
        payload["cliente"]["telefono"] = client_user.telefono if client_user is not None else None

    return payload


def list_available_open_requests_for_technician(
    claims: dict,
    db: Session,
    *,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """HU-26 ST-04 (CA-03, CA-04): Lista solicitudes abiertas compatibles solo para técnicos elegibles."""
    if offset < 0:
        raise HTTPException(400, "El parámetro offset no puede ser negativo")
    if limit < 1 or limit > 100:
        raise HTTPException(400, "El parámetro limit debe estar entre 1 y 100")

    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    require_eligible_technician_for_bidding(user, db)

    compatible_specs = _technician_compatible_specialty_ids(db, user.id)
    coverage_pairs = _technician_coverage_pairs(db, user.id)
    if not compatible_specs or not coverage_pairs:
        return {"total": 0, "offset": offset, "limit": limit, "solicitudes": []}

    published_rows = db.scalars(
        select(OpenRequest)
        .where(OpenRequest.Speciality_filter if hasattr(OpenRequest, "Speciality_filter") else OpenRequest.especialidad_id.in_(compatible_specs))
        .order_by(OpenRequest.creada_en.desc(), OpenRequest.id.desc())
    ).all()

    matching = [
        row
        for row in published_rows
        if (row.estado or "").strip().upper() == "PUBLICADA"
        and row.tecnico_id is None
        and (_normalize_place(row.municipio), _normalize_place(row.sector)) in coverage_pairs
    ]
    total = len(matching)
    page_slice = matching[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "solicitudes": [
            serialize_open_request_for_technician(row, user, db)
            for row in page_slice
        ],
    }


def get_open_request_for_viewer(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-26 ST-04 (CA-03, CA-04): Detalle de solicitud abierta con control de elegibilidad y privacidad."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    clean_id = (solicitud_id or "").strip()
    record = db.scalar(select(OpenRequest).where(OpenRequest.id == clean_id))
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    # 1. Si quien consulta es el cliente propietario: ve su propia solicitud completa
    if record.cliente_id == user.firebase_uid:
        spec_uuid: uuid.UUID | None = None
        try:
            spec_uuid = uuid.UUID(str(record.especialidad_id))
        except ValueError:
            spec_uuid = None
        specialty = db.get(Specialty, spec_uuid) if spec_uuid else None
        return serialize_open_request_for_owner(record, specialty)

    # 2. Si es otro usuario sin rol TECNICO (ej. otro cliente): acceso denegado
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "No tienes permiso para acceder a esta solicitud")

    estado_norm = (record.estado or "Publicada").strip().upper()

    # 3. Si la solicitud ya fue adjudicada a este técnico y está en ACEPTADA o COMPLETADA
    if record.tecnico_id == user.firebase_uid and estado_norm in {"ACEPTADA", "COMPLETADA"}:
        return serialize_open_request_for_technician(record, user, db)

    # 4. Técnico postulante: solo puede ver solicitudes en estado PUBLICADA si es elegible y compatible
    require_eligible_technician_for_bidding(user, db)
    if estado_norm != "PUBLICADA" or not is_technician_compatible_with_open_request(user.id, record, db):
        raise HTTPException(
            403,
            "Esta solicitud abierta no está disponible para tu especialidad o zona de cobertura",
        )

    return serialize_open_request_for_technician(record, user, db)


@router.get("/available")
def list_available_open_requests_endpoint(
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_available_open_requests_for_technician(claims, db, offset=offset, limit=limit)


@router.get("/{solicitud_id}")
def get_open_request_detail_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_open_request_for_viewer(solicitud_id, claims, db)
