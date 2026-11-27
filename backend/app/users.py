import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth, exceptions
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ACCOUNT_DELETED, Service, TechnicianProfile, User, UserRole
from app.security import TOKEN_CLOCK_SKEW_SECONDS, get_current_user
from app.technician_activation import sync_technician_verification_state

router = APIRouter()


def _require_live_account(user: User | None) -> User:
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if user.estado_identidad == ACCOUNT_DELETED:
        raise HTTPException(410, "Esta cuenta de TekniGO fue eliminada.")
    return user


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
        if len(value) > 254 or not re.fullmatch(r"[^@\s]+@(?:[a-z0-9-]+\.)+(?:com|net|org|edu|gov|info|biz|io|dev|app|[a-z]{2})", value):
            raise ValueError("Ingresa un correo válido")
        return value


def profile(user):
    return {"id": str(user.id), "nombre_completo": user.nombre_completo,
            "email": user.correo, "roles": sorted(r.rol for r in user.roles),
            "estado_identidad": user.estado_identidad}


def own_profile(user):
    # Datos personales completos: solo se devuelven a la propia persona (HU-06).
    return {**profile(user), "dni": user.dni, "telefono": user.telefono}


class RoleAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rol: Literal["CLIENTE", "TECNICO"]


def ensure_role(db: Session, user: User, role: Literal["CLIENTE", "TECNICO"]) -> None:
    """Agrega el rol y su perfil técnico si faltan; el endpoint confirma la transacción."""
    if not db.get(UserRole, (user.id, role)):
        db.add(UserRole(usuario_id=user.id, rol=role))
    if role == "TECNICO" and not db.get(TechnicianProfile, user.id):
        db.add(TechnicianProfile(usuario_id=user.id))


@router.post("/users/me/roles")
def add_role(data: RoleAddition, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = _require_live_account(db.scalar(select(User).where(User.firebase_uid == claims["uid"]).with_for_update()))
    try:
        ensure_role(db, user, data.rol)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "No pudimos agregar el rol. Actualiza tu cuenta e intenta nuevamente.") from None
    return profile(user)


@router.post("/users/register", status_code=201)
def register(data: Registration, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]).with_for_update())
    if user:
        _require_live_account(user)
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
        ensure_role(db, user, data.rol)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El DNI o teléfono ya está registrado, o hubo un registro simultáneo. Revisa los datos e intenta de nuevo.") from None
    return profile(user)

@router.get("/users/me")
def me(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    return profile(_require_live_account(user))

@router.get("/users/me/personal")
def my_personal_data(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    return own_profile(_require_live_account(user))

def ensure_free(db, user, column, value, message):
    taken = db.scalar(select(User.id).where(column == value, User.id != user.id))
    if taken:
        raise HTTPException(409, message)


# HU-06 ST-02: actualización del perfil (teléfono y correo).
@router.patch("/users/me")
def update_me(data: ProfileUpdate, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = _require_live_account(db.scalar(select(User).where(User.firebase_uid == claims["uid"]).with_for_update()))
    if data.telefono is None and data.correo is None:
        raise HTTPException(400, "No enviaste ningún dato para actualizar")

    phone_changed = data.telefono is not None and data.telefono != user.telefono
    email_changed = data.correo is not None and data.correo != user.correo.lower()
    if not phone_changed and not email_changed:
        return own_profile(user)

    if phone_changed:
        ensure_free(db, user, User.telefono, data.telefono, "Este teléfono ya está registrado")
    if email_changed:
        ensure_free(db, user, func.lower(User.correo), data.correo, "Ese correo ya está en uso")

    try:
        if phone_changed:
            user.telefono = data.telefono
            # Condición transversal y CA-04: cambiar de teléfono invalida su verificación previa
            user.telefono_verificado = False
            tech_profile = db.get(TechnicianProfile, user.id)
            if tech_profile is not None:
                sync_technician_verification_state(
                    user,
                    tech_profile,
                    db,
                    proceso_responsable="CAMBIO_TELEFONO",
                    actor_usuario_id=user.id,
                    causa_override="Cambio de número telefónico: se invalidó la verificación previa del teléfono.",
                )
        if email_changed:
            user.correo = data.correo
        db.flush()
        if email_changed:
            auth.update_user(claims["uid"], email=data.correo, email_verified=False)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ese teléfono o correo ya está registrado. Revisa los datos e intenta de nuevo.") from None
    except auth.EmailAlreadyExistsError:
        db.rollback()
        raise HTTPException(409, "Ese correo ya está en uso") from None
    except exceptions.FirebaseError:
        db.rollback()
        raise HTTPException(502, "No pudimos actualizar tu correo en este momento. Intenta de nuevo.") from None
    return own_profile(user)


@router.post("/auth/logout", status_code=204)
def logout(claims=Depends(get_current_user)):
    auth.revoke_refresh_tokens(claims["uid"])


@router.delete("/users/me", status_code=204)
def delete_my_account(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    auth_time = claims.get("auth_time")
    if not isinstance(auth_time, (float, int)) or not -TOKEN_CLOCK_SKEW_SECONDS <= time.time() - auth_time <= 300:
        raise HTTPException(403, "Confirma tu contraseña antes de eliminar la cuenta.")

    uid = claims["uid"]
    user = db.scalar(select(User).where(User.firebase_uid == uid).with_for_update().execution_options(populate_existing=True))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if user.estado_identidad != ACCOUNT_DELETED:
        # Keep the internal references used by requests and audit records.
        user.nombre_completo = "Cuenta eliminada"
        user.correo = f"{user.id.hex}@cuenta-eliminada.invalid"
        user.dni = f"X{user.id.hex[:12]}"
        user.telefono = f"X{user.id.hex[:15]}"
        user.telefono_verificado = False
        user.estado_identidad = ACCOUNT_DELETED
        technician = db.get(TechnicianProfile, user.id)
        if technician is not None:
            technician.disponible = False
            technician.suspendido = True
            db.execute(update(Service).where(Service.usuario_id == user.id).values(activo=False))
        db.execute(delete(UserRole).where(UserRole.usuario_id == user.id))
        db.commit()

    try:
        auth.delete_user(uid)
    except auth.UserNotFoundError:
        pass
    except exceptions.FirebaseError:
        raise HTTPException(503, "Se eliminó el perfil de TekniGO. Intenta de nuevo para terminar de quitar el acceso.") from None
