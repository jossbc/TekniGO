import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "usuarios"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True)
    nombre_completo: Mapped[str] = mapped_column(String(150))
    correo: Mapped[str] = mapped_column(String(254))
    dni: Mapped[str] = mapped_column(String(13), unique=True)
    telefono: Mapped[str] = mapped_column(String(16), unique=True)
    estado_identidad: Mapped[str] = mapped_column(String(30), default="PENDIENTE")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    roles: Mapped[list["UserRole"]] = relationship(lazy="selectin")


class UserRole(Base):
    __tablename__ = "usuarios_roles"
    __table_args__ = (CheckConstraint("rol IN ('CLIENTE', 'TECNICO', 'ADMIN')", name="ck_usuario_rol"),)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    rol: Mapped[str] = mapped_column(String(20), primary_key=True)


class TechnicianProfile(Base):
    __tablename__ = "perfiles_tecnicos"
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    disponible: Mapped[bool] = mapped_column(Boolean, default=False)
    estado_verificacion: Mapped[str] = mapped_column(String(30), default="NO_VERIFICADO")
    #--Campos Para HU-09 Tarifas Base--
    tarifa_base: Mapped[float] = mapped_column(Numeric(10,2), default=0.0)
    unidad_cobro: Mapped[str] = mapped_column(String(20), default="POR HORA")


class Specialty(Base):
    __tablename__ = "especialidades"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(80), unique=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TechnicianSpecialty(Base):
    __tablename__ = "tecnicos_especialidades"
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"), primary_key=True)
    especialidad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("especialidades.id"), primary_key=True)

class SpecialtySuggestion(Base):
    __tablename__ = "sugerencias_especialidades"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"), index=True)
    nombre: Mapped[str] = mapped_column(String(80))
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Service(Base):
    __tablename__ = "servicios"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"), index=True)
    especialidad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("especialidades.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(100))
    descripcion: Mapped[str] = mapped_column(Text)
    # -- Campos HU-09: Tarifas Base por Servicio --
    tarifa_base: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0, server_default="0.0")
    unidad_cobro: Mapped[str] = mapped_column(String(20), default="POR_HORA", server_default="POR_HORA")
    # ---------------------------------------------
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IdentityDocument(Base):
    __tablename__ = "documentos_identidad"
    __table_args__ = (
        CheckConstraint("tipo IN ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE')", name="ck_documento_identidad_tipo"),
        CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO')", name="ck_documento_identidad_estado"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    ruta_privada: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(50))
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoverageZone(Base):
    __tablename__ = "zonas_cobertura"
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"),nullable=False, index = True)
    departamento: Mapped[str] = mapped_column(String(50), nullable=False)
    municipio: Mapped[str] = mapped_column(String(50),nullable=False)
    sector: Mapped[str] = mapped_column(String(100),nullable=False)