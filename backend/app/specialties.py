from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Specialty
from app.security import get_current_user

router = APIRouter()


# HU-07 ST-02: catálogo de especialidades disponibles (CA-01).
@router.get("/specialties")
def list_specialties(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    specialties = db.scalars(select(Specialty).order_by(Specialty.nombre)).all()
    return [{"id": str(s.id), "nombre": s.nombre} for s in specialties]