import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth, exceptions
from pydantic import BaseModel, ConfigDict, Field, field_validator
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



class ProfileUpdate(BaseModel):

    model_config = ConfigDict(extra="forbid")

    telefono: str | None = None
    correo: str | None = None

    @field_validator("telefono")
    @classmethod
    def phone(cls, value):
        if value is None:
            return value
        value = re.sub(r"[\s()-]", "", value)
        if not value:
            raise ValueError("Ingresa tu teléfono")
        if not re.fullmatch(r"(?:\+504)?[389][0-9]{7}", value):
            raise ValueError("Ingresa un celular hondureño de 8 dígitos que comience con 9, 8 o 3.")
        return value if value.startswith("+504") else "+504" + value

    @field_validator("correo")
    @classmethod
    def email(cls, value):
        if value is None:
            return value
        value = value.strip().lower()
        if not value:
            raise ValueError("Ingresa tu correo")
        if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Ingresa un correo válido")
        return value


def profile(user):
    return {"id": str(user.id), "nombre_completo": user.nombre_completo,
            "email": user.correo, "dni": user.dni, "telefono": user.telefono,
            "roles": sorted(r.rol for r in user.roles),
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


# HU-06 ST-02: actualización del perfil (teléfono y correo).
@router.patch("/users/me")
def update_me(data: ProfileUpdate, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if data.telefono is None and data.correo is None:
        raise HTTPException(400, "No enviaste ningún dato para actualizar")

    phone_changed = data.telefono is not None and data.telefono != user.telefono
    email_changed = data.correo is not None and data.correo != user.correo.lower()
    if not phone_changed and not email_changed:
        return profile(user)

    try:
        if phone_changed:
            user.telefono = data.telefono
        if email_changed:
            user.correo = data.correo
        db.flush()
        if email_changed:
            auth.update_user(claims["uid"], email=data.correo)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este teléfono ya está registrado") from None
    except auth.EmailAlreadyExistsError:
        db.rollback()
        raise HTTPException(409, "Ese correo ya está en uso") from None
    except exceptions.FirebaseError:
        db.rollback()
        raise HTTPException(502, "No pudimos actualizar tu correo en este momento. Intenta de nuevo.") from None
    return profile(user)


@router.post("/auth/logout", status_code=204)
def logout(claims=Depends(get_current_user)):
    # Política inicial explícita: cerrar todas las sesiones de esta cuenta.
    auth.revoke_refresh_tokens(claims["uid"])