import uuid
import math
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Service, Specialty, TechnicianSpecialty
from app.security import get_current_user
from app.specialties import get_technician
from app.models import Service, Specialty, TechnicianProfile, TechnicianSpecialty

router = APIRouter()

VALID_UNITS = ("POR_HORA", "POR_SERVICIO", "POR_DIA", "POR_PROYECTO")
def validate_tariff_base(value: float) -> float:
    # HU-09 ST-04 / CA-03: Validar que sea un número real válido y mayor que cero
    if value is None or math.isnan(value) or math.isinf(value):
        raise HTTPException(400, "La tarifa debe ser un número válido")
    if value <= 0:
        raise HTTPException(400, "La tarifa debe ser mayor que cero")
    if value > 999999.99:
        raise HTTPException(400, "La tarifa base no puede exceder L. 999,999.99")
    return round(float(value), 2)
    
def validate_unit(unit: str) -> str:
    # CA-02: Unidad de cobro válida
    if unit not in VALID_UNITS:
        raise HTTPException(400, f"Unidad de cobro inválida. Opciones: {', '.join(VALID_UNITS)}")
    return unit

class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(max_length=100)
    descripcion: str = Field(max_length=1000)
    especialidad_id: uuid.UUID
    tarifa_base: float = Field(default=100.0, description="Tarifa base en Lempiras")    
    unidad_cobro: str = Field(default="POR_HORA", description="Unidad de cobro")

class ServiceTariffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tarifa_base: float = Field(..., description="Tarifa base en Lempiras mayor que cero")
    unidad_cobro: str = Field(..., description="Unidad de cobro")

class ServiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, max_length=100)
    descripcion: str | None = Field(default=None, max_length=1000)
    especialidad_id: uuid.UUID | None = None
    tarifa_base: float | None = None
    unidad_cobro: str | None = None
    activo: bool | None = None


def clean_name(value: str) -> str:
    nombre = " ".join(value.split())
    if len(nombre) < 3:
        raise HTTPException(400, "El nombre del servicio debe tener al menos 3 caracteres")
    return nombre


def clean_description(value: str) -> str:
    descripcion = value.strip()
    if len(descripcion) < 10:
        raise HTTPException(400, "La descripción debe tener al menos 10 caracteres")
    return descripcion


def check_specialty(db, user_id, especialidad_id):
    """CA-02: la especialidad del servicio debe estar registrada en el perfil del técnico."""
    registered = db.scalar(select(TechnicianSpecialty.especialidad_id).where(
        TechnicianSpecialty.usuario_id == user_id,
        TechnicianSpecialty.especialidad_id == especialidad_id,
    ))
    if registered is None:
        raise HTTPException(400, "Elige una de las especialidades que tienes registradas en tu perfil")


def check_duplicate(db, user_id, nombre, especialidad_id, exclude_id=None):
    query = select(Service.id).where(
        Service.usuario_id == user_id,
        Service.especialidad_id == especialidad_id,
        func.lower(Service.nombre) == nombre.lower(),
    )
    if exclude_id:
        query = query.where(Service.id != exclude_id)
    if db.scalar(query):
        raise HTTPException(409, "Ya tienes un servicio con ese nombre en esa especialidad")


def service_out(db, service):
    specialty = db.get(Specialty, service.especialidad_id)
    return {
        "id": str(service.id),
        "nombre": service.nombre,
        "descripcion": service.descripcion,
        "especialidad": {"id": str(specialty.id), "nombre": specialty.nombre} if specialty else None,
        "tarifa_base": float(service.tarifa_base) if service.tarifa_base is not None else 0.0,
        "unidad_cobro": service.unidad_cobro or "POR_HORA",
        "activo": service.activo,
    }


def own_service(db, user_id, service_id):
    service = db.scalar(select(Service).where(Service.id == service_id, Service.usuario_id == user_id))
    if service is None:
        raise HTTPException(404, "No se encontró el servicio")
    return service


