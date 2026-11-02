import uuid
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    TechnicianProfile,
    User,
    CoverageZone,
    Specialty,
    TechnicianSpecialty,
    IdentityDocument,
    IdentityVerificationRequest,
    IdentityVerificationResult,
)
from app.security import get_current_user
from app.storage import PrivateDocumentStorage
from app.identity_documents import _parse_multipart_parts, _require_admin
from app.technician_activation import (
    MAX_ANTIGUEDAD_ANTECEDENTES_DIAS,
    compute_document_expiration,
    require_operational_technician,
    sync_technician_verification_state,
)

router = APIRouter(prefix="/technicians", tags=["Technicians"])

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

    previous_state = tech_profile.estado_verificacion
    evaluation = sync_technician_verification_state(
        user,
        tech_profile,
        db,
        proceso_responsable="CONSULTA_DISPONIBILIDAD",
        actor_usuario_id=user.id,
    )
    if previous_state != evaluation["estado_verificacion"]:
        db.commit()

    return AvailabilityResponse(
        usuario_id=str(tech_profile.usuario_id),
        disponible=bool(tech_profile.disponible),
        estado_verificacion=str(tech_profile.estado_verificacion),
        puede_recibir_contrataciones=bool(evaluation["puede_recibir_contrataciones"]),
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
        db.flush()

    tech_profile.disponible = data.disponible
    evaluation = sync_technician_verification_state(
        user,
        tech_profile,
        db,
        proceso_responsable="CAMBIO_DISPONIBILIDAD",
        actor_usuario_id=user.id,
        causa_override=(
            "El técnico cambió su disponibilidad a Disponible."
            if data.disponible
            else "El técnico cambió su disponibilidad a No disponible."
        ),
    )
    db.commit()
    db.refresh(tech_profile)

    return AvailabilityResponse(
        usuario_id=str(tech_profile.usuario_id),
        disponible=bool(tech_profile.disponible),
        estado_verificacion=str(tech_profile.estado_verificacion),
        puede_recibir_contrataciones=bool(evaluation["puede_recibir_contrataciones"]),
    )


# --- ESQUEMAS Y ENDPOINTS HU-12, HU-13 y HU-18 ST-03: CATÁLOGO Y FILTROS ---

class CatalogTechnician(BaseModel):
    usuario_id: str
    nombre_completo: str
    especialidades: List[str]
    tecnico_verificado: bool
    estado_verificacion: str
    estado_certificacion: str
    #tarifa_base: float #SE ELIMINARON ESTOS CAMPOS DE PERFILES_TECNICOS
    #unidad_cobro: str
    calificacion_promedio: Optional[float] = None

    class Config:
        from_attributes = True

@router.get("/catalog", response_model=List[CatalogTechnician], status_code=status.HTTP_200_OK)
def get_technicians_catalog(
    nombre: str = Query(None, description="Filtrar por nombre del técnico"),
    especialidad: str = Query(None, description="Filtrar por nombre de especialidad"),
    municipio: str = Query(None, description="Filtrar por municipio"),
    db: Session = Depends(get_db)
):
    # CA-01, CA-02, CA-05 (HU-12 / HU-18): Solo candidatos disponibles, verificados, con teléfono validado
    # y sin suspensiones ni restricciones financieras.
    query = (
        select(User, TechnicianProfile)
        .join(TechnicianProfile, User.id == TechnicianProfile.usuario_id)
        .where(
            TechnicianProfile.disponible == True,
            TechnicianProfile.estado_verificacion == "VERIFICADO",
            User.telefono_verificado == True,
            TechnicianProfile.suspendido == False,
            TechnicianProfile.restriccion_financiera == False,
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
    state_changed = False
    for user, profile in results:
        # CA-04 y CA-06: Revalidar vigencias y requisitos obligatorios al consultar el catálogo
        prev_state = profile.estado_verificacion
        evaluation = sync_technician_verification_state(
            user,
            profile,
            db,
            proceso_responsable="CONSULTA_CATALOGO",
        )
        if prev_state != evaluation["estado_verificacion"]:
            state_changed = True
        if not evaluation["habilitado_operativamente"]:
            continue

        # Obtener las especialidades reales del técnico (CA-02 de HU-12)
        specs_query = (
            select(Specialty.nombre)
            .join(TechnicianSpecialty, Specialty.id == TechnicianSpecialty.especialidad_id)
            .where(TechnicianSpecialty.usuario_id == user.id)
        )
        specs = db.scalars(specs_query).all()

        # CA-03: Mostrar Técnico Verificado separado de certificación y calificación, sin datos privados
        catalog.append({
            "usuario_id": str(user.id),
            "nombre_completo": user.nombre_completo,
            "especialidades": list(specs),
            "tecnico_verificado": evaluation["tecnico_verificado"],
            "estado_verificacion": profile.estado_verificacion,
            "estado_certificacion": profile.estado_certificacion or "NO_CERTIFICADO",
            #"tarifa_base": float(profile.tarifa_base) if profile.tarifa_base else 0.0,
            #"unidad_cobro": str(profile.unidad_cobro) if profile.unidad_cobro else "POR_HORA", #SE QUITARON ESTOS CAMPOS DE PERFILES TECNICOS
            "calificacion_promedio": (
                float(profile.calificacion_promedio) if profile.calificacion_promedio is not None else None
            ),
        })

    if state_changed:
        db.commit()
    
    return catalog


# --- ENDPOINT HU-13 / HU-18 ST-03: VISTA DETALLADA DEL PERFIL PÚBLICO DEL TÉCNICO ---

class PublicService(BaseModel):
    id: str
    nombre: str
    descripcion: str

class TechnicianPublicProfile(BaseModel):
    usuario_id: str
    nombre_completo: str
    especialidades: List[str]
    servicios: List[PublicService]
    tecnico_verificado: bool
    estado_verificacion: str
    estado_certificacion: str
    #tarifa_base: float #SE ELIMINARON ESTOS CAMPOS DE LA TABLA DE PERFILES TECNICOS
    #unidad_cobro: str
    calificacion_promedio: Optional[float] = None
    
    class Config:
        from_attributes = True

@router.get("/profile/{user_id}", response_model=TechnicianPublicProfile, status_code=status.HTTP_200_OK)
def get_technician_public_profile(user_id: uuid.UUID, db: Session = Depends(get_db)):
    # Buscar al usuario y su perfil de técnico
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Técnico no encontrado.")
        
    tech_profile = db.get(TechnicianProfile, user_id)
    if not tech_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El perfil no está disponible.")

    prev_state = tech_profile.estado_verificacion
    evaluation = sync_technician_verification_state(
        user,
        tech_profile,
        db,
        proceso_responsable="CONSULTA_PERFIL_PUBLICO",
    )
    if prev_state != evaluation["estado_verificacion"]:
        db.commit()

    # CA-02, CA-04, CA-05: Si no está habilitado operativamente, no se muestra como disponible para contratar
    if not evaluation["habilitado_operativamente"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El perfil no está disponible.")

    # 1. Obtener Especialidades
    specs_query = select(Specialty.nombre).join(
        TechnicianSpecialty, Specialty.id == TechnicianSpecialty.especialidad_id
    ).where(TechnicianSpecialty.usuario_id == user.id)
    specs = db.scalars(specs_query).all()

    # 2. Obtener Servicios Activos (CA-05 de HU-13)
    from app.models import Service 
    services_query = select(Service).where(Service.usuario_id == user.id, Service.activo == True)
    services = db.scalars(services_query).all()

    # 3. Formatear la respuesta (CA-03: separado de certificación y calificación, sin DNI/teléfono/documentos)
    public_services = [
        {"id": str(s.id), "nombre": s.nombre, "descripcion": s.descripcion}
        for s in services
    ]

    return {
        "usuario_id": str(user.id),
        "nombre_completo": user.nombre_completo,
        "especialidades": list(specs),
        "servicios": public_services,
        "tecnico_verificado": evaluation["tecnico_verificado"],
        "estado_verificacion": tech_profile.estado_verificacion,
        "estado_certificacion": tech_profile.estado_certificacion or "NO_CERTIFICADO",
        #"tarifa_base": float(tech_profile.tarifa_base) if tech_profile.tarifa_base else 0.0, #SE ELIMINARON ESTOS CAMPOS DE PERFILES_TECNICOS
        #"unidad_cobro": str(tech_profile.unidad_cobro) if tech_profile.unidad_cobro else "POR_HORA",
        "calificacion_promedio": (
            float(tech_profile.calificacion_promedio) if tech_profile.calificacion_promedio is not None else None
        ),
    }


# --- HU-18 ST-03: PERMISOS OPERATIVOS EN SOLICITUDES Y POSTULACIONES ---

class JobApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    solicitud_id: uuid.UUID
    mensaje: Optional[str] = Field(default=None, max_length=500)


@router.post("/me/service-requests/{solicitud_id}/accept", status_code=status.HTTP_200_OK)
def accept_service_request(
    solicitud_id: uuid.UUID,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CA-02, CA-05 y Condición transversal: revalida habilitación al aceptar una solicitud por API."""
    user = db.scalar(
        select(User)
        .where(User.firebase_uid == claims["uid"])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completa tu registro en TekniGO")

    profile = require_operational_technician(
        user,
        db,
        proceso_responsable="ACEPTAR_SOLICITUD_API",
    )
    return {
        "status": "accepted",
        "solicitud_id": str(solicitud_id),
        "tecnico_id": str(profile.usuario_id),
        "estado_verificacion": profile.estado_verificacion,
        "habilitado_operativamente": True,
    }


@router.post("/me/job-applications", status_code=status.HTTP_201_CREATED)
def apply_to_job_request(
    payload: JobApplicationRequest,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CA-02, CA-05 y Condición transversal: revalida habilitación al postular mediante la API."""
    user = db.scalar(
        select(User)
        .where(User.firebase_uid == claims["uid"])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completa tu registro en TekniGO")

    profile = require_operational_technician(
        user,
        db,
        proceso_responsable="POSTULAR_SOLICITUD_API",
    )
    return {
        "status": "applied",
        "solicitud_id": str(payload.solicitud_id),
        "tecnico_id": str(profile.usuario_id),
        "estado_verificacion": profile.estado_verificacion,
        "habilitado_operativamente": True,
    }


# --- HU-16 ST-03 / HU-18 ST-01: Verificación documental y activación del técnico ---
TECHNICIAN_DOC_TYPES = {
    "dni_frontal": ("DNI_FRONTAL", "el DNI (frente)"),
    "dni_reverso": ("DNI_REVERSO", "el DNI (reverso)"),
    "selfie": ("SELFIE", "la selfie"),
    "antecedentes_policiales": ("ANTECEDENTES_POLICIALES", "los antecedentes policiales"),
    "antecedentes_penales": ("ANTECEDENTES_PENALES", "los antecedentes penales"),
}    
def _serialize_verification_doc(doc: IdentityDocument) -> dict:
    return {
        "id": str(doc.id),
        "tipo": doc.tipo,
        "estado": doc.estado,
        "observaciones": doc.observaciones,
        "fecha_emision_declarada": doc.fecha_emision_declarada.isoformat() if doc.fecha_emision_declarada else None,
        "fecha_vencimiento": doc.fecha_vencimiento.isoformat() if doc.fecha_vencimiento else None,
        "creado_en": doc.creado_en.isoformat() if doc.creado_en else None,
    }
def _parse_date_field(value: str | None, label: str) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, f"La fecha de emisión de {label} tiene formato inválido (usar AAAA-MM-DD)")

@router.post("/me/verification-documents", status_code=201)
async def upload_verification_documents(
    request: Request,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    technician = db.get(TechnicianProfile, user.id)
    if technician is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")
    if technician.estado_verificacion == "VERIFICADO":
        raise HTTPException(409, "Tu perfil de técnico ya fue verificado")
    if technician.estado_verificacion == "EN_REVISION":
        raise HTTPException(409, "Ya tienes una solicitud de verificación pendiente de revisión")
    body = await request.body()
    parts = _parse_multipart_parts(request.headers.get("content-type", ""), body)
    # 1. Consultar documentos existentes previamente para el usuario
    existing_docs_query = db.scalars(
        select(IdentityDocument).where(IdentityDocument.usuario_id == user.id)
    ).all()
    existing_docs_map = {doc.tipo: doc for doc in existing_docs_query}

    # 2. Validar presencia: Exigir solo los campos que NO vienen en el request Y tampoco existen previamente
    for field_key, (doc_type, label) in TECHNICIAN_DOC_TYPES.items():
        has_new_file = field_key in parts and bool(parts[field_key].content)
        has_existing_file = doc_type in existing_docs_map

        if not has_new_file and not has_existing_file:
            raise HTTPException(400, f"Falta adjuntar el archivo de {label}")

    # Parsear y validar fechas para antecedentes
    fecha_policiales = _parse_date_field(
        parts["fecha_policiales"].content.decode("utf-8", errors="replace") if "fecha_policiales" in parts and parts["fecha_policiales"].content else None,
        "los antecedentes policiales",
    )
    fecha_penales = _parse_date_field(
        parts["fecha_penales"].content.decode("utf-8", errors="replace") if "fecha_penales" in parts and parts["fecha_penales"].content else None,
        "los antecedentes penales",
    )

    # Si se sube una nueva constancia policial o no existía previamente, validar su vigencia
    if ("antecedentes_policiales" in parts and parts["antecedentes_policiales"].content) or "ANTECEDENTES_POLICIALES" not in existing_docs_map:
        _validar_vigencia_antecedentes(fecha_policiales, "los antecedentes policiales")

    # Si se sube una nueva constancia penal o no existía previamente, validar su vigencia
    if ("antecedentes_penales" in parts and parts["antecedentes_penales"].content) or "ANTECEDENTES_PENALES" not in existing_docs_map:
        _validar_vigencia_antecedentes(fecha_penales, "los antecedentes penales")

    version = (db.scalar(select(func.max(IdentityVerificationRequest.version)).where(
        IdentityVerificationRequest.usuario_id == user.id,
    )) or 0) + 1

    request_id = uuid.uuid4()
    store = PrivateDocumentStorage()
    stored_docs: list[IdentityDocument] = []

    # 3. Procesar o reutilizar cada documento
    for field_key, (doc_type, label) in TECHNICIAN_DOC_TYPES.items():
        has_new_file = field_key in parts and bool(parts[field_key].content)
        
        if has_new_file:
            # Opción A: Guardar archivo recién subido
            part = parts[field_key]
            saved = store.store(
                user_id=user.id, doc_type=doc_type,
                content=part.content, declared_content_type=part.content_type, label=label,
            )
            fecha = None
            if doc_type == "ANTECEDENTES_POLICIALES":
                fecha = fecha_policiales
            elif doc_type == "ANTECEDENTES_PENALES":
                fecha = fecha_penales

            stored_docs.append(IdentityDocument(
                usuario_id=user.id,
                solicitud_id=request_id, 
                tipo=doc_type,
                ruta_privada=saved.ruta_privada, 
                content_type=saved.content_type,
                tamano_bytes=saved.tamano_bytes, 
                sha256=saved.sha256,
                estado="PENDIENTE", 
                fecha_emision_declarada=fecha,
                fecha_vencimiento=compute_document_expiration(doc_type, fecha),
            ))
        else:
            # Opción B: Reutilizar el documento existente en DB vinculándolo a la nueva solicitud
            prev_doc = existing_docs_map[doc_type]
            fecha = prev_doc.fecha_emision_declarada
            if doc_type == "ANTECEDENTES_POLICIALES" and fecha_policiales:
                fecha = fecha_policiales
            elif doc_type == "ANTECEDENTES_PENALES" and fecha_penales:
                fecha = fecha_penales

            stored_docs.append(IdentityDocument(
                usuario_id=user.id,
                solicitud_id=request_id,
                tipo=doc_type,
                ruta_privada=prev_doc.ruta_privada,
                content_type=prev_doc.content_type,
                tamano_bytes=prev_doc.tamano_bytes,
                sha256=prev_doc.sha256,
                estado="PENDIENTE",
                fecha_emision_declarada=fecha,
                fecha_vencimiento=prev_doc.fecha_vencimiento or compute_document_expiration(doc_type, fecha),
            ))

    # Limpiar registros anteriores de estos tipos para el usuario
    doc_types_list = [dt for dt, _ in TECHNICIAN_DOC_TYPES.values()]
    db.execute(delete(IdentityDocument).where(
        IdentityDocument.usuario_id == user.id,
        IdentityDocument.tipo.in_(doc_types_list),
    ))

    # Guardar nueva solicitud, documentos y actualizar estado general
    db.add(IdentityVerificationRequest(id=request_id, usuario_id=user.id, version=version))
    db.flush()
    db.add_all(stored_docs)
    db.flush()

    db.add(IdentityVerificationResult(
        solicitud_id=request_id, estado="EN_REVISION",
        motivo="Documentos recibidos. Pendientes de verificación de identidad.",
        proceso_responsable="CARGA_DOCUMENTAL", version_proceso="1",
    ))

    # Sincronizar estado global del perfil y usuario
    user.estado_identidad = "EN_REVISION"
    sync_technician_verification_state(
        user,
        technician,
        db,
        proceso_responsable="CARGA_DOCUMENTAL",
        actor_usuario_id=user.id,
        causa_override="Documentos recibidos. Pendientes de verificación de identidad y antecedentes.",
    )

    db.commit()

    for doc in stored_docs:
        db.refresh(doc)

    return {
        "estado_verificacion": technician.estado_verificacion,
        "documentos": [_serialize_verification_doc(doc) for doc in stored_docs],
    }

@router.get("/me/verification-documents")
def list_verification_documents(
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    technician = db.get(TechnicianProfile, user.id)
    if technician is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")
    previous_state = technician.estado_verificacion
    evaluation = sync_technician_verification_state(
        user,
        technician,
        db,
        proceso_responsable="CONSULTA_DOCUMENTOS_TECNICO",
        actor_usuario_id=user.id,
    )
    if previous_state != evaluation["estado_verificacion"]:
        db.commit()
    doc_types_list = [dt for dt, _ in TECHNICIAN_DOC_TYPES.values()]
    docs = db.scalars(
        select(IdentityDocument).where(
            IdentityDocument.usuario_id == user.id,
            IdentityDocument.tipo.in_(doc_types_list),
        ).order_by(IdentityDocument.creado_en.asc())
    ).all()
    return {
        "estado_verificacion": technician.estado_verificacion,
        "observaciones": technician.observaciones_verificacion,
        "motivo_restriccion": technician.motivo_restriccion,
        "telefono_verificado": bool(user.telefono_verificado),
        "puede_recibir_contrataciones": evaluation["puede_recibir_contrataciones"],
        "requisitos": evaluation["requisitos"],
        "requisitos_pendientes": evaluation["requisitos_pendientes"],
        "documentos": [_serialize_verification_doc(doc) for doc in docs],
    }

@router.get("/me/activation-status")
def get_my_activation_status(
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    technician = db.get(TechnicianProfile, user.id)
    if technician is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")
    previous_state = technician.estado_verificacion
    evaluation = sync_technician_verification_state(
        user,
        technician,
        db,
        proceso_responsable="CONSULTA_ESTADO_ACTIVACION",
        actor_usuario_id=user.id,
    )
    if previous_state != evaluation["estado_verificacion"]:
        db.commit()
    return evaluation

@router.get("/admin/verification-documents/{document_id}/content")
def download_verification_document_admin(
    document_id: uuid.UUID,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Restringe estrictamente el acceso solo a administradores
    _require_admin(claims, db)
    doc = db.get(IdentityDocument, document_id)
    if doc is None:
        raise HTTPException(404, "No se encontró el documento")
    store = PrivateDocumentStorage()
    try:
        raw_bytes = store.read_private(doc.ruta_privada)
    except FileNotFoundError:
        raise HTTPException(404, "Archivo privado no encontrado") from None
    return Response(
        content=raw_bytes, 
        media_type=doc.content_type,
        headers={"Cache-Control": "no-store, private"},
    )

# Define el límite de antigüedad permitido (ej. 90 días o 3 meses)
MAX_ANTIGUEDAD_DIAS = MAX_ANTIGUEDAD_ANTECEDENTES_DIAS

def _validar_vigencia_antecedentes(fecha: date | None, label: str):
    if not fecha:
        raise HTTPException(400, f"Indica la fecha de emisión de {label}.")
    
    hoy = date.today()
    if fecha > hoy:
        raise HTTPException(400, f"La fecha de emisión de {label} no puede ser una fecha futura.")
    
    # Calcular diferencia de días
    antiguedad = (hoy - fecha).days
    if antiguedad > MAX_ANTIGUEDAD_DIAS:
        raise HTTPException(
            400, 
            f"La constancia de {label} ha expirado (emitida hace {antiguedad} días). El límite máximo de vigencia es de {MAX_ANTIGUEDAD_DIAS} días."
        )

class DocumentReviewItem(BaseModel):
    documento_id: uuid.UUID
    estado: str  # 'APROBADO', 'RECHAZADO', 'VENCIDO', 'INVALIDADO'
    observaciones: Optional[str] = None
    fecha_vencimiento: Optional[date] = None

class AdminVerificationReviewSchema(BaseModel):
    estado_verificacion: Optional[str] = None  # Opcional: el estado real lo determina el servicio central (CA-01)
    observaciones_verificacion: Optional[str] = None
    documentos: List[DocumentReviewItem]


@router.patch("/admin/technicians/{usuario_id}/verification-review")
def review_technician_verification(
    usuario_id: uuid.UUID,
    payload: AdminVerificationReviewSchema,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. Validar que quien ejecuta sea administrador
    admin_user = _require_admin(claims, db)

    # 2. Buscar usuario y perfil del técnico
    tech_user = db.get(User, usuario_id)
    technician = db.get(TechnicianProfile, usuario_id)
    if not tech_user or not technician:
        raise HTTPException(404, "No se encontró el perfil del técnico.")

    # 3. Actualizar observaciones generales del perfil
    technician.observaciones_verificacion = payload.observaciones_verificacion

    # 4. Actualizar el estado, observaciones y vigencia de cada documento evaluado
    for doc_review in payload.documentos:
        doc = db.get(IdentityDocument, doc_review.documento_id)
        if doc and doc.usuario_id == usuario_id:
            doc.estado = doc_review.estado
            doc.observaciones = doc_review.observaciones
            if doc_review.fecha_vencimiento is not None:
                doc.fecha_vencimiento = doc_review.fecha_vencimiento
            elif doc.fecha_vencimiento is None and doc.fecha_emision_declarada is not None:
                doc.fecha_vencimiento = compute_document_expiration(doc.tipo, doc.fecha_emision_declarada)

    db.flush()

    # 5. Evaluar y sincronizar estado según CA-01, CA-02, CA-04, CA-05 y registrar auditoría (CA-06)
    evaluation = sync_technician_verification_state(
        tech_user,
        technician,
        db,
        proceso_responsable="REVISION_DOCUMENTAL_ADMIN",
        actor_usuario_id=admin_user.id,
        causa_override=payload.observaciones_verificacion,
    )
    db.commit()

    return {
        "status": "success",
        "message": f"Verificación del técnico actualizada a: {technician.estado_verificacion}",
        "estado_verificacion": technician.estado_verificacion,
        "puede_recibir_contrataciones": evaluation["puede_recibir_contrataciones"],
        "motivo_restriccion": technician.motivo_restriccion,
        "requisitos_pendientes": evaluation["requisitos_pendientes"],
    }


from app.technician_activation import run_scheduled_expiration_review


@router.post("/admin/expiration-review", status_code=status.HTTP_200_OK)
def run_expiration_review_admin(
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HU-18 ST-05 (CA-04, CA-06): Ejecuta la revisión programada de vencimientos de técnicos."""
    admin_user = _require_admin(claims, db)
    summary = run_scheduled_expiration_review(
        db,
        actor_usuario_id=admin_user.id,
        proceso_responsable="REVISION_PROGRAMADA_VENCIMIENTOS",
    )
    db.commit()
    return summary
