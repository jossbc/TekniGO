import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TechnicianProfile, User, UserRole
from app.security import get_current_user

router = APIRouter()


class Registration(BaseModel):
    nombre_completo: str = Field(min_length=2, max_length=150)
    dni: str
    telefono: str
    rol: Literal["CLIENTE", "TECNICO"]

    @field_validator("nombre_completo")
    @classmethod
    def name(cls, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Ingresa tu nombre completo")
        return value

    @field_validator("dni")
    @classmethod
    def identity(cls, value):
        value = re.sub(r"[\s-]", "", value)
        if not re.fullmatch(r"[0-9]{13}", value):
            raise ValueError("El DNI hondureño debe contener 13 dígitos")
        return value

    @field_validator("telefono")
    @classmethod
    def phone(cls, value):
        value = re.sub(r"[\s()-]", "", value)
        if not re.fullmatch(r"(?:\+504)?[389][0-9]{7}", value):
            raise ValueError("Ingresa un celular hondureño de 8 dígitos que comience con 9, 8 o 3. Puedes usar guion y el prefijo +504.")
        return value if value.startswith("+504") else "+504" + value


def profile(user):
    return {"id": str(user.id), "nombre_completo": user.nombre_completo,
            "email": user.correo, "roles": sorted(r.rol for r in user.roles),
            "estado_identidad": user.estado_identidad}


@router.post("/users/register", status_code=201)
def register(data: Registration, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if user:
        if user.dni != data.dni or user.telefono != data.telefono:
            raise HTTPException(409, "La cuenta ya tiene otros datos de identidad. Usa su flujo de actualización.")
    else:
        email = claims.get("email")
        if not email:
            raise HTTPException(400, "La identidad de Firebase requiere correo electrónico")
        user = User(firebase_uid=claims["uid"], correo=email, nombre_completo=data.nombre_completo,
                    dni=data.dni, telefono=data.telefono)
        db.add(user)
    try:
        db.flush()
        existing = db.get(UserRole, (user.id, data.rol))
        if not existing:
            db.add(UserRole(usuario_id=user.id, rol=data.rol))
        if data.rol == "TECNICO" and not db.get(TechnicianProfile, user.id):
            db.add(TechnicianProfile(usuario_id=user.id))
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El DNI o teléfono ya está registrado, o hubo un registro simultáneo. Revisa los datos e intenta de nuevo.") from None
    return profile(user)


@router.get("/users/me")
def me(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    return profile(user)


@router.post("/auth/logout", status_code=204)
def logout(claims=Depends(get_current_user)):
    # Política inicial explícita: cerrar todas las sesiones de esta cuenta.
    auth.revoke_refresh_tokens(claims["uid"])
