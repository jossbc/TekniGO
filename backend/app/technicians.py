from typing import Literal, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select,delete
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TechnicianProfile, User, UserRole, CoverageZone, Specialty, TechnicianSpecialty
from app.security import get_current_user

router = APIRouter(prefix="/technicians", tags=["Technicians"])

#Opciones válidas para la unidad de cobro
PricingUnit = Literal["POR_HORA", "POR_SERVICIO", "POR_DIA", "POR_PROYECTO"]

#---MODELO DE TARIFAS HU-09--
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

#--MODELO DE ZONAS DE COBERTURA (HU-10)--
class CoverageZoneItem(BaseModel):
    departamento: str = Field(..., min_length=2, max_length=50)
    municipio: str = Field(..., min_length=2, max_length=50)
    sector: str = Field(..., min_length=2, max_length=100)
    class Config:
        from_attributes = True

class TechnicianCoverageUpdate(BaseModel):
    zonas_cobertura: List[CoverageZoneItem] = Field(
        ..., 
        description="Lista de zonas de cobertura (departamentos, municipios y sectores) donde opera el técnico"
    )



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

#--ENDPOINT PARA OBTENER LAS ZONAS ACTUALES DEL TECNICO
@router.get("/me/coverage-zones", response_model=List[CoverageZoneItem], status_code=status.HTTP_200_OK)
def get_coverage_zones(claims=Depends(get_current_user),
    db: Session = Depends(get_db),):
    #BUSCAR ZONAS DEL TECNICO
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
            detail="Solo los técnicos pueden consultar sus zonas de cobertura.",
        )
    zones = db.scalars(
        select(CoverageZone)
        .where(CoverageZone.usuario_id == user.id)
        .order_by(CoverageZone.municipio, CoverageZone.sector)
    ).all()
    
    return zones

#--ENDPOINT PARA ACTUALIZAR/REGISTRAR MULTIPLES ZONAS DE COBERTURAS
@router.patch("/me/coverage-zones", response_model=List[CoverageZoneItem], status_code=status.HTTP_200_OK)
def update_coverage_zones(
    payload: TechnicianCoverageUpdate,
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
            detail="Solo los técnicos pueden configurar zonas de cobertura.",
        )
     # Evitar duplicados exactos (mismo departamento, municipio y sector)
    seen = set()
    cleaned_zones = []
    for item in payload.zonas_cobertura:
        dep = item.departamento.strip()
        mun = item.municipio.strip()
        sec = item.sector.strip()
        key = (dep.lower(), mun.lower(), sec.lower())
        if key not in seen:
            seen.add(key)
            cleaned_zones.append(CoverageZone(usuario_id=user.id, departamento=dep, municipio=mun, sector=sec))
    # Reemplazar las zonas anteriores del técnico por las nuevas
    db.execute(delete(CoverageZone).where(CoverageZone.usuario_id == user.id))
    db.add_all(cleaned_zones)
    db.commit()
    return cleaned_zones


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


# --- ESQUEMAS Y ENDPOINTS HU-12 y HU-13: CATÁLOGO Y FILTROS ---

class CatalogTechnician(BaseModel):
    usuario_id: str
    nombre_completo: str
    especialidades: List[str]
    estado_verificacion: str
    tarifa_base: float
    unidad_cobro: str
    calificacion_promedio: float

    class Config:
        from_attributes = True

