"""HU-50 administration of the existing specialties catalog."""
import os
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _require_admin
from app.models import CatalogChange, OperationalSettings, Specialty, SpecialtySuggestion, SuggestedTariff, User
from app.security import get_current_user
from app.services import VALID_UNITS, validate_unit

router = APIRouter(prefix="/admin/catalog", tags=["Administración del catálogo"])


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1, max_length=80)
    activa: bool = True

    @field_validator("nombre", mode="before")
    @classmethod
    def normalize_spaces(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value


class TariffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    promedio: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class SuggestionDecision(BaseModel):
    estado: Literal["APROBADA", "RECHAZADA"]


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comision_porcentaje: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    limite_deuda: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    minimo_retiro: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


def _demo_enabled():
    return os.getenv("TEKNIGO_DEMO", "").strip().lower() == "true"


def _category(row):
    return {"id": str(row.id), "nombre": row.nombre, "activa": row.activa}


def _suggestion(row):
    return {"id": str(row.id), "nombre": row.nombre, "estado": row.estado}


def _matching_category(db, name, excluded_id=None):
    key = " ".join(name.split()).casefold()
    return next((row for row in db.scalars(select(Specialty))
                 if row.id != excluded_id and " ".join(row.nombre.split()).casefold() == key), None)


def _settings(row):
    return {
        **{key: str(getattr(row, key)) if getattr(row, key) is not None else None
           for key in SettingsInput.model_fields},
        "limite_sin_liquidar": row.limite_sin_liquidar,
        "valores_demo": row.valores_demo,
    }


def get_operational_settings(db: Session, *, lock: bool = False) -> OperationalSettings:
    query = select(OperationalSettings).where(OperationalSettings.id == 1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(503, "Falta inicializar la configuración operativa.")
    return row


def _audit(db, admin, resource, previous, current):
    if previous != current:
        db.add(CatalogChange(recurso=resource, anterior=previous, nuevo=current, administrador_id=admin.id))


@router.get("")
def get_catalog(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(claims, db)
    categories = db.scalars(select(Specialty).order_by(Specialty.nombre)).all()
    tariffs = db.scalars(select(SuggestedTariff).order_by(SuggestedTariff.especialidad_id, SuggestedTariff.unidad)).all()
    pending = db.scalars(select(SpecialtySuggestion)
                         .where(SpecialtySuggestion.estado == "PENDIENTE")
                         .order_by(SpecialtySuggestion.creado_en, SpecialtySuggestion.id)).all()
    return {
        "categorias": [_category(row) for row in categories],
        "tarifas": [{"especialidad_id": str(row.especialidad_id), "unidad": row.unidad,
                     "promedio": str(row.promedio)} for row in tariffs],
        "unidades": sorted(VALID_UNITS),
        "configuracion": _settings(get_operational_settings(db)),
        "demo_habilitada": _demo_enabled(),
        "sugerencias": [_suggestion(row) for row in pending],
    }


@router.get("/history")
def get_history(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(claims, db)
    rows = db.execute(
        select(CatalogChange, User.nombre_completo).join(User, User.id == CatalogChange.administrador_id)
        .order_by(CatalogChange.id.desc()).limit(100)
    ).all()
    return [{"id": row.id, "recurso": row.recurso, "anterior": row.anterior, "nuevo": row.nuevo,
             "administrador_id": str(row.administrador_id), "administrador": name, "fecha": row.fecha.isoformat()}
            for row, name in rows]


def _stage_category(db, admin, data, row=None):
    previous = _category(row) if row is not None else None
    if _matching_category(db, data.nombre, row.id if row else None):
        raise HTTPException(409, "Ya existe una categoría con ese nombre.")
    if row is None:
        row = Specialty()
        db.add(row)
    row.nombre, row.activa = data.nombre, data.activa
    db.flush()
    current = _category(row)
    _audit(db, admin, f"categoria:{row.id}", previous, current)
    return current


def _save_category(db, admin, data, row=None):
    try:
        current = _stage_category(db, admin, data, row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe una categoría con ese nombre.") from None
    return current


@router.put("/suggestions/{suggestion_id}")
def review_suggestion(suggestion_id: uuid.UUID, data: SuggestionDecision, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    row = db.scalar(select(SpecialtySuggestion).where(SpecialtySuggestion.id == suggestion_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "No existe la sugerencia.")
    if row.estado == data.estado:
        return _suggestion(row)
    if row.estado != "PENDIENTE":
        raise HTTPException(409, "La sugerencia ya fue revisada.")

    previous = _suggestion(row)
    try:
        if data.estado == "APROBADA":
            existing = _matching_category(db, row.nombre)
            if existing is None:
                _stage_category(db, admin, CategoryInput(nombre=row.nombre))
            elif not existing.activa:
                raise HTTPException(409, "El oficio ya existe y está desactivado. Actívalo antes de aprobar esta sugerencia.")
        row.estado = data.estado
        current = _suggestion(row)
        _audit(db, admin, f"sugerencia:{row.id}", previous, current)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El catálogo cambió durante la revisión. Actualiza e intenta nuevamente.") from None
    return current


@router.post("/categories", status_code=201)
def create_category(data: CategoryInput, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    return _save_category(db, _require_admin(claims, db), data)


def _lock_category(db, category_id):
    row = db.scalar(select(Specialty).where(Specialty.id == category_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "No existe la categoría.")
    return row


@router.put("/categories/{category_id}")
def update_category(category_id: uuid.UUID, data: CategoryInput, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    return _save_category(db, admin, data, _lock_category(db, category_id))


@router.put("/categories/{category_id}/tariffs/{unit}")
def save_tariff(category_id: uuid.UUID, unit: str, data: TariffInput, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    normalized = validate_unit(unit.strip().upper())
    _lock_category(db, category_id)
    row = db.get(SuggestedTariff, (category_id, normalized))
    previous = {"promedio": str(row.promedio)} if row is not None else None
    if row is None:
        row = SuggestedTariff(especialidad_id=category_id, unidad=normalized)
        db.add(row)
    row.promedio = data.promedio.quantize(Decimal("0.01"))
    current = {"promedio": str(row.promedio)}
    _audit(db, admin, f"tarifa:{category_id}:{normalized}", previous, current)
    db.commit()
    return current


def _save_settings(db, admin, row, data, *, demo):
    previous = _settings(row)
    for key, value in data.model_dump().items():
        setattr(row, key, value.quantize(Decimal("0.01")))
    row.valores_demo = demo
    current = _settings(row)
    _audit(db, admin, "configuracion_operativa", previous, current)
    db.commit()
    return current


@router.put("/settings")
def save_settings(data: SettingsInput, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    return _save_settings(db, admin, get_operational_settings(db, lock=True), data, demo=_demo_enabled())


@router.post("/settings/demo")
def seed_demo_settings(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    admin = _require_admin(claims, db)
    if not _demo_enabled():
        raise HTTPException(403, "Los ejemplos solo se pueden sembrar en un entorno de demostración.")
    row = get_operational_settings(db, lock=True)
    if any(getattr(row, key) is not None for key in SettingsInput.model_fields):
        raise HTTPException(409, "Ya existe una configuración. Edítala para cambiar sus valores.")
    data = SettingsInput(comision_porcentaje=10, limite_deuda=500, minimo_retiro=200)
    return _save_settings(db, admin, row, data, demo=True)
