"""Demand indicators for the administration dashboard (HU-54)."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
import json
from pathlib import Path
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import String, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _require_admin
from app.models import (
    DirectRequest, OpenRequest, RequestStateEvent, Service, Specialty,
    TEGUCIGALPA_TZ, ensure_utc_datetime,
)
from app.request_states import state_key
from app.security import get_current_user

router = APIRouter(prefix="/admin/reports", tags=["Reportes de administración"])
UNCLASSIFIED = "Sin clasificar"
UNKNOWN_LOCATION = "Sin identificar"
WEEKDAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
CONTRACTED = ("ACEPTADA", "ASIGNADA", "EN PROCESO", "COMPLETADA")


def _location_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", " ".join(value.split()).casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


# Snapshot of the same catalog shipped with the request forms. The containers
# for frontend and backend have separate build contexts.
LOCATIONS = json.loads(Path(__file__).with_name("honduras_locations.json").read_text(encoding="utf-8"))
MUNICIPALITIES = defaultdict(list)
for department, names in LOCATIONS.items():
    for name in names:
        MUNICIPALITIES[_location_key(name)].append((department, name))


def _location(value: str | None) -> tuple[str, str]:
    matches = MUNICIPALITIES.get(_location_key(value or ""), ())
    if len(matches) == 1:
        return matches[0]
    if matches:
        return UNKNOWN_LOCATION, matches[0][1]
    return UNKNOWN_LOCATION, UNKNOWN_LOCATION


def _request_columns(model, modality: str, specialty, start: datetime, end: datetime):
    state = func.replace(func.upper(func.trim(RequestStateEvent.estado)), "_", " ")
    contracted = select(RequestStateEvent.id).where(
        RequestStateEvent.modalidad == modality,
        RequestStateEvent.solicitud_id == cast(model.id, String),
        state.in_(CONTRACTED),
    ).exists()
    return select(
        literal(modality), model.creada_en, model.municipio, specialty, model.estado, contracted,
    ).where(model.creada_en >= start, model.creada_en < end)


def _rank(counts: Counter) -> list[dict]:
    return [
        {"nombre": name, "solicitudes": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _leaders(ranking: list[dict]) -> list[dict]:
    return [item for item in ranking if item["solicitudes"] == ranking[0]["solicitudes"]]


def get_demand_report(
    db: Session,
    desde: date,
    hasta: date,
    oficio: str | None = None,
    departamento: str | None = None,
    municipio: str | None = None,
) -> dict:
    days = (hasta - desde).days + 1
    if not 1 <= days <= 366 or hasta == date.max:
        raise HTTPException(422, "Selecciona un rango válido de hasta 366 días.")
    if oficio not in (None, "sin-clasificar"):
        try:
            oficio = str(UUID(oficio))
        except ValueError:
            raise HTTPException(422, "El oficio seleccionado no es válido.") from None
    if departamento and departamento not in LOCATIONS and departamento != UNKNOWN_LOCATION:
        raise HTTPException(422, "El departamento seleccionado no es válido.")
    if municipio and municipio != UNKNOWN_LOCATION:
        matches = MUNICIPALITIES.get(_location_key(municipio))
        if not matches:
            raise HTTPException(422, "El municipio seleccionado no es válido.")
        municipio = matches[0][1]

    start = datetime.combine(desde, time.min, TEGUCIGALPA_TZ).astimezone(timezone.utc)
    end = datetime.combine(hasta + timedelta(days=1), time.min, TEGUCIGALPA_TZ).astimezone(timezone.utc)
    specialties = {str(row.id): row.nombre for row in db.execute(select(Specialty.id, Specialty.nombre))}
    direct = _request_columns(DirectRequest, "DIRECTA", Service.especialidad_id, start, end).outerjoin(
        Service, Service.id == DirectRequest.servicio_id
    )
    opened = _request_columns(OpenRequest, "ABIERTA", OpenRequest.especialidad_id, start, end)
    counts = Counter()
    trades = Counter()
    departments = Counter()
    cities = Counter()
    daily = {desde + timedelta(days=offset): 0 for offset in range(days)}

    for modality, created, city, specialty, status, accepted in db.execute(
        union_all(direct, opened).execution_options(yield_per=500)
    ):
        department, city = _location(city)
        specialty_id = str(specialty)
        key = specialty_id if specialty_id in specialties else "sin-clasificar"
        if (oficio and key != oficio) or (departamento and department != departamento) or (municipio and city != municipio):
            continue
        counts[modality] += 1
        normalized_status = state_key(status).replace("-", " ")
        if accepted or normalized_status in CONTRACTED:
            counts["contratadas"] += 1
        if normalized_status == "COMPLETADA":
            counts["completadas"] += 1
        trades[specialties.get(key, UNCLASSIFIED)] += 1
        departments[department] += 1
        cities[f"{city} · {department}"] += 1
        local_day = ensure_utc_datetime(created).astimezone(TEGUCIGALPA_TZ).date()
        daily[local_day] += 1

    totals_by_weekday = Counter()
    weekday_occurrences = Counter()
    for day, count in daily.items():
        totals_by_weekday[day.weekday()] += count
        weekday_occurrences[day.weekday()] += 1
    weekday_averages = {
        index: Fraction(totals_by_weekday[index], occurrences)
        for index, occurrences in weekday_occurrences.items()
    }
    weekdays = [
        {"nombre": name, "solicitudes": totals_by_weekday[index], "dias": weekday_occurrences[index],
         "promedio": round(float(weekday_averages[index]), 2) if index in weekday_averages else None}
        for index, name in enumerate(WEEKDAYS)
    ]
    maximum = max(weekday_averages.values())
    trade_ranking = _rank(trades)
    city_ranking = _rank(cities)
    filters = {
        "desde": desde, "hasta": hasta, "oficio": oficio,
        "departamento": departamento, "municipio": municipio,
    }
    return {
        "filtros": filters,
        "opciones": {
            "oficios": [{"id": item, "nombre": name} for item, name in sorted(
                {**specialties, "sin-clasificar": UNCLASSIFIED}.items(), key=lambda item: item[1].casefold()
            )],
            "ubicaciones": {**LOCATIONS, UNKNOWN_LOCATION: sorted({
                UNKNOWN_LOCATION, *(matches[0][1] for matches in MUNICIPALITIES.values() if len(matches) > 1),
            })},
        },
        "resumen": {
            "total": counts["DIRECTA"] + counts["ABIERTA"],
            "directas": counts["DIRECTA"], "abiertas": counts["ABIERTA"],
            "contratadas": counts["contratadas"], "completadas": counts["completadas"],
        },
        "oficios": trade_ranking,
        "departamentos": _rank(departments),
        "municipios": city_ranking,
        "dias": [{"fecha": day, "solicitudes": count} for day, count in daily.items()],
        "semana": weekdays,
        "destacados": {
            "oficios": _leaders(trade_ranking),
            "municipios": _leaders(city_ranking),
            "semana": [weekdays[index] for index, average in weekday_averages.items() if maximum and average == maximum],
        },
    }


@router.get("/demand")
def demand_report(
    response: Response,
    desde: date | None = None,
    hasta: date | None = None,
    oficio: str | None = Query(None, max_length=36),
    departamento: str | None = Query(None, max_length=80),
    municipio: str | None = Query(None, max_length=80),
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(claims, db)
    response.headers["Cache-Control"] = "no-store, private"
    last_day = hasta or datetime.now(TEGUCIGALPA_TZ).date()
    first_day = desde or max(last_day, date.min + timedelta(days=29)) - timedelta(days=29)
    return get_demand_report(db, first_day, last_day, oficio, departamento, municipio)