@router.get("/catalog", response_model=List[CatalogTechnician], status_code=status.HTTP_200_OK)
def get_technicians_catalog(
    nombre: str = Query(None, description="Filtrar por nombre del técnico"),
    especialidad: str = Query(None, description="Filtrar por nombre de especialidad"),
    municipio: str = Query(None, description="Filtrar por municipio"),
    db: Session = Depends(get_db)
):
    # CA-01 (HU-12): El catálogo debe mostrar técnicos verificados (y disponibles)
    query = (
        select(User, TechnicianProfile)
        .join(TechnicianProfile, User.id == TechnicianProfile.usuario_id)
        .where(
            TechnicianProfile.disponible == True,
            TechnicianProfile.estado_verificacion == "VERIFICADO"
        )
    )

    # Filtros de la HU-13 + Búsqueda por nombre
    if nombre:
        query = query.where(User.nombre_completo.ilike(f"%{nombre}%"))
        
    if especialidad:
        query = query.join(TechnicianSpecialty, User.id == TechnicianSpecialty.usuario_id)\
                     .join(Specialty, TechnicianSpecialty.especialidad_id == Specialty.id)\
                     .where(Specialty.nombre.ilike(f"%{especialidad}%"))
                     
    if municipio:
        query = query.join(CoverageZone, User.id == CoverageZone.usuario_id)\
                     .where(CoverageZone.municipio.ilike(f"%{municipio}%"))

    results = db.execute(query).unique().all()

    catalog = []
    for user, profile in results:
        # Obtener las especialidades reales del técnico (CA-02 de HU-12)
        specs_query = (
            select(Specialty.nombre)
            .join(TechnicianSpecialty, Specialty.id == TechnicianSpecialty.especialidad_id)
            .where(TechnicianSpecialty.usuario_id == user.id)
        )
        specs = db.scalars(specs_query).all()

        catalog.append({
            "usuario_id": str(user.id),
            "nombre_completo": user.nombre_completo,
            "especialidades": list(specs),
            "estado_verificacion": profile.estado_verificacion,
            "tarifa_base": float(profile.tarifa_base) if profile.tarifa_base else 0.0,
            "unidad_cobro": str(profile.unidad_cobro) if profile.unidad_cobro else "POR_HORA",
            "calificacion_promedio": 5.0 # MOCK: Se conectará a valor real en futuros Sprints
        })
    
    return catalog

    # --- ENDPOINT HU-13: VISTA DETALLADA DEL PERFIL DEL TÉCNICO ---

class PublicService(BaseModel):
    id: str
    nombre: str
    descripcion: str

class TechnicianPublicProfile(BaseModel):
    usuario_id: str
    nombre_completo: str
    especialidades: List[str]
    servicios: List[PublicService]
    estado_verificacion: str
    tarifa_base: float
    unidad_cobro: str
    calificacion_promedio: float
    
    class Config:
        from_attributes = True

@router.get("/profile/{user_id}", response_model=TechnicianPublicProfile, status_code=status.HTTP_200_OK)
def get_technician_public_profile(user_id: str, db: Session = Depends(get_db)):
    # Buscar al usuario y su perfil de técnico
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Técnico no encontrado.")
        
    tech_profile = db.get(TechnicianProfile, user_id)
    if not tech_profile or not tech_profile.disponible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El perfil no está disponible.")

    # 1. Obtener Especialidades
    specs_query = select(Specialty.nombre).join(
        TechnicianSpecialty, Specialty.id == TechnicianSpecialty.especialidad_id
    ).where(TechnicianSpecialty.usuario_id == user.id)
    specs = db.scalars(specs_query).all()

    # 2. Obtener Servicios Activos (CA-05 de HU-13)
    # Importamos Service arriba si no lo teníamos: from app.models import Service
    from app.models import Service 
    services_query = select(Service).where(Service.usuario_id == user.id, Service.activo == True)
    services = db.scalars(services_query).all()

    # 3. Formatear la respuesta
    public_services = [
        {"id": str(s.id), "nombre": s.nombre, "descripcion": s.descripcion}
        for s in services
    ]

    return {
        "usuario_id": str(user.id),
        "nombre_completo": user.nombre_completo,
        "especialidades": list(specs),
        "servicios": public_services,
        "estado_verificacion": tech_profile.estado_verificacion,
        "tarifa_base": float(tech_profile.tarifa_base) if tech_profile.tarifa_base else 0.0,
        "unidad_cobro": str(tech_profile.unidad_cobro) if tech_profile.unidad_cobro else "POR_HORA",
        "calificacion_promedio": 5.0 # MOCK: Futuros Sprints
    }