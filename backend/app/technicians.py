from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TechnicianProfile, User, UserRole
from app.security import get_current_user

router = APIRouter(prefix="/technicians", tags=["Technicians"])

#Opciones válidas para la unidad de cobro
PricingUnit = Literal["POR_HORA", "POR_SERVICIO", "POR_DIA", "POR_PROYECTO"]

class TariffUpdate(BaseModel):
    tarifa_base: float = Field(..., description = "Tarifa base en Lempiras")
    unidad_cobro: PricingUnit = Field(..., description = "Unidad de Cobro Elegida")

    @field_validator("tarifa_base")
    @classmethod
    def validate_positive_tariff(cls, value:float) -> float:
        #Validación de tarifa > 0
        if value <= 0:
            raise ValueError("La tarifa debe ser mayor que cero (0)")
        return value

class TariffResponse(BaseModel):
        usuario_id: str
        tarifa_base: float
        unidad_cobro: str

        class Config:
            from_attributes = True

    #---ENDPOINT HU-09 ACTUALIZACIÓN DE TARIFAS--
@router.patch("/me/tariff", response_model = TariffResponse, status_code = status.HTTP_200_OK)
def update_my_tariff(
        data: TariffUpdate,
        claims = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    #PERMITIR A UN USUARIO CON ROL TECNICO ACTUALIZAR SU TARIFA Y UNIDAD DE COBRO
    #Obtener usuario autenticado
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail = "Usuario no encontrado. Completa tu registro"
        )
    
    #VERIFICAR QUE EL USUARIO TENGA ROL DE TÉCNICO
    has_technician_role = any(role.rol == "TECNICO" for role in user.roles)
    if not has_technician_role:
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail = "Solo los usuarios con rol TECNICO pueden configurar tarifas base."
        )
    #BUSCAR PERFIL DEL TECNICO
    tech_profile = db.get(TechnicianProfile, user.id)
    if not tech_profile:
        #Si por alguna razón no existia el perfil, lo creamos
        tech_profile = TechnicianProfile(usuario_id = user.id)
        db.add(tech_profile)
    #Actualizar Tarifa y Unidad de Cobro
    tech_profile.tarifa_base = data.tarifa_base
    tech_profile.unidad_cobro = data.unidad_cobro
    db.commit()
    db.refresh(tech_profile)
    return TariffResponse(
        usuario_id = str(tech_profile.usuario_id),
        tarifa_base = float(tech_profile.tarifa_base),
        unidad_cobro = str(tech_profile.unidad_cobro)
    )


# --- ESQUEMAS Y ENDPOINTS HU-11: GESTIONAR DISPONIBILIDAD ---
class AvailabilityUpdate(BaseModel):
    disponible: bool = Field(..., description="Estado de disponibilidad del técnico (True = Disponible, False = No disponible)")


class AvailabilityResponse(BaseModel):
    usuario_id: str
    disponible: bool
    estado_verificacion: str
    puede_recibir_contrataciones: bool

    class Config:
        from_attributes = True


@router.get("/me/availability", response_model=AvailabilityResponse, status_code=status.HTTP_200_OK)
def get_my_availability(
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado. Completa tu registro.",
        )

    has_technician_role = any(role.rol == "TECNICO" for role in user.roles)
    if not has_technician_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los usuarios con rol TECNICO pueden gestionar disponibilidad.",
        )

    tech_profile = db.get(TechnicianProfile, user.id)
    if not tech_profile:
        tech_profile = TechnicianProfile(usuario_id=user.id, disponible=False)
        db.add(tech_profile)
        db.commit()
        db.refresh(tech_profile)

    # CA-04 y condiciones transversales: disponible y verificado son dimensiones distintas,
    # pero no disponible impide recibir nuevas contrataciones.
    puede_contratar = bool(tech_profile.disponible and tech_profile.estado_verificacion == "VERIFICADO")

    return AvailabilityResponse(
        usuario_id=str(tech_profile.usuario_id),
        disponible=bool(tech_profile.disponible),
        estado_verificacion=str(tech_profile.estado_verificacion),
        puede_recibir_contrataciones=puede_contratar,
    )


@router.patch("/me/availability", response_model=AvailabilityResponse, status_code=status.HTTP_200_OK)
def update_my_availability(
    data: AvailabilityUpdate,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado. Completa tu registro.",
        )

    has_technician_role = any(role.rol == "TECNICO" for role in user.roles)
    if not has_technician_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los usuarios con rol TECNICO pueden cambiar su disponibilidad.",
        )

    tech_profile = db.get(TechnicianProfile, user.id)
    if not tech_profile:
        tech_profile = TechnicianProfile(usuario_id=user.id)
        db.add(tech_profile)

    tech_profile.disponible = data.disponible
    db.commit()
    db.refresh(tech_profile)

    puede_contratar = bool(tech_profile.disponible and tech_profile.estado_verificacion == "VERIFICADO")

    return AvailabilityResponse(
        usuario_id=str(tech_profile.usuario_id),
        disponible=bool(tech_profile.disponible),
        estado_verificacion=str(tech_profile.estado_verificacion),
        puede_recibir_contrataciones=puede_contratar,
    )