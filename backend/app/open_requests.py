"""HU-26: Publicación de solicitudes abiertas (licitaciones), rango sugerido y visibilidad por elegibilidad."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
import re
from typing import Any
import unicodedata
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    CoverageZone,
    DiscardedOpenRequest,
    IdentityDocument,
    OpenRequest,
    OpenRequestProposal,
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


def serialize_open_request_for_owner(
    record: OpenRequest,
    specialty: Specialty | None = None,
    db: Session | None = None,
) -> dict:
    presupuesto_val = float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01")))
    payload = {
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
    if db is not None:
        proposals = db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .order_by(OpenRequestProposal.creada_en.desc(), OpenRequestProposal.id.desc())
        ).all()
        payload["propuestas"] = [serialize_open_request_proposal(p, db) for p in proposals]
        payload["eventos"] = get_open_request_events(str(record.id))
    return payload


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


def _strip_accents(value: str | None) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return ""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def _normalize_place(value: str | None) -> str:
    return _strip_accents(value).lower()


_SECTOR_PREFIX_PATTERN = re.compile(
    r"^(?:col(?:onia)?\.?|bo\.?|barrio|res(?:idencial)?\.?|urb(?:anizacion)?\.?|sec(?:tor)?\.?|aldea|villa|lot(?:ificacion)?\.?|ciudad)\s+",
    re.IGNORECASE,
)

_WILDCARD_SECTORS = {
    "todo el municipio",
    "todos",
    "todo",
    "cualquier sector",
    "general",
    "toda la ciudad",
}


def _municipio_canonical_tokens(value: str | None) -> set[str]:
    norm = _normalize_place(value)
    if not norm:
        return set()
    tokens = {norm}
    # Separar alias entre paréntesis, ej. "Distrito Central (Tegucigalpa)" -> "distrito central", "tegucigalpa"
    outside = re.sub(r"\([^)]*\)", " ", norm)
    outside_clean = " ".join(outside.split()).strip(" -_,")
    if outside_clean:
        tokens.add(outside_clean)
    for inside in re.findall(r"\(([^)]+)\)", norm):
        inside_clean = " ".join(inside.split()).strip(" -_,")
        if inside_clean:
            tokens.add(inside_clean)

    # Alias geográficos equivalentes en Honduras
    if tokens & {"distrito central", "tegucigalpa", "comayaguela", "tegucigalpa y comayaguela"}:
        tokens.update({"distrito central", "tegucigalpa", "comayaguela", "distrito central (tegucigalpa)"})
    if tokens & {"san juan de flores", "cantarranas", "san juan de flores (cantarranas)"}:
        tokens.update({"san juan de flores", "cantarranas", "san juan de flores (cantarranas)"})
    if tokens & {"san pedro sula", "sps"}:
        tokens.update({"san pedro sula", "sps"})
    return tokens


def _sector_canonical_key(value: str | None) -> str:
    norm = _normalize_place(value)
    if not norm:
        return ""
    # Limpiar puntuación común y prefijos de colonia/barrio/residencial repetidamente
    cleaned = re.sub(r"[.,;#\-_/]+", " ", norm)
    cleaned = " ".join(cleaned.split())
    prev = None
    while cleaned and cleaned != prev:
        prev = cleaned
        cleaned = _SECTOR_PREFIX_PATTERN.sub("", cleaned).strip()
    return " ".join(cleaned.split()) or norm


def _municipalities_match(muni_a: str | None, muni_b: str | None) -> bool:
    tokens_a = _municipio_canonical_tokens(muni_a)
    tokens_b = _municipio_canonical_tokens(muni_b)
    if not tokens_a or not tokens_b:
        return False
    return bool(tokens_a & tokens_b)


def _sectors_match(sector_a: str | None, sector_b: str | None) -> bool:
    key_a = _sector_canonical_key(sector_a)
    key_b = _sector_canonical_key(sector_b)
    if not key_a or not key_b:
        return False
    if key_a in _WILDCARD_SECTORS or key_b in _WILDCARD_SECTORS:
        return True
    return key_a == key_b


def _locations_match(
    muni_a: str | None,
    sector_a: str | None,
    muni_b: str | None,
    sector_b: str | None,
) -> bool:
    return _municipalities_match(muni_a, muni_b) and _sectors_match(sector_a, sector_b)


AVAILABLE_OPPORTUNITY_STATE_KEYS = {"PUBLICADA", "CON_OFERTAS"}


def _canonical_state_key(raw_estado: str | None) -> str:
    tokens = (raw_estado or "").strip().upper().replace("-", " ").replace("_", " ").split()
    return "_".join(tokens)


def is_open_request_available_for_bidding(record: OpenRequest) -> bool:
    """CA-01 y CA-02: Solo solicitudes Publicadas o Con ofertas y sin técnico adjudicado son oportunidades."""
    if record.tecnico_id is not None:
        return False
    return _canonical_state_key(record.estado) in AVAILABLE_OPPORTUNITY_STATE_KEYS


def _normalize_opportunity_state_filter(estado: str | None) -> str:
    if estado is None or not estado.strip():
        return "TODOS"
    key = _canonical_state_key(estado)
    if key == "TODOS":
        return "TODOS"
    if key in AVAILABLE_OPPORTUNITY_STATE_KEYS:
        return key
    raise HTTPException(
        400,
        "Estado de filtro inválido. Solo puedes consultar oportunidades en estado TODOS, PUBLICADA o CON_OFERTAS.",
    )


def require_eligible_technician_for_bidding(
    user: User,
    db: Session,
    *,
    proceso_responsable: str = "CONSULTA_LICITACIONES_ABIERTAS",
) -> TechnicianProfile:
    """CA-04 y Condición transversal: Re-evalúa en tiempo real que el técnico esté verificado, disponible, no suspendido y sin restricción de saldo."""
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Solo cuentas de técnico pueden consultar ofertas abiertas o postular")

    profile = db.scalar(
        select(TechnicianProfile)
        .where(TechnicianProfile.usuario_id == user.id)
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")

    has_docs = db.scalar(
        select(IdentityDocument.id).where(IdentityDocument.usuario_id == user.id).limit(1)
    )
    if has_docs is not None:
        return require_operational_technician(
            user,
            db,
            proceso_responsable=proceso_responsable,
        )

    if getattr(user, "suspendido", False) or profile.suspendido:
        raise HTTPException(403, "Tu perfil técnico presenta una suspensión activa y no puede postular")
    if profile.restriccion_financiera:
        raise HTTPException(403, "Tu perfil técnico presenta una restricción financiera activa por saldo")
    if user.estado_identidad != "VERIFICADO" or profile.estado_verificacion != "VERIFICADO":
        raise HTTPException(403, "El técnico debe estar verificado para consultar ofertas abiertas o postular")
    if not user.telefono_verificado:
        raise HTTPException(403, "Debes verificar tu número de teléfono para consultar ofertas abiertas o postular")
    if not profile.disponible:
        raise HTTPException(403, "Debes estar en estado Disponible para consultar ofertas o postular")
    if not is_operationally_enabled(user, profile):
        raise HTTPException(403, "Tu perfil técnico no está habilitado operativamente")
    return profile


def require_eligible_technician_for_open_request_action(
    solicitud_id: str,
    claims: dict,
    db: Session,
    *,
    proceso_responsable: str = "POSTULACION_LICITACION_ABIERTA",
) -> tuple[User, TechnicianProfile, OpenRequest]:
    """HU-27 ST-04 (CA-04): Validación operativa compartida que se repite al ejecutar cualquier acción o postulación."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(
        select(User)
        .where(User.firebase_uid == uid)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    profile = require_eligible_technician_for_bidding(
        user,
        db,
        proceso_responsable=proceso_responsable,
    )

    clean_id = (solicitud_id or "").strip()
    record = db.scalar(
        select(OpenRequest)
        .where(OpenRequest.id == clean_id)
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    if (
        not is_open_request_available_for_bidding(record)
        or not is_technician_compatible_with_open_request(user.id, record, db)
    ):
        raise HTTPException(
            403,
            "Esta solicitud abierta no está disponible para tu especialidad o zona de cobertura",
        )

    return user, profile, record


def _technician_compatible_specialty_ids(db: Session, usuario_id: uuid.UUID) -> set[str]:
    rows = db.scalars(
        select(TechnicianSpecialty.especialidad_id).where(TechnicianSpecialty.usuario_id == usuario_id)
    ).all()
    return {str(spec_id) for spec_id in rows}


def _technician_coverage_zones(db: Session, usuario_id: uuid.UUID) -> list[CoverageZone]:
    return list(
        db.scalars(
            select(CoverageZone).where(CoverageZone.usuario_id == usuario_id)
        ).all()
    )


def _technician_coverage_pairs(db: Session, usuario_id: uuid.UUID) -> set[tuple[str, str]]:
    zones = _technician_coverage_zones(db, usuario_id)
    return {(_normalize_place(z.municipio), _sector_canonical_key(z.sector)) for z in zones}


def _technician_covers_location(
    zones: list[CoverageZone],
    municipio: str | None,
    sector: str | None,
) -> bool:
    return any(
        _locations_match(z.municipio, z.sector, municipio, sector)
        for z in zones
    )


def _technician_discarded_request_ids(db: Session, usuario_id: uuid.UUID) -> set[str]:
    """HU-27 ST-02 (CA-05): Obtiene los IDs de licitaciones descartadas personalmente por este técnico."""
    rows = db.scalars(
        select(DiscardedOpenRequest.solicitud_id).where(DiscardedOpenRequest.usuario_id == usuario_id)
    ).all()
    return {str(req_id) for req_id in rows}


def is_technician_compatible_with_open_request(
    usuario_id: uuid.UUID,
    record: OpenRequest,
    db: Session,
) -> bool:
    """CA-04: Comprueba que la especialidad y la zona (municipio y sector) coincidan con el técnico."""
    compatible_specs = _technician_compatible_specialty_ids(db, usuario_id)
    if str(record.especialidad_id) not in compatible_specs:
        return False

    zones = _technician_coverage_zones(db, usuario_id)
    return _technician_covers_location(zones, record.municipio, record.sector)


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

    my_proposal = db.scalar(
        select(OpenRequestProposal).where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.usuario_id == technician_user.id,
            OpenRequestProposal.activa.is_(True),
        )
    )
    payload["mi_propuesta"] = (
        {
            "id": str(my_proposal.id),
            "tipo": my_proposal.tipo,
            "monto": float(Decimal(str(my_proposal.monto)).quantize(Decimal("0.01"))),
            "estado": (my_proposal.estado or "PENDIENTE").strip().upper(),
            "creada_en": my_proposal.creada_en.isoformat() if my_proposal.creada_en else None,
        }
        if my_proposal is not None
        else None
    )

    return payload


