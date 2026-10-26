import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Specialty, SpecialtySuggestion, TechnicianProfile, TechnicianSpecialty, User
from app.security import get_current_user

router = APIRouter()


class SpecialtySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    especialidad_ids: list[uuid.UUID] = Field(min_length=1)


class SuggestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=3, max_length=80)


def get_technician(claims, db):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    technician = db.get(TechnicianProfile, user.id)
    if technician is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")
    return technician


def specialties_of(db, user_id):
    rows = db.scalars(
        select(Specialty)
        .join(TechnicianSpecialty, TechnicianSpecialty.especialidad_id == Specialty.id)
        .where(TechnicianSpecialty.usuario_id == user_id)
        .order_by(Specialty.nombre)
    ).all()
    return [{"id": str(s.id), "nombre": s.nombre} for s in rows]


def suggestions_of(db, user_id):
    rows = db.scalars(
        select(SpecialtySuggestion)
        .where(SpecialtySuggestion.usuario_id == user_id)
        .order_by(SpecialtySuggestion.creado_en.desc())
    ).all()
    return [{"id": str(s.id), "nombre": s.nombre, "estado": s.estado} for s in rows]


# HU-07 ST-02: catálogo de especialidades disponibles (CA-01).
@router.get("/specialties")
def list_specialties(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    specialties = db.scalars(select(Specialty).order_by(Specialty.nombre)).all()
    return [{"id": str(s.id), "nombre": s.nombre} for s in specialties]


# HU-07 ST-02: especialidades guardadas del técnico (CA-04).
@router.get("/technicians/me/specialties")
def my_specialties(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    return specialties_of(db, technician.usuario_id)


# HU-07 ST-02: guardar varias especialidades sin duplicados (CA-02, CA-03, CA-04).
@router.put("/technicians/me/specialties")
def save_my_specialties(data: SpecialtySelection, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    ids = data.especialidad_ids
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "No puedes repetir especialidades")
    found = db.scalars(select(Specialty.id).where(Specialty.id.in_(ids))).all()
    if len(found) != len(ids):
        raise HTTPException(400, "Una o más especialidades no existen")
    try:
        db.execute(delete(TechnicianSpecialty).where(TechnicianSpecialty.usuario_id == technician.usuario_id))
        db.add_all([TechnicianSpecialty(usuario_id=technician.usuario_id, especialidad_id=i) for i in ids])
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "No se pudieron guardar las especialidades. Intenta de nuevo.") from None
    return specialties_of(db, technician.usuario_id)


# Opción "Otro": el técnico propone una especialidad que queda PENDIENTE hasta que un administrador la apruebe.
@router.post("/technicians/me/specialty-suggestions", status_code=201)
def suggest_specialty(data: SuggestionIn, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    nombre = " ".join(data.nombre.split())
    if len(nombre) < 3:
        raise HTTPException(400, "Escribe el nombre de la especialidad")
    if db.scalar(select(Specialty.id).where(func.lower(Specialty.nombre) == nombre.lower())):
        raise HTTPException(409, "Esa especialidad ya está en la lista. Márcala arriba.")
    if db.scalar(select(SpecialtySuggestion.id).where(
        SpecialtySuggestion.usuario_id == technician.usuario_id,
        func.lower(SpecialtySuggestion.nombre) == nombre.lower(),
        SpecialtySuggestion.estado == "PENDIENTE",
    )):
        raise HTTPException(409, "Ya enviaste esa especialidad y está pendiente de revisión")
    db.add(SpecialtySuggestion(usuario_id=technician.usuario_id, nombre=nombre))
    db.commit()
    return suggestions_of(db, technician.usuario_id)


@router.get("/technicians/me/specialty-suggestions")
def my_suggestions(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    return suggestions_of(db, technician.usuario_id)