# HU-08 ST-02: servicios del técnico, activos e inactivos (CA-03).
@router.get("/technicians/me/services")
def my_services(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    rows = db.scalars(
        select(Service).where(Service.usuario_id == technician.usuario_id).order_by(Service.creado_en.desc())
    ).all()
    return [service_out(db, s) for s in rows]


# HU-08 ST-02: crear un servicio con nombre, descripción y especialidad (CA-01, CA-02).
@router.post("/technicians/me/services", status_code=201)
def create_service(data: ServiceIn, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    nombre = clean_name(data.nombre)
    descripcion = clean_description(data.descripcion)
    tarifa_base = validate_tariff_base(data.tarifa_base)
    unidad_cobro = validate_unit(data.unidad_cobro)
    check_specialty(db, technician.usuario_id, data.especialidad_id)
    check_duplicate(db, technician.usuario_id, nombre, data.especialidad_id)
    service = Service(
        usuario_id=technician.usuario_id,
        especialidad_id=data.especialidad_id,
        nombre=nombre,
        descripcion=descripcion,
        tarifa_base=tarifa_base,
        unidad_cobro=unidad_cobro,
    )
    db.add(service)
    db.commit()
    db.refresh(service)
    return service_out(db, service)


# HU-08 ST-02: editar o desactivar/reactivar un servicio propio (CA-03).
@router.patch("/technicians/me/services/{service_id}")
def update_service(service_id: uuid.UUID, data: ServiceUpdate,
                   claims=Depends(get_current_user), db: Session = Depends(get_db)):
    technician = get_technician(claims, db)
    service = own_service(db, technician.usuario_id, service_id)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No enviaste ningún cambio")
    if any(changes.get(k) is None for k in changes):
        raise HTTPException(400, "Los campos enviados no pueden estar vacíos")

    nombre = clean_name(changes["nombre"]) if "nombre" in changes else service.nombre
    descripcion = clean_description(changes["descripcion"]) if "descripcion" in changes else service.descripcion
    especialidad_id = changes.get("especialidad_id", service.especialidad_id)
    if especialidad_id != service.especialidad_id:
        check_specialty(db, technician.usuario_id, especialidad_id)
    check_duplicate(db, technician.usuario_id, nombre, especialidad_id, exclude_id=service.id)

    service.nombre = nombre
    service.descripcion = descripcion
    service.especialidad_id = especialidad_id
    if "tarifa_base" in changes:
        service.tarifa_base = validate_tariff_base(changes["tarifa_base"])
    if "unidad_cobro" in changes:
        service.unidad_cobro = validate_unit(changes["unidad_cobro"])
    if "activo" in changes:
        service.activo = changes["activo"]
    db.commit()
    db.refresh(service)
    return service_out(db, service)

# HU-08 ST-04 (CA-04): servicios activos de un técnico, para su perfil público.
@router.get("/technicians/{usuario_id}/services")
def public_services(usuario_id: uuid.UUID, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    if db.get(TechnicianProfile, usuario_id) is None:
        raise HTTPException(404, "No se encontró al técnico")
    rows = db.scalars(
        select(Service)
        .where(Service.usuario_id == usuario_id, Service.activo.is_(True))
        .order_by(Service.nombre)
    ).all()
    result = []
    for s in rows:
        specialty = db.get(Specialty, s.especialidad_id)
        result.append({
            "id": str(s.id),
            "nombre": s.nombre,
            "descripcion": s.descripcion,
            "especialidad": specialty.nombre if specialty else None,
            "tarifa_base": float(s.tarifa_base) if s.tarifa_base is not None else 0.0,
            "unidad_cobro": s.unidad_cobro or "POR_HORA",
        })
    return result


# HU-09 ST-02: Endpoint específico de actualización de tarifas del servicio (CA-01, CA-02, CA-03, CA-05).
@router.patch("/technicians/me/services/{service_id}/tariff")
def update_service_tariff(
    service_id: uuid.UUID,
    data: ServiceTariffUpdate,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    technician = get_technician(claims, db)
    service = own_service(db, technician.usuario_id, service_id)
    service.tarifa_base = validate_tariff_base(data.tarifa_base)
    service.unidad_cobro = validate_unit(data.unidad_cobro)
    db.commit()
    db.refresh(service)
    return service_out(db, service)