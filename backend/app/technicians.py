from typing import Literal, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select,delete
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TechnicianProfile, User, UserRole, CoverageZone, IdentityDocument
from app.security import get_current_user
from app.storage import PrivateDocumentStorage
from app.identity_documents import UploadedPart, _parse_multipart_parts, _require_admin
from datetime import date

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

# --- HU-16 ST-03: Verificación documental del técnico ---
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
        "creado_en": doc.creado_en.isoformat() if doc.creado_en else None,
    }
def _parse_date_field(value: str | None, label: str) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, f"La fecha de emisión de {label} tiene formato inválido (usar AAAA-MM-DD)")
@router.patch("/technicians/me/verification-documents", status_code=201)
async def upload_verification_documents(
    request: status,
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
    for field_key in TECHNICIAN_DOC_TYPES:
        if field_key not in parts or not parts[field_key].content:
            _, label = TECHNICIAN_DOC_TYPES[field_key]
            raise HTTPException(400, f"Falta adjuntar el archivo de {label}")
    fecha_policiales = _parse_date_field(
        parts["fecha_policiales"].content.decode("utf-8", errors="replace") if "fecha_policiales" in parts else None,
        "los antecedentes policiales",
    )
    fecha_penales = _parse_date_field(
        parts["fecha_penales"].content.decode("utf-8", errors="replace") if "fecha_penales" in parts else None,
        "los antecedentes penales",
    )
    store = PrivateDocumentStorage()
    stored_docs: list[IdentityDocument] = []
    for field_key, (doc_type, label) in TECHNICIAN_DOC_TYPES.items():
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
            usuario_id=user.id, tipo=doc_type,
            ruta_privada=saved.ruta_privada, content_type=saved.content_type,
            tamano_bytes=saved.tamano_bytes, sha256=saved.sha256,
            estado="PENDIENTE", fecha_emision_declarada=fecha,
        ))
    doc_types_list = [dt for dt, _ in TECHNICIAN_DOC_TYPES.values()]
    db.execute(delete(IdentityDocument).where(
        IdentityDocument.usuario_id == user.id,
        IdentityDocument.tipo.in_(doc_types_list),
    ))
    db.add_all(stored_docs)
    technician.estado_verificacion = "EN_REVISION"
    db.commit()
    for doc in stored_docs:
        db.refresh(doc)
    return {
        "estado_verificacion": technician.estado_verificacion,
        "documentos": [_serialize_verification_doc(doc) for doc in stored_docs],
    }
@router.get("/technicians/me/verification-documents")
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
        "documentos": [_serialize_verification_doc(doc) for doc in docs],
    }
@router.get("/admin/verification-documents/{document_id}/content")
def download_verification_document_admin(
    document_id: uuid.UUID,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
        content=raw_bytes, media_type=doc.content_type,
        headers={"Cache-Control": "no-store, private"},
    )