def list_available_open_requests_for_technician(
    claims: dict,
    db: Session,
    *,
    especialidad_id: uuid.UUID | str | None = None,
    municipio: str | None = None,
    sector: str | None = None,
    estado: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """HU-26 ST-04 y HU-27 ST-01 (CA-01 a CA-04, CA-06): Consulta oportunidades por cobertura, oficio y estado."""
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

    state_filter = _normalize_opportunity_state_filter(estado)
    spec_filter_str: str | None = None
    if especialidad_id is not None and str(especialidad_id).strip():
        try:
            spec_filter_str = str(uuid.UUID(str(especialidad_id).strip()))
        except ValueError:
            raise HTTPException(400, "El filtro especialidad_id no es un UUID válido") from None

    municipio_clean = " ".join((municipio or "").split()) if municipio and municipio.strip() else None
    sector_clean = " ".join((sector or "").split()) if sector and sector.strip() else None
    norm_municipio = _normalize_place(municipio_clean) if municipio_clean else None
    norm_sector = _normalize_place(sector_clean) if sector_clean else None

    applied_filters = {
        "especialidad_id": spec_filter_str,
        "municipio": municipio_clean,
        "sector": sector_clean,
        "estado": state_filter,
    }

    compatible_specs = _technician_compatible_specialty_ids(db, user.id)
    zones = _technician_coverage_zones(db, user.id)
    diagnostico_cobertura = {
        "tiene_especialidades": bool(compatible_specs),
        "cantidad_especialidades": len(compatible_specs),
        "tiene_zonas_cobertura": bool(zones),
        "cantidad_zonas_cobertura": len(zones),
    }
    if not compatible_specs or not zones:
        missing_parts: list[str] = []
        if not compatible_specs:
            missing_parts.append("especialidades en tu perfil")
        if not zones:
            missing_parts.append("zonas de cobertura")
        missing_detail = f" (te falta configurar {' y '.join(missing_parts)} en tu perfil)" if missing_parts else ""
        return {
            "total": 0,
            "offset": offset,
            "limit": limit,
            "filtros_aplicados": applied_filters,
            "diagnostico_cobertura": diagnostico_cobertura,
            "mensaje": (
                f"No se encontraron oportunidades disponibles para tu especialidad o zona{missing_detail}. "
                "Revisa tus filtros o cobertura."
            ),
            "solicitudes": [],
        }

    # CA-02: Solo se puede consultar dentro de las especialidades compatibles del técnico
    target_specs = compatible_specs
    if spec_filter_str is not None:
        if spec_filter_str not in compatible_specs:
            return {
                "total": 0,
                "offset": offset,
                "limit": limit,
                "filtros_aplicados": applied_filters,
                "diagnostico_cobertura": diagnostico_cobertura,
                "mensaje": "No se encontraron oportunidades disponibles con los filtros seleccionados. Revisa o ajusta los filtros.",
                "solicitudes": [],
            }
        target_specs = {spec_filter_str}

    candidate_rows = db.scalars(
        select(OpenRequest)
        .where(OpenRequest.especialidad_id.in_(target_specs))
        .order_by(OpenRequest.creada_en.desc(), OpenRequest.id.desc())
    ).all()

    discarded_ids = _technician_discarded_request_ids(db, user.id)

    matching: list[OpenRequest] = []
    for row in candidate_rows:
        # CA-05: Ocultar las solicitudes descartadas personalmente por este técnico
        if str(row.id) in discarded_ids:
            continue
        # CA-01 y CA-02: Solo Publicada o Con ofertas y sin técnico adjudicado
        if not is_open_request_available_for_bidding(row):
            continue
        row_state_key = _canonical_state_key(row.estado)
        if state_filter != "TODOS" and row_state_key != state_filter:
            continue

        if not _technician_covers_location(zones, row.municipio, row.sector):
            continue
        if municipio_clean is not None and not _municipalities_match(row.municipio, municipio_clean):
            continue
        if sector_clean is not None and not _sectors_match(row.sector, sector_clean):
            continue

        matching.append(row)

    total = len(matching)
    page_slice = matching[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "filtros_aplicados": applied_filters,
        "diagnostico_cobertura": diagnostico_cobertura,
        "mensaje": (
            "No se encontraron oportunidades disponibles con los filtros seleccionados. Revisa o ajusta los filtros."
            if total == 0
            else None
        ),
        "solicitudes": [
            serialize_open_request_for_technician(row, user, db)
            for row in page_slice
        ],
    }


def list_coverage_zones_catalog(
    db: Session,
    *,
    especialidad_id: uuid.UUID | str | None = None,
    municipio: str | None = None,
) -> list[dict]:
    """Retorna las zonas de cobertura (departamento, municipio, sector) registradas por técnicos para sugerir sectores estandarizados."""
    query = select(CoverageZone).outerjoin(
        TechnicianProfile,
        TechnicianProfile.usuario_id == CoverageZone.usuario_id,
    )
    if especialidad_id is not None and str(especialidad_id).strip():
        try:
            spec_uuid = uuid.UUID(str(especialidad_id).strip())
            query = query.join(
                TechnicianSpecialty,
                TechnicianSpecialty.usuario_id == CoverageZone.usuario_id,
            ).where(TechnicianSpecialty.especialidad_id == spec_uuid)
        except ValueError:
            pass

    rows = db.scalars(query).all()
    seen: set[tuple[str, str, str]] = set()
    items: list[dict] = []
    for zone in rows:
        if municipio and municipio.strip() and not _municipalities_match(zone.municipio, municipio):
            continue
        depto_clean = " ".join((zone.departamento or "").split())
        muni_clean = " ".join((zone.municipio or "").split())
        sector_clean = " ".join((zone.sector or "").split())
        if not muni_clean or not sector_clean:
            continue
        key = (
            _normalize_place(depto_clean),
            _normalize_place(muni_clean),
            _sector_canonical_key(sector_clean),
        )
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "departamento": depto_clean,
                "municipio": muni_clean,
                "sector": sector_clean,
            }
        )
    items.sort(key=lambda item: (item["departamento"], item["municipio"], item["sector"]))
    return items


