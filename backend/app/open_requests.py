"""HU-26: Publicación de solicitudes abiertas (licitaciones), rango sugerido y visibilidad por elegibilidad."""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import os
import re
from typing import Any
import unicodedata
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, model_validator, Field, field_validator
from sqlalchemy import select, func
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
    SuggestedTariff,
    TechnicianProfile,
    TechnicianSpecialty,
    TradeCertificate,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.notifications_events import (
    notify_open_request_award,
    notify_open_request_proposal,
    notify_open_request_proposal_rejected,
)
from app.permissions import require_enabled_client
from app.request_states import CONTACT_STATES, record_creation, request_timeline, start_request_service, state_key, transition_request
from app.security import get_current_user
from app.services import VALID_UNITS
from app.specialties import require_active_specialty
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

# --- HU-29 ST-01: Esquema y validaciones decimales para Contraoferta ---

class CounterOfferSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monto: Any = Field(description="Monto positivo propuesto en lempiras para la contraoferta")
    justificacion: str | None = Field(default=None, max_length=500, description="Justificación opcional del monto propuesto")

    @field_validator("justificacion")
    @classmethod
    def clean_justificacion(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None

#HU-30 Esquema para el Rechazo de una Propuesta CA-03


def validate_counter_offer_amount(value: Any) -> Decimal:
    """HU-29 CA-02: Monto vacío, cero, negativo o no numérico impide enviar; el valor válido se guarda con dos decimales."""
    if value is None:
        raise HTTPException(400, "El monto de la contraoferta es obligatorio")
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise HTTPException(400, "El monto de la contraoferta es obligatorio")
        if cleaned.upper().startswith("L."):
            cleaned = cleaned[2:].strip()
        elif cleaned.upper().startswith("L"):
            cleaned = cleaned[1:].strip()
        value = cleaned
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "El monto de la contraoferta debe ser un número válido") from None

    if not raw.is_finite():
        raise HTTPException(400, "El monto de la contraoferta debe ser un número finito")
    if raw <= Decimal("0"):
        raise HTTPException(400, "El monto de la contraoferta debe ser mayor a cero")
    if raw > MAX_BUDGET_AMOUNT:
        raise HTTPException(400, "El monto de la contraoferta no puede exceder L. 999,999.99")

    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
    specialty = require_active_specialty(db, payload.especialidad_id)

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
    monto_base_val = float(Decimal(str(record.monto_base or record.presupuesto)).quantize(Decimal("0.01")))
    monto_total_val = float(
        Decimal(str(record.monto_total if record.monto_total is not None else record.presupuesto)).quantize(
            Decimal("0.01")
        )
    )
    estado_norm = (record.estado or "Publicada").strip().upper()
    finalizacion_dt = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
    vencimiento_dt = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
    pendiente_recepcion = bool(getattr(record, "pendiente_recepcion", False)) or (
        finalizacion_dt is not None and estado_norm in {"EN PROCESO", "EN_PROCESO"}
    )
    payload = {
        "id": str(record.id),
        "solicitud_id": str(record.id),
        "modalidad": "ABIERTA",
        "estado": estado_norm,
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
        "referencia_pasarela": record.referencia_pasarela,
        "presupuesto": presupuesto_val,
        "monto_base": monto_base_val,
        "monto_total": monto_total_val,
        "desglose_medios": getattr(record, "desglose_medios", None),
        "creada_en": record.creada_en.isoformat() if record.creada_en else None,
        "finalizacion_reportada_en": finalizacion_dt.isoformat() if finalizacion_dt else None,
        "vencimiento_confirmacion_en": vencimiento_dt.isoformat() if vencimiento_dt else None,
        "finalizacion_reportada_local": format_tegucigalpa_iso(finalizacion_dt),
        "vencimiento_confirmacion_local": format_tegucigalpa_iso(vencimiento_dt),
        "pendiente_recepcion": pendiente_recepcion,
        "solicitud_origen_id": record.solicitud_origen_id,
        "solicitud_origen_modalidad": record.solicitud_origen_modalidad,
        "mi_resena_enviada": False,
        "evaluacion_cerrada": False,
    }
    if db is not None:
        if estado_norm == "COMPLETADA":
            from app.reviews import get_request_review_flags

            mi_resena_enviada, evaluacion_cerrada = get_request_review_flags(
                db, "ABIERTA", str(record.id), viewer_role="CLIENTE"
            )
            payload["mi_resena_enviada"] = mi_resena_enviada
            payload["evaluacion_cerrada"] = evaluacion_cerrada
        proposals = db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .order_by(OpenRequestProposal.creada_en.desc(), OpenRequestProposal.id.desc())
        ).all()
        payload["propuestas"] = [serialize_open_request_proposal(p, db) for p in proposals]
        payload["eventos"] = get_open_request_events(str(record.id))
        payload["linea_tiempo"] = request_timeline(db, record)
        payload["auditoria_adjudicacion"] = get_open_request_award_audits(str(record.id))
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
    record_creation(db, record)
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