@router.get("/coverage-zones")
def get_coverage_zones_catalog_endpoint(
    response: Response,
    especialidad_id: uuid.UUID | None = Query(None, description="Filtrar zonas por especialidad"),
    municipio: str | None = Query(None, description="Filtrar zonas por municipio"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_coverage_zones_catalog(
        db,
        especialidad_id=especialidad_id,
        municipio=municipio,
    )


def get_open_request_for_viewer(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-26 ST-04 y HU-27 ST-01 (CA-01 a CA-04): Detalle de solicitud abierta con control de elegibilidad y privacidad."""
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
        return serialize_open_request_for_owner(record, specialty, db)

    # 2. Si es otro usuario sin rol TECNICO (ej. otro cliente): acceso denegado
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "No tienes permiso para acceder a esta solicitud")

    estado_norm = (record.estado or "Publicada").strip().upper()

    # 3. Si la solicitud ya fue adjudicada a este técnico y está en ACEPTADA o COMPLETADA
    if record.tecnico_id == user.firebase_uid and estado_norm in {"ACEPTADA", "COMPLETADA"}:
        return serialize_open_request_for_technician(record, user, db)

    # 4. Técnico postulante: solo puede ver solicitudes en estado PUBLICADA o CON OFERTAS sin adjudicar si es elegible y compatible
    require_eligible_technician_for_bidding(user, db)
    if (
        not is_open_request_available_for_bidding(record)
        or not is_technician_compatible_with_open_request(user.id, record, db)
    ):
        raise HTTPException(
            403,
            "Esta solicitud abierta no está disponible para tu especialidad o zona de cobertura",
        )

    return serialize_open_request_for_technician(record, user, db)


@router.get("/available")
def list_available_open_requests_endpoint(
    response: Response,
    especialidad_id: uuid.UUID | None = Query(None, description="Filtrar por oficio/especialidad compatible"),
    municipio: str | None = Query(None, description="Filtrar por municipio dentro de tu cobertura"),
    sector: str | None = Query(None, description="Filtrar por sector dentro de tu cobertura"),
    estado: str | None = Query("TODOS", description="Filtrar por estado: TODOS, PUBLICADA o CON_OFERTAS"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_available_open_requests_for_technician(
        claims,
        db,
        especialidad_id=especialidad_id,
        municipio=municipio,
        sector=sector,
        estado=estado,
        offset=offset,
        limit=limit,
    )


@router.get("/{solicitud_id}")
def get_open_request_detail_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_open_request_for_viewer(solicitud_id, claims, db)


def discard_open_request_for_technician(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-27 ST-02 y ST-04 (CA-04, CA-05): Descarta una publicación del listado personal del técnico repitiendo la validación operativa."""
    user, _, record = require_eligible_technician_for_open_request_action(
        solicitud_id,
        claims,
        db,
        proceso_responsable="DESCARTE_LICITACION_ABIERTA",
    )

    existing = db.scalar(
        select(DiscardedOpenRequest).where(
            DiscardedOpenRequest.usuario_id == user.id,
            DiscardedOpenRequest.solicitud_id == str(record.id),
        )
    )
    if existing is None:
        existing = DiscardedOpenRequest(
            id=str(uuid.uuid4()),
            usuario_id=user.id,
            tecnico_id=user.firebase_uid,
            solicitud_id=str(record.id),
            descartada_en=datetime.now(timezone.utc),
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)

    return {
        "solicitud_id": str(record.id),
        "descartada": True,
        "descartada_en": existing.descartada_en.isoformat() if existing.descartada_en else None,
        "estado_solicitud": (record.estado or "Publicada").strip().upper(),
        "mensaje": "Publicación descartada de tu listado personal",
    }


@router.post("/{solicitud_id}/discard")
def discard_open_request_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return discard_open_request_for_technician(solicitud_id, claims, db)


def validate_technician_can_bid_on_open_request(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-27 ST-04 (CA-04): Valida en tiempo real si el técnico puede postular a la solicitud abierta."""
    user, profile, record = require_eligible_technician_for_open_request_action(
        solicitud_id,
        claims,
        db,
        proceso_responsable="POSTULACION_LICITACION_ABIERTA",
    )
    return {
        "solicitud_id": str(record.id),
        "tecnico_id": user.firebase_uid,
        "usuario_id": str(user.id),
        "elegible_para_postular": True,
        "estado_solicitud": (record.estado or "Publicada").strip().upper(),
        "disponible": bool(profile.disponible),
        "estado_verificacion": profile.estado_verificacion,
        "suspendido": bool(profile.suspendido),
        "restriccion_financiera": bool(profile.restriccion_financiera),
    }


@router.get("/{solicitud_id}/bidding-eligibility")
@router.post("/{solicitud_id}/bids/validate")
def validate_bidding_eligibility_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return validate_technician_can_bid_on_open_request(solicitud_id, claims, db)


_OPEN_REQUEST_EVENTS: dict[str, list[dict]] = defaultdict(list)


def emit_new_offer_event(
    record: OpenRequest,
    serialized_proposal: dict,
    *,
    emitted_at: datetime | None = None,
) -> dict:
    """HU-28 ST-04 (CA-05): Emite evento de nueva oferta para actualización del cliente sin exponer dirección ni teléfono."""
    timestamp = (emitted_at or datetime.now(timezone.utc)).isoformat()
    event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "NUEVA_OFERTA",
        "solicitud_id": str(record.id),
        "cliente_id": record.cliente_id,
        "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
        "propuesta": serialized_proposal,
        "municipio": record.municipio,
        "sector": record.sector,
        "ubicacion_exacta_protegida": True,
        "emitido_en": timestamp,
        "mensaje": (
            f"Nueva propuesta recibida por L. {serialized_proposal['monto']:.2f} "
            f"de {serialized_proposal['tecnico'].get('nombre_completo') or 'técnico verificado'}."
        ),
    }
    _OPEN_REQUEST_EVENTS[str(record.id)].append(event)
    return event


def get_open_request_events(solicitud_id: str) -> list[dict]:
    return list(_OPEN_REQUEST_EVENTS.get(str(solicitud_id), []))


def serialize_open_request_proposal(
    proposal: OpenRequestProposal,
    db: Session,
    *,
    tech_user: User | None = None,
    tech_profile: TechnicianProfile | None = None,
) -> dict:
    """HU-28 (CA-02, CA-05): Serializa una propuesta conservando técnico, monto, fecha y estado pendiente."""
    resolved_user = tech_user or db.scalar(select(User).where(User.id == proposal.usuario_id))
    resolved_profile = tech_profile or db.get(TechnicianProfile, proposal.usuario_id)
    monto_val = float(Decimal(str(proposal.monto)).quantize(Decimal("0.01")))
    return {
        "id": str(proposal.id),
        "propuesta_id": str(proposal.id),
        "solicitud_id": str(proposal.solicitud_id),
        "usuario_id": str(proposal.usuario_id),
        "tecnico_id": proposal.tecnico_id,
        "tipo": proposal.tipo,
        "monto": monto_val,
        "justificacion": proposal.justificacion,
        "estado": (proposal.estado or "PENDIENTE").strip().upper(),
        "activa": bool(proposal.activa),
        "creada_en": proposal.creada_en.isoformat() if proposal.creada_en else None,
        "actualizada_en": proposal.actualizada_en.isoformat() if proposal.actualizada_en else None,
        "tecnico": {
            "usuario_id": str(proposal.usuario_id),
            "firebase_uid": proposal.tecnico_id,
            "nombre_completo": resolved_user.nombre_completo if resolved_user is not None else None,
            "estado_verificacion": (
                resolved_profile.estado_verificacion if resolved_profile is not None else None
            ),
            "calificacion_promedio": (
                float(Decimal(str(resolved_profile.calificacion_promedio)).quantize(Decimal("0.01")))
                if resolved_profile is not None and resolved_profile.calificacion_promedio is not None
                else None
            ),
        },
    }


def accept_published_budget_for_open_request(
    solicitud_id: str,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-28 ST-02 (CA-01 a CA-06): Registra la propuesta aceptando el monto publicado y actualiza estado en transacción."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(
        select(User)
        .where(User.firebase_uid == uid)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    # CA-01 y Condición transversal: Re-evaluar habilitación operativa en el momento exacto de postular
    profile = require_eligible_technician_for_bidding(
        user,
        db,
        proceso_responsable="POSTULACION_ACEPTAR_PRESUPUESTO",
    )

    clean_id = (solicitud_id or "").strip()
    record = db.scalar(
        select(OpenRequest)
        .where(OpenRequest.id == clean_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    state_key = _canonical_state_key(record.estado)

    # CA-06: Si otro técnico ya fue adjudicado antes de confirmar la operación, rechazar sin modificar la contratación
    if record.tecnico_id is not None or state_key in {"ACEPTADA", "ADJUDICADA", "COMPLETADA"}:
        if record.tecnico_id and record.tecnico_id != user.firebase_uid:
            raise HTTPException(
                409,
                "Otro técnico ya fue adjudicado a esta solicitud antes de confirmar tu postulación.",
            )
        raise HTTPException(
            409,
            "Esta solicitud abierta ya fue adjudicada y no admite nuevas postulaciones.",
        )

    if state_key not in AVAILABLE_OPPORTUNITY_STATE_KEYS:
        raise HTTPException(
            409,
            f"La solicitud se encuentra en estado {(record.estado or '').strip()} y ya no recibe propuestas.",
        )

    if not is_technician_compatible_with_open_request(user.id, record, db):
        raise HTTPException(
            403,
            "Esta solicitud abierta no está disponible para tu especialidad o zona de cobertura",
        )

    published_budget = Decimal(str(record.presupuesto)).quantize(Decimal("0.01"))
    reference_now = now or datetime.now(timezone.utc)

    existing = db.scalar(
        select(OpenRequestProposal)
        .where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.usuario_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    # CA-04: Evitar duplicados en reintentos de la misma aceptación y bloquear competencia contra sí mismo
    if existing is not None:
        existing_amount = Decimal(str(existing.monto)).quantize(Decimal("0.01"))
        existing_state = (existing.estado or "PENDIENTE").strip().upper()
        if (
            existing.activa
            and existing_state == "PENDIENTE"
            and existing.tipo == "ACEPTACION_PRESUPUESTO"
            and existing_amount == published_budget
        ):
            if _canonical_state_key(record.estado) == "PUBLICADA":
                record.estado = "Con ofertas"
                db.commit()
                db.refresh(record)
            serialized_existing = serialize_open_request_proposal(
                existing,
                db,
                tech_user=user,
                tech_profile=profile,
            )
            return {
                "propuesta": serialized_existing,
                "solicitud": serialize_open_request_for_technician(record, user, db),
                "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
                "reintento_idempotente": True,
                "mensaje": "Ya habías aceptado el presupuesto publicado para esta solicitud.",
            }

        if existing.activa and existing_state in {"PENDIENTE", "ACEPTADA"}:
            raise HTTPException(
                409,
                "Ya cuentas con una propuesta activa en esta licitación y no puedes competir contra ti mismo.",
            )

        # Si tenía una propuesta previa inactiva (ej. RETIRADA), reactivarla con la nueva aceptación
        existing.tipo = "ACEPTACION_PRESUPUESTO"
        existing.monto = published_budget
        existing.justificacion = None
        existing.estado = "PENDIENTE"
        existing.activa = True
        existing.actualizada_en = reference_now
        proposal = existing
    else:
        proposal = OpenRequestProposal(
            id=str(uuid.uuid4()),
            solicitud_id=str(record.id),
            usuario_id=user.id,
            tecnico_id=user.firebase_uid,
            tipo="ACEPTACION_PRESUPUESTO",
            monto=published_budget,
            justificacion=None,
            estado="PENDIENTE",
            activa=True,
            creada_en=reference_now,
            actualizada_en=reference_now,
        )
        db.add(proposal)

    # CA-02 y CA-03: La primera propuesta cambia Publicada a Con ofertas; las siguientes mantienen Con ofertas; no adjudica técnico
    record.estado = "Con ofertas"
    record.tecnico_id = None

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(OpenRequestProposal).where(
                OpenRequestProposal.solicitud_id == str(record.id),
                OpenRequestProposal.usuario_id == user.id,
            )
        )
        if concurrent is not None and (concurrent.tipo == "ACEPTACION_PRESUPUESTO"):
            return {
                "propuesta": serialize_open_request_proposal(concurrent, db, tech_user=user, tech_profile=profile),
                "solicitud": serialize_open_request_for_technician(record, user, db),
                "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
                "reintento_idempotente": True,
                "mensaje": "Ya habías aceptado el presupuesto publicado para esta solicitud.",
            }
        raise HTTPException(
            409,
            "Ya cuentas con una propuesta activa en esta licitación y no puedes competir contra ti mismo.",
        ) from None

    db.refresh(record)
    db.refresh(proposal)

    serialized_proposal = serialize_open_request_proposal(
        proposal,
        db,
        tech_user=user,
        tech_profile=profile,
    )
    event = emit_new_offer_event(
        record,
        serialized_proposal,
        emitted_at=reference_now,
    )
    return {
        "propuesta": serialized_proposal,
        "solicitud": serialize_open_request_for_technician(record, user, db),
        "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
        "evento": event,
        "reintento_idempotente": False,
        "mensaje": "Propuesta enviada por el monto publicado. Queda pendiente de decisión del cliente.",
    }


@router.post("/{solicitud_id}/accept-budget", status_code=201)
@router.post("/{solicitud_id}/proposals/accept-budget", status_code=201)
def accept_published_budget_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    result = accept_published_budget_for_open_request(solicitud_id, claims, db)
    if result.get("reintento_idempotente"):
        response.status_code = 200
    return result


def list_open_request_proposals_for_viewer(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-28 ST-04 (CA-05): El cliente propietario ve todas las propuestas y eventos; el técnico ve solo su propuesta sin dirección ni teléfono."""
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

    if record.cliente_id == user.firebase_uid:
        proposals = db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .order_by(OpenRequestProposal.creada_en.desc(), OpenRequestProposal.id.desc())
        ).all()
        return {
            "solicitud_id": str(record.id),
            "estado_solicitud": (record.estado or "Publicada").strip().upper(),
            "total": len(proposals),
            "propuestas": [serialize_open_request_proposal(p, db) for p in proposals],
            "eventos": get_open_request_events(str(record.id)),
        }

    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "No tienes permiso para consultar las propuestas de esta solicitud")

    own_proposals = db.scalars(
        select(OpenRequestProposal)
        .where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.usuario_id == user.id,
        )
        .order_by(OpenRequestProposal.creada_en.desc())
    ).all()
    return {
        "solicitud_id": str(record.id),
        "estado_solicitud": (record.estado or "Publicada").strip().upper(),
        "ubicacion_exacta_protegida": True,
        "total": len(own_proposals),
        "propuestas": [serialize_open_request_proposal(p, db, tech_user=user) for p in own_proposals],
    }


@router.get("/{solicitud_id}/proposals")
@router.get("/{solicitud_id}/events")
def get_open_request_proposals_and_events_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_open_request_proposals_for_viewer(solicitud_id, claims, db)