def _compute_unit_stats(unit: str, tariffs: list[Decimal], margin: Decimal, configured_average: Decimal | None = None) -> dict:
    count = len(tariffs)
    avg = configured_average if configured_average is not None else (
        sum(tariffs, Decimal("0")) / Decimal(count)
    ).quantize(Decimal("0.01"))
    suggested_min = (avg * (Decimal("1") - margin)).quantize(Decimal("0.01"))
    suggested_max = (avg * (Decimal("1") + margin)).quantize(Decimal("0.01"))
    return {
        "unidad_cobro": unit,
        "tarifa_promedio": float(avg),
        "tarifa_minima": float(min(tariffs)) if tariffs else None,
        "tarifa_maxima": float(max(tariffs)) if tariffs else None,
        "rango_sugerido_min": float(suggested_min),
        "rango_sugerido_max": float(suggested_max),
        "cantidad_servicios": count,
        "origen_tarifa": "CONFIGURADA" if configured_average is not None else "SERVICIOS_ACTIVOS",
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
    if specialty is None or not specialty.activa:
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
    configured = select(SuggestedTariff).where(SuggestedTariff.especialidad_id == especialidad_id)
    if normalized_unit:
        configured = configured.where(SuggestedTariff.unidad == normalized_unit)
    averages = {row.unidad: row.promedio for row in db.scalars(configured)}
    for unit in averages:
        eligible_units[unit] = by_unit[unit]
    margin_percent = format((applied_margin * 100).normalize(), "f")
    rule = f"Tarifa promedio ± {margin_percent}%."

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
            "regla_calculo": rule,
        }

    # Ordenar unidades por mayor cantidad de servicios comparables y luego alfabéticamente
    sorted_units = sorted(
        eligible_units.items(),
        key=lambda item: (item[0] not in averages, -len(item[1]), item[0]),
    )
    unit_summaries = [
        _compute_unit_stats(unit, tariffs, applied_margin, averages.get(unit))
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
        "origen_tarifa": primary["origen_tarifa"],
        "regla_calculo": rule,
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
    estado_norm = state_key(record.estado)
    is_awarded_technician = (
        bool(record.tecnico_id)
        and record.tecnico_id == technician_user.firebase_uid
        and estado_norm in CONTACT_STATES
    )

    spec_uuid: uuid.UUID | None = None
    try:
        spec_uuid = uuid.UUID(str(record.especialidad_id))
    except ValueError:
        spec_uuid = None
    specialty = db.get(Specialty, spec_uuid) if spec_uuid else None
    client_user = db.scalar(select(User).where(User.firebase_uid == record.cliente_id))

    presupuesto_val = float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01")))
    finalizacion_dt = ensure_utc_datetime(getattr(record, "finalizacion_reportada_en", None))
    vencimiento_dt = ensure_utc_datetime(getattr(record, "vencimiento_confirmacion_en", None))
    pendiente_recepcion = bool(getattr(record, "pendiente_recepcion", False)) or (
        finalizacion_dt is not None and estado_norm in {"EN PROCESO", "EN_PROCESO"}
    )
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
        "finalizacion_reportada_en": finalizacion_dt.isoformat() if finalizacion_dt else None,
        "vencimiento_confirmacion_en": vencimiento_dt.isoformat() if vencimiento_dt else None,
        "finalizacion_reportada_local": format_tegucigalpa_iso(finalizacion_dt),
        "vencimiento_confirmacion_local": format_tegucigalpa_iso(vencimiento_dt),
        "pendiente_recepcion": pendiente_recepcion,
        "cliente": {
            "nombre_completo": client_user.nombre_completo if client_user is not None else None,
        },
        "ubicacion_exacta_protegida": not is_awarded_technician,
    }

    # HU-31_CA-05 y Condición transversal: Solo el técnico adjudicado en estado ACEPTADA (o COMPLETADA) recibe dirección exacta y teléfono
    payload["es_tecnico_adjudicado"] = is_awarded_technician
    if estado_norm == "COMPLETADA" and bool(record.tecnico_id) and record.tecnico_id == technician_user.firebase_uid:
        from app.reviews import get_request_review_flags

        mi_resena_enviada, evaluacion_cerrada = get_request_review_flags(
            db, "ABIERTA", str(record.id), viewer_role="TECNICO"
        )
        payload["mi_resena_enviada"] = mi_resena_enviada
        payload["evaluacion_cerrada"] = evaluacion_cerrada
    else:
        payload["mi_resena_enviada"] = False
        payload["evaluacion_cerrada"] = False
    if is_awarded_technician:
        payload["pago_tarjeta_confirmado"] = bool(record.referencia_pasarela)
        payload["direccion_exacta"] = record.direccion_exacta
        payload["cliente"]["telefono"] = client_user.telefono if client_user is not None else None
        payload["linea_tiempo"] = request_timeline(db, record)

    my_proposal = db.scalar(
        select(OpenRequestProposal)
        .where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.usuario_id == technician_user.id,
        )
        .order_by(OpenRequestProposal.actualizada_en.desc())
    )
    payload["mi_propuesta"] = (
        {
            "id": str(my_proposal.id),
            "tipo": my_proposal.tipo,
            "monto": float(Decimal(str(my_proposal.monto)).quantize(Decimal("0.01"))),
            "justificacion": my_proposal.justificacion,
            "estado": (my_proposal.estado or "PENDIENTE").strip().upper(),
            "activa": bool(my_proposal.activa),
            "creada_en": my_proposal.creada_en.isoformat() if my_proposal.creada_en else None,
        }
        if my_proposal is not None
        else None
    )
    payload["mis_avisos"] = get_open_request_author_notices(
        str(record.id),
        tecnico_id=technician_user.firebase_uid,
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

    estado_norm = state_key(record.estado)

    if record.tecnico_id in (user.firebase_uid, str(user.id)) and estado_norm in CONTACT_STATES | {"CANCELADA"}:
        detail = serialize_open_request_for_technician(record, user, db)
        if estado_norm == "CANCELADA":
            detail["linea_tiempo"] = request_timeline(db, record)
        return detail

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


@router.post("/{solicitud_id}/start")
def start_open_request_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims.get("uid")))
    if user is None or not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Solo el técnico seleccionado puede iniciar este servicio.")
    record = db.scalar(
        select(OpenRequest).where(OpenRequest.id == solicitud_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta.")
    if record.tecnico_id != user.firebase_uid:
        raise HTTPException(403, "Solo el técnico seleccionado puede iniciar este servicio.")
    start_request_service(db, record, user.firebase_uid)
    response.headers["Cache-Control"] = "no-store, private"
    return serialize_open_request_for_technician(record, user, db)


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
_OPEN_REQUEST_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = defaultdict(list)


def emit_new_offer_event(
    record: OpenRequest,
    serialized_proposal: dict,
    *,
    emitted_at: datetime | None = None,
) -> dict:
    """HU-28 / HU-29 (CA-05): Emite evento de nueva oferta para actualización del cliente sin exponer dirección ni teléfono."""
    timestamp = (emitted_at or datetime.now(timezone.utc)).isoformat()
    tipo_propuesta = serialized_proposal.get("tipo")
    es_contraoferta = (tipo_propuesta == "CONTRAOFERTA")
    event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "NUEVA_OFERTA",
        "tipo_propuesta": tipo_propuesta,
        "solicitud_id": str(record.id),
        "cliente_id": record.cliente_id,
        "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
        "propuesta": serialized_proposal,
        "municipio": record.municipio,
        "sector": record.sector,
        "ubicacion_exacta_protegida": True,
        "emitido_en": timestamp,
        "mensaje": (
            f"Nueva contraoferta recibida por L. {serialized_proposal['monto']:.2f} "
            if es_contraoferta
            else f"Nueva propuesta recibida por L. {serialized_proposal['monto']:.2f} "
        )
        + f"de {serialized_proposal['tecnico'].get('nombre_completo') or 'técnico verificado'}.",
    }
    _OPEN_REQUEST_EVENTS[str(record.id)].append(event)
    # HU-29 ST-04: Difusión inmediata a suscriptores SSE activos
    for queue in list(_OPEN_REQUEST_SUBSCRIBERS.get(str(record.id), [])):
        try:
            queue.put_nowait(event)
        except Exception:
            pass
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
    """HU-28 / HU-29 / HU-30 (CA-01, CA-02, CA-06): Serializa propuesta con técnico, calificación, insignias separadas y habilitación."""
    resolved_user = tech_user or db.scalar(select(User).where(User.id == proposal.usuario_id))
    resolved_profile = tech_profile or db.get(TechnicianProfile, proposal.usuario_id)
    monto_val = float(Decimal(str(proposal.monto)).quantize(Decimal("0.01")))

    # HU-30 CA-02: Insignias separadas de verificación y certificación de oficio
    verificado = bool(
        resolved_user is not None
        and resolved_user.estado_identidad == "VERIFICADO"
        and resolved_profile is not None
        and resolved_profile.estado_verificacion == "VERIFICADO"
    )

    certificaciones_count = 0
    if resolved_profile is not None:
        certificaciones_count = db.scalar(
            select(func.count(TradeCertificate.id)).where(
                TradeCertificate.usuario_id == resolved_profile.usuario_id,
                TradeCertificate.estado == "APROBADO",
            )
        ) or 0

    certificado = certificaciones_count > 0

    # HU-30 CA-06: Verificación de habilitación operativa para adjudicación
    habilitado_operativamente = bool(
        resolved_user is not None
        and resolved_profile is not None
        and is_operationally_enabled(resolved_user, resolved_profile)
    )

    propuesta_estado = (proposal.estado or "PENDIENTE").strip().upper()
    elegible_para_adjudicar = bool(
        proposal.activa
        and propuesta_estado == "PENDIENTE"
        and habilitado_operativamente
    )

    from app.reviews import get_technician_reputation_stats

    tech_avg_rating = (
        get_technician_reputation_stats(resolved_profile.usuario_id, db)[0]
        if resolved_profile is not None
        else None
    )

    return {
        "id": str(proposal.id),
        "propuesta_id": str(proposal.id),
        "solicitud_id": str(proposal.solicitud_id),
        "usuario_id": str(proposal.usuario_id),
        "tecnico_id": proposal.tecnico_id,
        "tipo": proposal.tipo,
        "monto": monto_val,
        "justificacion": proposal.justificacion,
        "estado": propuesta_estado,
        "activa": bool(proposal.activa),
        "elegible_para_adjudicar": elegible_para_adjudicar,
        "creada_en": proposal.creada_en.isoformat() if proposal.creada_en else None,
        "actualizada_en": proposal.actualizada_en.isoformat() if proposal.actualizada_en else None,
        "tecnico": {
            "usuario_id": str(proposal.usuario_id),
            "firebase_uid": proposal.tecnico_id,
            "nombre_completo": resolved_user.nombre_completo if resolved_user is not None else None,
            "estado_verificacion": (
                resolved_profile.estado_verificacion if resolved_profile is not None else None
            ),
            "verificado": verificado,
            "certificado": certificado,
            "certificaciones_aprobadas_count": certificaciones_count,
            "disponible": resolved_profile.disponible if resolved_profile is not None else False,
            "habilitado_operativamente": habilitado_operativamente,
            "calificacion_promedio": tech_avg_rating,
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
                transition_request(db, record, "CON OFERTAS", role="TECNICO", uid=user.firebase_uid, now=reference_now)
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
    if state_key == "PUBLICADA":
        transition_request(db, record, "CON OFERTAS", role="TECNICO", uid=user.firebase_uid, now=reference_now)

    try:
        db.flush()
        notify_open_request_proposal(db, record, proposal, now=reference_now)
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

# --- HU-29 ST-01 / ST-02: Lógica de contraoferta ---

def submit_counter_offer_for_open_request(
    solicitud_id: str,
    payload: CounterOfferSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-29 ST-01 (CA-01 a CA-06): Registra una contraoferta validando importe positivo, elegibilidad y unicidad."""
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

    # CA-01 y Condición transversal: Validar elegibilidad técnica en tiempo real
    profile = require_eligible_technician_for_bidding(
        user,
        db,
        proceso_responsable="POSTULACION_CONTRAOFERTA",
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

    # CA-06: Si la solicitud ya está adjudicada, cancelada o no disponible, rechazar
    if record.tecnico_id is not None or state_key in {"ACEPTADA", "ADJUDICADA", "COMPLETADA"}:
        if record.tecnico_id and record.tecnico_id != user.firebase_uid:
            raise HTTPException(
                409,
                "Otro técnico ya fue adjudicado a esta solicitud antes de confirmar tu contraoferta.",
            )
        raise HTTPException(
            409,
            "Esta solicitud abierta ya fue adjudicada y no admite nuevas contraofertas.",
        )

    if state_key == "CANCELADA":
        raise HTTPException(
            409,
            "La solicitud se encuentra cancelada y no recibe contraofertas.",
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

    # CA-02: Validar monto decimal positivo con 2 decimales
    counter_amount = validate_counter_offer_amount(payload.monto)
    reference_now = now or datetime.now(timezone.utc)

    # CA-06 / ST-02: Validar propuestas activas previas del mismo técnico
    existing = db.scalar(
        select(OpenRequestProposal)
        .where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.usuario_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if existing is not None:
        existing_amount = Decimal(str(existing.monto)).quantize(Decimal("0.01"))
        existing_state = (existing.estado or "PENDIENTE").strip().upper()

        # Idempotencia: Si reintenta exactamente la misma contraoferta activa
        if (
            existing.activa
            and existing_state == "PENDIENTE"
            and existing.tipo == "CONTRAOFERTA"
            and existing_amount == counter_amount
        ):
            if _canonical_state_key(record.estado) == "PUBLICADA":
                transition_request(db, record, "CON OFERTAS", role="TECNICO", uid=uid, now=reference_now)
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
                "mensaje": f"Ya habías enviado una contraoferta por L. {float(counter_amount):.2f} para esta solicitud.",
            }

        # CA-06: Si ya tiene otra propuesta activa, bloquear competencia contra sí mismo
        if existing.activa and existing_state in {"PENDIENTE", "ACEPTADA"}:
            raise HTTPException(
                409,
                "Ya cuentas con una propuesta activa en esta licitación y no puedes competir contra ti mismo.",
            )

        # Si tenía propuesta previa inactiva (ej. RETIRADA/RECHAZADA), reactivar como contraoferta
        existing.tipo = "CONTRAOFERTA"
        existing.monto = counter_amount
        existing.justificacion = payload.justificacion
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
            tipo="CONTRAOFERTA",
            monto=counter_amount,
            justificacion=payload.justificacion,
            estado="PENDIENTE",
            activa=True,
            creada_en=reference_now,
            actualizada_en=reference_now,
        )
        db.add(proposal)

    # CA-03 & CA-04: Pasa a "Con ofertas" y continúa disponible sin adjudicar
    if state_key == "PUBLICADA":
        transition_request(db, record, "CON OFERTAS", role="TECNICO", uid=uid, now=reference_now)
    record.tecnico_id = None

    try:
        db.flush()
        notify_open_request_proposal(db, record, proposal, now=reference_now)
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(OpenRequestProposal).where(
                OpenRequestProposal.solicitud_id == str(record.id),
                OpenRequestProposal.usuario_id == user.id,
            )
        )
        if concurrent is not None and concurrent.tipo == "CONTRAOFERTA":
            concurrent_amount = Decimal(str(concurrent.monto)).quantize(Decimal("0.01"))
            if concurrent_amount == counter_amount:
                return {
                    "propuesta": serialize_open_request_proposal(concurrent, db, tech_user=user, tech_profile=profile),
                    "solicitud": serialize_open_request_for_technician(record, user, db),
                    "estado_solicitud": (record.estado or "Con ofertas").strip().upper(),
                    "reintento_idempotente": True,
                    "mensaje": f"Ya habías enviado una contraoferta por L. {float(counter_amount):.2f} para esta solicitud.",
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
    # CA-05: Emite evento en tiempo real protegiendo dirección y teléfono
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
        "mensaje": f"Contraoferta enviada por L. {float(counter_amount):.2f}. Queda pendiente de decisión del cliente.",
    }


@router.post("/{solicitud_id}/counter-offer", status_code=201)
@router.post("/{solicitud_id}/proposals/counter-offer", status_code=201)
def submit_counter_offer_endpoint(
    solicitud_id: str,
    payload: CounterOfferSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    result = submit_counter_offer_for_open_request(solicitud_id, payload, claims, db)
    if result.get("reintento_idempotente"):
        response.status_code = 200
    return result

def list_open_request_proposals_for_viewer(
    solicitud_id: str,
    claims: dict,
    db: Session,
) -> dict:
    """HU-28 ST-04 / HU-30 ST-01 / HU-31 ST-04 (CA-01, CA-05): Permisos de propuestas, avisos y privacidad tras adjudicar."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    clean_id = (solicitud_id or "").strip()
    record = db.get(OpenRequest, clean_id)
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    estado_norm = (record.estado or "Publicada").strip().upper()

    # CA-01: Propietario consulta todas las propuestas para comparación y auditoría
    if record.cliente_id == user.firebase_uid:
        proposals = db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .order_by(OpenRequestProposal.creada_en.desc(), OpenRequestProposal.id.desc())
        ).all()
        return {
            "solicitud_id": str(record.id),
            "presupuesto_base": float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01"))),
            "estado_solicitud": estado_norm,
            "es_propietario": True,
            "tecnico_adjudicado_id": record.tecnico_id,
            "total": len(proposals),
            "propuestas": [serialize_open_request_proposal(p, db) for p in proposals],
            "eventos": get_open_request_events(str(record.id)),
            "avisos_autores": get_open_request_author_notices(str(record.id)),
            "auditoria_adjudicacion": get_open_request_award_audits(str(record.id)),
        }

    # CA-01: Si no es el propietario, solo un técnico con rol puede consultar su propia propuesta
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

    # HU-31 ST-04 (CA-05): Solo el técnico seleccionado en estado ACEPTADA/COMPLETADA recibe dirección exacta y teléfono
    is_awarded_tech = (
        bool(record.tecnico_id)
        and record.tecnico_id == user.firebase_uid
        and estado_norm in {"ACEPTADA", "COMPLETADA"}
    )
    payload = {
        "solicitud_id": str(record.id),
        "presupuesto_base": float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01"))),
        "estado_solicitud": estado_norm,
        "es_propietario": False,
        "es_tecnico_adjudicado": is_awarded_tech,
        "ubicacion_exacta_protegida": not is_awarded_tech,
        "municipio": record.municipio,
        "sector": record.sector,
        "total": len(own_proposals),
        "propuestas": [serialize_open_request_proposal(p, db, tech_user=user) for p in own_proposals],
        "avisos": get_open_request_author_notices(str(record.id), tecnico_id=user.firebase_uid),
    }
    if is_awarded_tech:
        client_user = db.scalar(select(User).where(User.firebase_uid == record.cliente_id))
        payload["direccion_exacta"] = record.direccion_exacta
        payload["cliente"] = {
            "nombre_completo": client_user.nombre_completo if client_user is not None else None,
            "telefono": client_user.telefono if client_user is not None else None,
        }
    return payload


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


# --- HU-29 ST-04 / HU-31 ST-04: Endpoint SSE en tiempo real con control de privacidad ---

@router.get("/{solicitud_id}/events/stream")
async def stream_open_request_events(
    solicitud_id: str,
    request: Request,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HU-29 ST-04 / HU-31 ST-04 (CA-05): Stream SSE en tiempo real aplicando permisos de contacto y dirección."""
    user = db.scalar(select(User).where(User.firebase_uid == claims.get("uid")))
    if not user:
        raise HTTPException(401, "Usuario no autenticado")

    clean_id = (solicitud_id or "").strip()
    record = db.get(OpenRequest, clean_id)
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    is_owner = (record.cliente_id == user.firebase_uid)
    is_tech = any(role.rol == "TECNICO" for role in user.roles)
    if not is_owner and not is_tech:
        raise HTTPException(403, "No tienes permiso para escuchar eventos de esta solicitud")

    estado_norm = (record.estado or "Con ofertas").strip().upper()
    is_awarded_tech = (
        bool(record.tecnico_id)
        and record.tecnico_id == user.firebase_uid
        and estado_norm in {"ACEPTADA", "COMPLETADA"}
    )
    if not is_owner and estado_norm in {"ACEPTADA", "COMPLETADA"} and not is_awarded_tech:
        has_own_proposal = db.scalar(
            select(OpenRequestProposal.id).where(
                OpenRequestProposal.solicitud_id == clean_id,
                OpenRequestProposal.usuario_id == user.id,
            )
        )
        if not has_own_proposal:
            raise HTTPException(
                403,
                "Esta licitación ya fue adjudicada a otro técnico y no tienes acceso a sus eventos ni datos privados.",
            )

    queue: asyncio.Queue = asyncio.Queue()
    _OPEN_REQUEST_SUBSCRIBERS[clean_id].append(queue)

    async def event_generator():
        try:
            init_payload: dict[str, Any] = {
                "tipo": "CONECTADO",
                "solicitud_id": clean_id,
                "estado_solicitud": estado_norm,
                "ubicacion_exacta_protegida": not (is_owner or is_awarded_tech),
            }
            if is_owner or is_awarded_tech:
                init_payload["direccion_exacta"] = record.direccion_exacta
            yield f"event: conexion\ndata: {json.dumps(init_payload)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    sanitized_event = dict(event_data)
                    can_view_private = is_owner or (
                        sanitized_event.get("tecnico_adjudicado_id") == user.firebase_uid
                        and sanitized_event.get("estado_solicitud") in {"ACEPTADA", "COMPLETADA"}
                    )
                    if not can_view_private:
                        sanitized_event.pop("direccion_exacta", None)
                        sanitized_event.pop("telefono", None)
                        sanitized_event["ubicacion_exacta_protegida"] = True
                    yield f"event: propuesta_actualizada\ndata: {json.dumps(sanitized_event)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            if queue in _OPEN_REQUEST_SUBSCRIBERS.get(clean_id, []):
                _OPEN_REQUEST_SUBSCRIBERS[clean_id].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
# --- HU-30 ST-03: Rechazo individual de propuestas por el cliente ---
class RejectProposalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: str | None = Field(default=None, max_length=500, description="Motivo opcional del rechazo")


def reject_open_request_proposal_by_owner(
    solicitud_id: str,
    proposal_id: str,
    payload: RejectProposalSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-30 ST-03 (CA-03): El cliente propietario rechaza una propuesta pendiente sin cancelar la licitación."""
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    clean_solicitud_id = (solicitud_id or "").strip()
    record = db.scalar(
        select(OpenRequest)
        .where(OpenRequest.id == clean_solicitud_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    if record.cliente_id != user.firebase_uid:
        raise HTTPException(403, "Solo el cliente propietario puede rechazar propuestas de esta licitación")

    state_key = _canonical_state_key(record.estado)
    if state_key in {"ACEPTADA", "ADJUDICADA", "CANCELADA", "COMPLETADA"}:
        raise HTTPException(
            409,
            f"La solicitud se encuentra en estado {(record.estado or '').strip()} y no permite rechazar propuestas.",
        )

    clean_prop_id = (proposal_id or "").strip()
    proposal = db.scalar(
        select(OpenRequestProposal)
        .where(
            OpenRequestProposal.id == clean_prop_id,
            OpenRequestProposal.solicitud_id == str(record.id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if proposal is None:
        raise HTTPException(404, "No se encontró la propuesta en esta solicitud")

    current_prop_state = (proposal.estado or "PENDIENTE").strip().upper()
    if current_prop_state == "RECHAZADA":
        raise HTTPException(409, "La propuesta ya fue rechazada anteriormente")
    if current_prop_state in {"ACEPTADA", "ADJUDICADA"}:
        raise HTTPException(409, "No se puede rechazar una propuesta ya aceptada")

    reference_now = now or datetime.now(timezone.utc)
    proposal.estado = "RECHAZADA"
    proposal.activa = False
    proposal.actualizada_en = reference_now

    if payload.motivo and payload.motivo.strip():
        motivo_limpio = payload.motivo.strip()
        proposal.justificacion = (
            f"{proposal.justificacion} | Motivo rechazo: {motivo_limpio}"
            if proposal.justificacion
            else f"Motivo rechazo: {motivo_limpio}"
        )

    # CA-03: No cancelar la licitación. Si quedan otras propuestas activas sigue Con ofertas; si no, pasa a Publicada
    active_count = db.scalar(
        select(func.count(OpenRequestProposal.id)).where(
            OpenRequestProposal.solicitud_id == str(record.id),
            OpenRequestProposal.activa.is_(True),
            OpenRequestProposal.estado == "PENDIENTE",
            OpenRequestProposal.id != proposal.id,
        )
    ) or 0

    if not active_count and state_key == "CON_OFERTAS":
        transition_request(db, record, "PUBLICADA", role="CLIENTE", uid=uid, now=reference_now)
    notify_open_request_proposal_rejected(db, record, proposal, now=reference_now)
    db.commit()
    db.refresh(record)
    db.refresh(proposal)

    # Emitir evento y notificar a suscriptores SSE
    event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "PROPUESTA_RECHAZADA",
        "solicitud_id": str(record.id),
        "propuesta_id": str(proposal.id),
        "tecnico_id": proposal.tecnico_id,
        "estado_solicitud": (record.estado or "Publicada").strip().upper(),
        "emitido_en": reference_now.isoformat(),
        "mensaje": f"Propuesta por L. {float(proposal.monto):.2f} fue rechazada por el cliente.",
    }
    _OPEN_REQUEST_EVENTS[str(record.id)].append(event)
    for queue in list(_OPEN_REQUEST_SUBSCRIBERS.get(str(record.id), [])):
        try:
            queue.put_nowait(event)
        except Exception:
            pass

    return {
        "mensaje": "Propuesta rechazada correctamente. La licitación continúa disponible.",
        "solicitud_id": str(record.id),
        "estado_solicitud": (record.estado or "Publicada").strip().upper(),
        "propuesta": serialize_open_request_proposal(proposal, db),
    }

# --- HU-31 ST-01: Adjudicación transaccional con bloqueo y unicidad de técnico seleccionado ---

_OPEN_REQUEST_AWARD_AUDITS: dict[str, list[dict]] = defaultdict(list)


def get_open_request_award_audits(solicitud_id: str) -> list[dict]:
    """HU-31 (CA-06): Consulta la auditoría conservada de decisiones de adjudicación para la licitación."""
    return list(_OPEN_REQUEST_AWARD_AUDITS.get(str(solicitud_id), []))


def _record_award_decision_audit(
    *,
    solicitud_id: str,
    propuesta_id: str | None,
    cliente_id: str | None,
    tecnico_id: str | None,
    monto_pactado: float | None,
    metodo_pago: str | None,
    resultado: str,
    motivo: str,
    referencia_pasarela: str | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> dict:
    entry = {
        "auditoria_id": str(uuid.uuid4()),
        "solicitud_id": str(solicitud_id),
        "propuesta_id": str(propuesta_id) if propuesta_id else None,
        "cliente_id": cliente_id,
        "tecnico_id": tecnico_id,
        "monto_pactado": monto_pactado,
        "metodo_pago": metodo_pago,
        "referencia_pasarela": referencia_pasarela,
        "idempotency_key": idempotency_key,
        "resultado": resultado,
        "motivo": motivo,
        "registrado_en": (now or datetime.now(timezone.utc)).isoformat(),
    }
    _OPEN_REQUEST_AWARD_AUDITS[str(solicitud_id)].append(entry)
    return entry


class AwardProposalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metodo_pago: str | None = Field(
        default=None,
        description="Método de pago confirmado para la contratación ('efectivo' o 'tarjeta'). Si se omite, conserva el de la solicitud.",
    )
    referencia_pasarela: str | None = Field(
        default=None,
        max_length=100,
        description="Referencia o token de autorización de la pasarela cuando el método es tarjeta.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=120,
        description="Clave de idempotencia opcional para evitar doble adjudicación o cobros duplicados.",
    )
    simular_fallo_pago: bool = Field(
        default=False,
        description="Permite simular un rechazo de pasarela en pruebas para verificar que el fallo no cierra la licitación (CA-03).",
    )


# --- HU-31 ST-02: Confirmación del método de pago y adaptador de cobro idempotente ---

_IDEMPOTENT_PAYMENT_CHARGES: dict[str, dict] = {}
_FAILED_GATEWAY_PREFIXES = ("FAIL", "ERROR", "DECLINED", "RECHAZAD")


def get_open_request_payment_charges(solicitud_id: str | None = None) -> list[dict]:
    """HU-31 ST-02 (CA-03, CA-06): Consulta los cobros confirmados registrados por el adaptador idempotente."""
    charges = list(_IDEMPOTENT_PAYMENT_CHARGES.values())
    if solicitud_id is not None:
        return [c for c in charges if c.get("solicitud_id") == str(solicitud_id)]
    return charges


def process_idempotent_award_payment(
    *,
    solicitud_id: str,
    propuesta_id: str,
    cliente_id: str,
    tecnico_id: str,
    monto_pactado: Decimal,
    metodo_pago: str,
    referencia_pasarela: str | None = None,
    idempotency_key: str | None = None,
    simular_fallo_pago: bool = False,
    now: datetime | None = None,
) -> dict:
    """
    HU-31 ST-02 (CA-02, CA-03, CA-06):
    - Con efectivo: no ejecuta cobro digital y confirma inmediatamente en la misma operación.
    - Con tarjeta: valida/autoriza el cobro de forma idempotente; si falla, no confirma la adjudicación.
    - Evita cobros duplicados ante confirmaciones simultáneas o reintentos con la misma clave/solicitud.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    monto_float = float(monto_pactado.quantize(Decimal("0.01")))
    clean_method = (metodo_pago or "").strip().lower()

    if clean_method == "efectivo":
        return {
            "confirmado": True,
            "metodo_pago": "efectivo",
            "cobro_digital_ejecutado": False,
            "reutilizado_idempotente": False,
            "monto": monto_float,
            "moneda": "HNL",
            "referencia_pasarela": None,
            "idempotency_key": idempotency_key,
            "procesado_en": timestamp,
            "motivo": "Pago en efectivo confirmado; no requiere cobro por pasarela.",
        }

    # Clave determinista por licitación/propuesta o idempotency_key explícita (CA-06)
    effective_key = (
        idempotency_key.strip()
        if idempotency_key and idempotency_key.strip()
        else f"award-charge:{solicitud_id}:{propuesta_id}:{monto_pactado:.2f}"
    )

    # Si ya existe un cobro confirmado para esta clave o para esta solicitud, reutilizar sin cobrar dos veces
    existing_charge = _IDEMPOTENT_PAYMENT_CHARGES.get(effective_key)
    if existing_charge is None:
        existing_charge = next(
            (
                c
                for c in _IDEMPOTENT_PAYMENT_CHARGES.values()
                if c.get("solicitud_id") == str(solicitud_id)
                and c.get("propuesta_id") == str(propuesta_id)
                and c.get("confirmado") is True
            ),
            None,
        )
    if existing_charge is not None:
        reused = dict(existing_charge)
        reused["reutilizado_idempotente"] = True
        return reused

    clean_ref = (referencia_pasarela or "").strip()
    if simular_fallo_pago:
        return {
            "confirmado": False,
            "metodo_pago": "tarjeta",
            "cobro_digital_ejecutado": False,
            "reutilizado_idempotente": False,
            "monto": monto_float,
            "moneda": "HNL",
            "referencia_pasarela": clean_ref or None,
            "idempotency_key": effective_key,
            "procesado_en": timestamp,
            "motivo": "El cobro con tarjeta fue rechazado por la pasarela de pago. La licitación continúa abierta.",
        }

    if not clean_ref:
        return {
            "confirmado": False,
            "metodo_pago": "tarjeta",
            "cobro_digital_ejecutado": False,
            "reutilizado_idempotente": False,
            "monto": monto_float,
            "moneda": "HNL",
            "referencia_pasarela": None,
            "idempotency_key": effective_key,
            "procesado_en": timestamp,
            "motivo": "Con tarjeta se requiere confirmación y referencia de cobro de la pasarela antes de adjudicar (CA-03).",
        }

    if clean_ref.upper().startswith(_FAILED_GATEWAY_PREFIXES):
        return {
            "confirmado": False,
            "metodo_pago": "tarjeta",
            "cobro_digital_ejecutado": False,
            "reutilizado_idempotente": False,
            "monto": monto_float,
            "moneda": "HNL",
            "referencia_pasarela": clean_ref,
            "idempotency_key": effective_key,
            "procesado_en": timestamp,
            "motivo": f"El cobro con tarjeta ({clean_ref}) falló en la pasarela. No se cerró la licitación ni se descartaron propuestas.",
        }

    charge_record = {
        "cobro_id": str(uuid.uuid4()),
        "solicitud_id": str(solicitud_id),
        "propuesta_id": str(propuesta_id),
        "cliente_id": cliente_id,
        "tecnico_id": tecnico_id,
        "confirmado": True,
        "metodo_pago": "tarjeta",
        "cobro_digital_ejecutado": True,
        "reutilizado_idempotente": False,
        "monto": monto_float,
        "moneda": "HNL",
        "referencia_pasarela": clean_ref,
        "idempotency_key": effective_key,
        "procesado_en": timestamp,
        "motivo": f"Cobro con tarjeta confirmado exitosamente (ref: {clean_ref}).",
    }
    _IDEMPOTENT_PAYMENT_CHARGES[effective_key] = charge_record
    return charge_record

# --- HU-31 ST-03: Descarte de propuestas restantes y emisión de avisos/eventos de cierre ---

_OPEN_REQUEST_AUTHOR_NOTICES: dict[str, list[dict]] = defaultdict(list)


def get_open_request_author_notices(
    solicitud_id: str,
    *,
    tecnico_id: str | None = None,
) -> list[dict]:
    """HU-31 ST-03 (CA-04): Consulta los avisos generados para los autores de propuestas tras adjudicar."""
    notices = list(_OPEN_REQUEST_AUTHOR_NOTICES.get(str(solicitud_id), []))
    if tecnico_id is not None:
        return [n for n in notices if n.get("autor_tecnico_id") == tecnico_id]
    return notices


def emit_open_request_award_closing_events(
    record: OpenRequest,
    winning_proposal: OpenRequestProposal,
    discarded_proposals: list[OpenRequestProposal],
    *,
    emitted_at: datetime | None = None,
) -> tuple[dict, list[dict]]:
    """
    HU-31 ST-03 (CA-04, CA-05):
    - Genera avisos individuales para cada autor cuya propuesta fue descartada y para el técnico adjudicado.
    - Emite el evento de cierre de licitación a los suscriptores SSE sin exponer dirección exacta ni teléfono en el canal compartido.
    """
    timestamp = (emitted_at or datetime.now(timezone.utc)).isoformat()
    monto_ganador = float(Decimal(str(winning_proposal.monto)).quantize(Decimal("0.01")))
    notices: list[dict] = []

    # Aviso para el técnico adjudicado
    winner_notice = {
        "aviso_id": str(uuid.uuid4()),
        "tipo": "PROPUESTA_ADJUDICADA",
        "solicitud_id": str(record.id),
        "propuesta_id": str(winning_proposal.id),
        "autor_usuario_id": str(winning_proposal.usuario_id),
        "autor_tecnico_id": winning_proposal.tecnico_id,
        "estado_propuesta": "ACEPTADA",
        "estado_solicitud": "ACEPTADA",
        "monto_pactado": monto_ganador,
        "metodo_pago": record.metodo_pago,
        "emitido_en": timestamp,
        "mensaje": (
            f"Tu propuesta por L. {monto_ganador:.2f} fue seleccionada por el cliente. "
            "La contratación ha pasado a estado Aceptada."
        ),
    }
    notices.append(winner_notice)

    # CA-04: Avisos para los autores de las demás propuestas descartadas (sin dirección exacta ni teléfono)
    for disc in discarded_proposals:
        monto_disc = float(Decimal(str(disc.monto)).quantize(Decimal("0.01")))
        discard_notice = {
            "aviso_id": str(uuid.uuid4()),
            "tipo": "PROPUESTA_DESCARTADA_POR_ADJUDICACION",
            "solicitud_id": str(record.id),
            "propuesta_id": str(disc.id),
            "autor_usuario_id": str(disc.usuario_id),
            "autor_tecnico_id": disc.tecnico_id,
            "estado_propuesta": (disc.estado or "RECHAZADA").strip().upper(),
            "estado_solicitud": "ACEPTADA",
            "monto_propuesto": monto_disc,
            "ubicacion_exacta_protegida": True,
            "emitido_en": timestamp,
            "mensaje": (
                f"La licitación fue adjudicada a otro técnico y tu propuesta por L. {monto_disc:.2f} "
                "ha sido descartada."
            ),
        }
        notices.append(discard_notice)

    _OPEN_REQUEST_AUTHOR_NOTICES[str(record.id)].extend(notices)

    closing_event = {
        "evento_id": str(uuid.uuid4()),
        "tipo": "LICITACION_ADJUDICADA",
        "solicitud_id": str(record.id),
        "cliente_id": record.cliente_id,
        "tecnico_adjudicado_id": winning_proposal.tecnico_id,
        "propuesta_adjudicada_id": str(winning_proposal.id),
        "propuestas_descartadas_ids": [str(p.id) for p in discarded_proposals],
        "cantidad_propuestas_descartadas": len(discarded_proposals),
        "monto_pactado": monto_ganador,
        "metodo_pago": record.metodo_pago,
        "estado_solicitud": "ACEPTADA",
        "municipio": record.municipio,
        "sector": record.sector,
        "ubicacion_exacta_protegida": True,
        "emitido_en": timestamp,
        "mensaje": f"Licitación cerrada y adjudicada por L. {monto_ganador:.2f}.",
    }
    _OPEN_REQUEST_EVENTS[str(record.id)].append(closing_event)

    for queue in list(_OPEN_REQUEST_SUBSCRIBERS.get(str(record.id), [])):
        try:
            queue.put_nowait(closing_event)
        except Exception:
            pass

    return closing_event, notices


def award_open_request_proposal_by_owner(
    solicitud_id: str,
    proposal_id: str,
    payload: AwardProposalSchema | None = None,
    claims: dict | None = None,
    db: Session | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-31 ST-01 (CA-01, CA-02, CA-06): Adjudicación transaccional con bloqueo pesimista y unicidad de técnico."""
    if db is None:
        raise HTTPException(500, "Sesión de base de datos no disponible")

    payload = payload or AwardProposalSchema()
    reference_now = now or datetime.now(timezone.utc)

    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    client_user = db.scalar(
        select(User)
        .where(User.firebase_uid == uid)
        .execution_options(populate_existing=True)
    )
    if client_user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    clean_solicitud_id = (solicitud_id or "").strip()
    clean_prop_id = (proposal_id or "").strip()

    # ST-01: Bloqueo pesimista sobre la solicitud abierta para serializar confirmaciones concurrentes
    record = db.scalar(
        select(OpenRequest)
        .where(OpenRequest.id == clean_solicitud_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(404, "No se encontró la solicitud abierta")

    # CA-01: Solo el propietario puede seleccionar una propuesta
    if record.cliente_id != client_user.firebase_uid:
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=clean_prop_id,
            cliente_id=client_user.firebase_uid,
            tecnico_id=None,
            monto_pactado=None,
            metodo_pago=payload.metodo_pago,
            resultado="RECHAZADA_NO_PROPIETARIO",
            motivo="Intento de adjudicación por un usuario distinto al cliente propietario.",
            idempotency_key=payload.idempotency_key,
            now=reference_now,
        )
        raise HTTPException(403, "Solo el cliente propietario puede adjudicar una propuesta de esta licitación")

    if getattr(client_user, "suspendido", False):
        raise HTTPException(403, "Tu cuenta presenta una suspensión activa y no puede adjudicar contrataciones")

    # Bloqueo de todas las propuestas de la solicitud
    all_proposals = list(
        db.scalars(
            select(OpenRequestProposal)
            .where(OpenRequestProposal.solicitud_id == str(record.id))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    proposal = next((p for p in all_proposals if str(p.id) == clean_prop_id), None)
    if proposal is None:
        raise HTTPException(404, "No se encontró la propuesta seleccionada en esta licitación")

    agreed_amount = Decimal(str(proposal.monto)).quantize(Decimal("0.01"))
    agreed_amount_float = float(agreed_amount)

    raw_method = (payload.metodo_pago if payload.metodo_pago is not None else record.metodo_pago) or ""
    metodo_pago = raw_method.strip().lower()
    if metodo_pago not in VALID_PAYMENT_METHODS:
        raise HTTPException(400, "Método de pago inválido. Elige efectivo o tarjeta")

    state_key = _canonical_state_key(record.estado)
    prop_state = (proposal.estado or "PENDIENTE").strip().upper()

    # CA-06: Unicidad e idempotencia ante confirmaciones simultáneas
    if record.tecnico_id is not None or state_key in {"ACEPTADA", "ADJUDICADA"}:
        if record.tecnico_id == proposal.tecnico_id and prop_state == "ACEPTADA":
            audit_entry = _record_award_decision_audit(
                solicitud_id=str(record.id),
                propuesta_id=str(proposal.id),
                cliente_id=client_user.firebase_uid,
                tecnico_id=proposal.tecnico_id,
                monto_pactado=agreed_amount_float,
                metodo_pago=record.metodo_pago,
                referencia_pasarela=record.referencia_pasarela,
                idempotency_key=payload.idempotency_key,
                resultado="REINTENTO_IDEMPOTENTE",
                motivo="Confirmación repetida sobre la misma propuesta ya adjudicada; se evita duplicar adjudicación y cobro.",
                now=reference_now,
            )
            spec_uuid = uuid.UUID(str(record.especialidad_id)) if record.especialidad_id else None
            specialty = db.get(Specialty, spec_uuid) if spec_uuid else None
            return {
                "mensaje": "Esta propuesta ya había sido adjudicada exitosamente.",
                "reintento_idempotente": True,
                "solicitud_id": str(record.id),
                "estado_solicitud": (record.estado or "Aceptada").strip().upper(),
                "tecnico_adjudicado_id": record.tecnico_id,
                "monto_pactado": float(Decimal(str(record.presupuesto)).quantize(Decimal("0.01"))),
                "metodo_pago": record.metodo_pago,
                "referencia_pasarela": record.referencia_pasarela,
                "propuesta_adjudicada": serialize_open_request_proposal(proposal, db),
                "solicitud": serialize_open_request_for_owner(record, specialty, db),
                "auditoria": audit_entry,
            }

        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            idempotency_key=payload.idempotency_key,
            resultado="RECHAZADA_YA_ADJUDICADA",
            motivo=f"Licitación previamente adjudicada al técnico {record.tecnico_id}; se impide doble adjudicación.",
            now=reference_now,
        )
        raise HTTPException(
            409,
            "Esta licitación ya fue adjudicada a otro técnico y no admite una segunda adjudicación.",
        )

    # CA-01: Solo se puede adjudicar cuando la solicitud se encuentra en estado Con ofertas
    if state_key != "CON_OFERTAS":
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            idempotency_key=payload.idempotency_key,
            resultado="RECHAZADA_ESTADO_SOLICITUD_INVALIDO",
            motivo=f"La solicitud está en estado {(record.estado or '').strip()} y requiere estar Con ofertas.",
            now=reference_now,
        )
        raise HTTPException(
            409,
            f"Solo se puede adjudicar una solicitud en estado Con ofertas (estado actual: {(record.estado or '').strip()}).",
        )

    # CA-01: La propuesta debe estar pendiente y activa
    if not proposal.activa or prop_state != "PENDIENTE":
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            idempotency_key=payload.idempotency_key,
            resultado="RECHAZADA_PROPUESTA_NO_PENDIENTE",
            motivo=f"La propuesta seleccionada no está pendiente (estado: {prop_state}).",
            now=reference_now,
        )
        raise HTTPException(
            409,
            f"Solo puedes adjudicar una propuesta pendiente y activa (estado actual de la propuesta: {prop_state}).",
        )

    # CA-01 y Condición transversal: Re-evaluar en tiempo real que el técnico siga elegible
    tech_user = db.scalar(
        select(User)
        .where(User.id == proposal.usuario_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tech_user is None:
        raise HTTPException(404, "No se encontró la cuenta del técnico postulante")

    try:
        tech_profile = require_eligible_technician_for_bidding(
            tech_user,
            db,
            proceso_responsable="ADJUDICACION_LICITACION_ABIERTA",
        )
    except HTTPException as exc:
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            idempotency_key=payload.idempotency_key,
            resultado="RECHAZADA_TECNICO_NO_ELEGIBLE",
            motivo=f"Técnico ya no elegible al momento de adjudicar: {exc.detail}",
            now=reference_now,
        )
        raise HTTPException(
            409,
            f"El técnico seleccionado ya no se encuentra elegible para adjudicación: {exc.detail}",
        ) from None

    if not is_technician_compatible_with_open_request(tech_user.id, record, db):
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            idempotency_key=payload.idempotency_key,
            resultado="RECHAZADA_INCOMPATIBILIDAD_COBERTURA",
            motivo="El técnico ya no mantiene la especialidad o zona de cobertura compatible con la solicitud.",
            now=reference_now,
        )
        raise HTTPException(
            409,
            "El técnico seleccionado ya no mantiene cobertura o especialidad compatible con esta solicitud.",
        )

    # CA-02: Conservar técnico, monto pactado y método de pago; pasar a Aceptada
    # HU-31_ST-02 (CA-02, CA-03, CA-06): Confirmar método de pago y ejecutar cobro idempotente si es tarjeta
    effective_gateway_ref = (
        payload.referencia_pasarela
        if payload.referencia_pasarela is not None
        else (record.referencia_pasarela if metodo_pago == "tarjeta" else None)
    )
    payment_result = process_idempotent_award_payment(
        solicitud_id=str(record.id),
        propuesta_id=str(proposal.id),
        cliente_id=client_user.firebase_uid,
        tecnico_id=proposal.tecnico_id,
        monto_pactado=agreed_amount,
        metodo_pago=metodo_pago,
        referencia_pasarela=effective_gateway_ref,
        idempotency_key=payload.idempotency_key,
        simular_fallo_pago=bool(payload.simular_fallo_pago),
        now=reference_now,
    )

    # CA-03: Si el pago con tarjeta falla, no se cierra la licitación ni se descartan las otras propuestas
    if not payment_result["confirmado"]:
        _record_award_decision_audit(
            solicitud_id=str(record.id),
            propuesta_id=str(proposal.id),
            cliente_id=client_user.firebase_uid,
            tecnico_id=proposal.tecnico_id,
            monto_pactado=agreed_amount_float,
            metodo_pago=metodo_pago,
            referencia_pasarela=effective_gateway_ref,
            idempotency_key=payload.idempotency_key,
            resultado="PAGO_FALLIDO_SIN_CIERRE",
            motivo=payment_result["motivo"],
            now=reference_now,
        )
        raise HTTPException(402, payment_result["motivo"])

    # CA-02 y CA-03: Conservar técnico, monto pactado, método de pago y referencia; pasar a Aceptada
    record.tecnico_id = proposal.tecnico_id
    record.presupuesto = agreed_amount
    record.monto_base = agreed_amount
    ajuste_actual = Decimal(str(record.monto_ajuste or Decimal("0.00"))).quantize(Decimal("0.01"))
    record.monto_total = (agreed_amount + ajuste_actual).quantize(Decimal("0.01"))
    record.metodo_pago = metodo_pago
    record.referencia_pasarela = payment_result["referencia_pasarela"]
    transition_request(db, record, "ACEPTADA", role="CLIENTE", uid=uid, now=reference_now)

    proposal.estado = "ACEPTADA"
    proposal.activa = True
    proposal.actualizada_en = reference_now

    # ST-03 (CA-04): Descartar automáticamente todas las demás propuestas pendientes de la licitación
    discarded_proposals: list[OpenRequestProposal] = []
    for other_prop in all_proposals:
        if str(other_prop.id) == str(proposal.id):
            continue
        other_state = (other_prop.estado or "PENDIENTE").strip().upper()
        if other_prop.activa or other_state == "PENDIENTE":
            other_prop.estado = "RECHAZADA"
            other_prop.activa = False
            other_prop.actualizada_en = reference_now
            motivo_cierre = "Descartada automáticamente por adjudicación de la licitación a otro técnico"
            if not other_prop.justificacion:
                other_prop.justificacion = motivo_cierre
            elif motivo_cierre not in other_prop.justificacion:
                other_prop.justificacion = f"{other_prop.justificacion} | {motivo_cierre}"
            discarded_proposals.append(other_prop)

    notify_open_request_award(db, record, proposal, discarded_proposals, now=reference_now)
    db.commit()
    db.refresh(record)
    db.refresh(proposal)
    for disc_prop in discarded_proposals:
        db.refresh(disc_prop)

    # ST-03 (CA-04): Emitir avisos para los autores de propuestas descartadas y evento de cierre
    closing_event, author_notices = emit_open_request_award_closing_events(
        record,
        proposal,
        discarded_proposals,
        emitted_at=reference_now,
    )

    audit_entry = _record_award_decision_audit(
        solicitud_id=str(record.id),
        propuesta_id=str(proposal.id),
        cliente_id=client_user.firebase_uid,
        tecnico_id=proposal.tecnico_id,
        monto_pactado=agreed_amount_float,
        metodo_pago=record.metodo_pago,
        referencia_pasarela=record.referencia_pasarela,
        idempotency_key=payload.idempotency_key,
        resultado="ADJUDICADA",
        motivo=f"Propuesta adjudicada exitosamente por L. {agreed_amount_float:.2f} con método {record.metodo_pago}.",
        now=reference_now,
    )

    spec_uuid: uuid.UUID | None = None
    try:
        spec_uuid = uuid.UUID(str(record.especialidad_id))
    except ValueError:
        spec_uuid = None
    specialty = db.get(Specialty, spec_uuid) if spec_uuid else None

    return {
        "mensaje": "Licitación adjudicada exitosamente. La contratación ha pasado a estado Aceptada.",
        "reintento_idempotente": False,
        "solicitud_id": str(record.id),
        "estado_solicitud": (record.estado or "Aceptada").strip().upper(),
        "tecnico_adjudicado_id": record.tecnico_id,
        "monto_pactado": agreed_amount_float,
        "metodo_pago": record.metodo_pago,
        "referencia_pasarela": record.referencia_pasarela,
        "cobro": payment_result,
        "propuesta_adjudicada": serialize_open_request_proposal(
            proposal,
            db,
            tech_user=tech_user,
            tech_profile=tech_profile,
        ),
        "propuestas_descartadas": [serialize_open_request_proposal(p, db) for p in discarded_proposals],
        "cantidad_propuestas_descartadas": len(discarded_proposals),
        "avisos_autores": author_notices,
        "evento_cierre": closing_event,
        "solicitud": serialize_open_request_for_owner(record, specialty, db),
        "auditoria": audit_entry,
    }


@router.post("/{solicitud_id}/proposals/{proposal_id}/award", status_code=200)
@router.post("/{solicitud_id}/award/{proposal_id}", status_code=200)
def award_proposal_endpoint(
    solicitud_id: str,
    proposal_id: str,
    response: Response,
    payload: AwardProposalSchema = AwardProposalSchema(),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return award_open_request_proposal_by_owner(solicitud_id, proposal_id, payload, claims, db)

@router.post("/{solicitud_id}/proposals/{proposal_id}/reject", status_code=200)
def reject_proposal_endpoint(
    solicitud_id: str,
    proposal_id: str,
    payload: RejectProposalSchema = RejectProposalSchema(),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return reject_open_request_proposal_by_owner(solicitud_id, proposal_id, payload, claims, db)


from app.service_completion import (  # noqa: E402, F401
    report_open_request_completion_endpoint,
    report_service_completion,
)
