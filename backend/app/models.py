import uuid
from datetime import datetime, date, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, TypeDecorator, Uuid, func, Numeric, UniqueConstraint, JSON, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FlexibleUuid(TypeDecorator):
    """Acepta uuid.UUID o str en Python; en PostgreSQL usa el tipo nativo UUID y en SQLite VARCHAR(36)."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Uuid(as_uuid=False))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(uuid.UUID(str(value))) if not isinstance(value, uuid.UUID) else str(value)
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return str(value)


class User(Base):
    __tablename__ = "usuarios"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True)
    nombre_completo: Mapped[str] = mapped_column(String(150))
    correo: Mapped[str] = mapped_column(String(254))
    dni: Mapped[str] = mapped_column(String(13), unique=True)
    telefono: Mapped[str] = mapped_column(String(16), unique=True)
    telefono_verificado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
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
    __table_args__ = (
        CheckConstraint(
            "estado_verificacion IN ('NO_VERIFICADO', 'PENDIENTE', 'EN_REVISION', 'VERIFICADO', 'RECHAZADO', 'RESTRINGIDO')",
            name="ck_perfil_tecnico_estado_verificacion",
        ),
    )
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    disponible: Mapped[bool] = mapped_column(Boolean, default=False)
    estado_verificacion: Mapped[str] = mapped_column(String(30), default="NO_VERIFICADO")
    # HU-16 ST-02: Observaciones de la revisión documental del técnico
    observaciones_verificacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HU-18 ST-02: Vigencias, dimensiones independientes y restricciones
    verificado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    motivo_restriccion: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspendido: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    restriccion_financiera: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    estado_certificacion: Mapped[str] = mapped_column(String(30), default="NO_CERTIFICADO", server_default="NO_CERTIFICADO")
    calificacion_promedio: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)


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


class IdentityVerificationRequest(Base):
    __tablename__ = "solicitudes_verificacion_identidad"
    __table_args__ = (
        UniqueConstraint("usuario_id", "version", name="uq_solicitud_identidad_version"),
        CheckConstraint("version > 0", name="ck_solicitud_identidad_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityVerificationResult(Base):
    __tablename__ = "resultados_verificacion_identidad"
    __table_args__ = (
        CheckConstraint("estado IN ('PENDIENTE', 'EN_REVISION', 'RECHAZADO', 'VERIFICADO', 'INCIERTO', 'ERROR')", name="ck_resultado_identidad_estado"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    solicitud_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solicitudes_verificacion_identidad.id"), index=True)
    estado: Mapped[str] = mapped_column(String(30))
    motivo: Mapped[str] = mapped_column(Text)
    proceso_responsable: Mapped[str] = mapped_column(String(100))
    version_proceso: Mapped[str] = mapped_column(String(50))
    evidencia: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityDocument(Base):
    __tablename__ = "documentos_identidad"
    __table_args__ = (
        CheckConstraint("tipo IN ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE', 'ANTECEDENTES_POLICIALES', 'ANTECEDENTES_PENALES')", name="ck_documento_identidad_tipo"),
        CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'VENCIDO', 'INVALIDADO')", name="ck_documento_identidad_estado"),
        UniqueConstraint("solicitud_id", "tipo", name="uq_documento_solicitud_tipo"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), index=True)
    solicitud_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solicitudes_verificacion_identidad.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    ruta_privada: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(50))
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    # HU-16 ST-02 / ST-04: Fecha de emisión declarada para antecedentes
    fecha_emision_declarada: Mapped[date | None] = mapped_column(Date, nullable=True)
    # HU-18 ST-02: Vigencia de requisitos obligatorios
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentReviewDecision(Base):
    __tablename__ = "decisiones_documentales"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documentos_identidad.id"), index=True)
    solicitud_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solicitudes_verificacion_identidad.id"), index=True)
    administrador_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"))
    version_evaluada: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(20))
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    legible: Mapped[bool] = mapped_column(Boolean)
    identidad_coincide: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TechnicianStatusAudit(Base):
    __tablename__ = "historial_estados_tecnico"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"), index=True)
    estado_anterior: Mapped[str] = mapped_column(String(30))
    estado_nuevo: Mapped[str] = mapped_column(String(30))
    habilitado_anterior: Mapped[bool] = mapped_column(Boolean)
    habilitado_nuevo: Mapped[bool] = mapped_column(Boolean)
    causa: Mapped[str] = mapped_column(Text)
    actor_usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    proceso_responsable: Mapped[str] = mapped_column(String(100))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoverageZone(Base):
    __tablename__ = "zonas_cobertura"
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"),nullable=False, index = True)
    departamento: Mapped[str] = mapped_column(String(50), nullable=False)
    municipio: Mapped[str] = mapped_column(String(50),nullable=False)
    sector: Mapped[str] = mapped_column(String(100),nullable=False)


class DirectRequest(Base):
    __tablename__ = 'solicitudes_directas'

    id = Column(FlexibleUuid, primary_key=True, default=lambda: str(uuid.uuid4()))
    cliente_id = Column(String, ForeignKey('usuarios.firebase_uid'), nullable=False)
    tecnico_id = Column(String, ForeignKey('usuarios.firebase_uid'), nullable=False)
    servicio_id = Column(FlexibleUuid, ForeignKey('servicios.id'), nullable=False)

    # Ubicación y Detalles
    municipio = Column(String, nullable=False)
    sector = Column(String, nullable=False)
    direccion_exacta = Column(String, nullable=False)
    fecha_hora = Column(DateTime(timezone=True), nullable=False)
    descripcion = Column(Text, nullable=False)

    # Presupuesto (HU-21 y HU-20)
    cantidad = Column(Numeric(10, 2), nullable=False)
    tarifa_aplicada = Column(Numeric(10, 2), nullable=False)
    unidad_cobro = Column(String, nullable=False)
    monto_base = Column(Numeric(10, 2), nullable=False)
    metodo_pago = Column(String, nullable=False) # 'efectivo' o 'tarjeta'

    estado = Column(String, nullable=False, default="Pendiente") # Pendiente, Aceptada, Rechazada, Cancelada, Completada
    creada_en = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relaciones para facilitar consultas en SQLAlchemy
    cliente = relationship("User", foreign_keys=[cliente_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])
    servicio = relationship("Service", foreign_keys=[servicio_id])


class OpenRequest(Base):
    __tablename__ = "solicitudes_abiertas"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cliente_id = Column(String, ForeignKey("usuarios.firebase_uid"), nullable=False, index=True)
    tecnico_id = Column(String, ForeignKey("usuarios.firebase_uid"), nullable=True, default=None)
    especialidad_id = Column(FlexibleUuid, ForeignKey("especialidades.id"), nullable=False, index=True)
    servicio_id = Column(FlexibleUuid, ForeignKey("servicios.id"), nullable=True, default=None)

    modalidad = Column(String(20), nullable=False, default="ABIERTA", server_default="ABIERTA")
    municipio = Column(String(80), nullable=False)
    sector = Column(String(100), nullable=False)
    direccion_exacta = Column(Text, nullable=False)
    fecha_hora = Column(DateTime(timezone=True), nullable=False)
    descripcion = Column(Text, nullable=False)

    presupuesto = Column(Numeric(10, 2), nullable=False)
    monto_base = Column(Numeric(10, 2), nullable=False)
    metodo_pago = Column(String(20), nullable=False)  # 'efectivo' o 'tarjeta'

    estado = Column(String(30), nullable=False, default="Publicada", server_default="Publicada")
    creada_en = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())

    cliente = relationship("User", foreign_keys=[cliente_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])
    especialidad = relationship("Specialty", foreign_keys=[especialidad_id])


class DiscardedOpenRequest(Base):
    __tablename__ = "solicitudes_abiertas_descartadas"
    __table_args__ = (
        UniqueConstraint("usuario_id", "solicitud_id", name="uq_solicitud_abierta_descartada_tecnico"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("perfiles_tecnicos.usuario_id"),
        nullable=False,
        index=True,
    )
    tecnico_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("usuarios.firebase_uid"),
        nullable=False,
        index=True,
    )
    solicitud_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("solicitudes_abiertas.id"),
        nullable=False,
        index=True,
    )
    descartada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    solicitud = relationship("OpenRequest", foreign_keys=[solicitud_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])


class OpenRequestProposal(Base):
    __tablename__ = "propuestas_solicitudes_abiertas"
    __table_args__ = (
        UniqueConstraint("solicitud_id", "usuario_id", name="uq_propuesta_activa_por_tecnico_y_licitacion"),
        CheckConstraint(
            "estado IN ('PENDIENTE', 'ACEPTADA', 'RECHAZADA', 'CANCELADA', 'RETIRADA')",
            name="ck_propuesta_solicitud_abierta_estado",
        ),
        CheckConstraint("monto > 0", name="ck_propuesta_solicitud_abierta_monto"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    solicitud_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("solicitudes_abiertas.id"),
        nullable=False,
        index=True,
    )
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("perfiles_tecnicos.usuario_id"),
        nullable=False,
        index=True,
    )
    tecnico_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("usuarios.firebase_uid"),
        nullable=False,
        index=True,
    )
    tipo: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="ACEPTACION_PRESUPUESTO",
        server_default="ACEPTACION_PRESUPUESTO",
    )
    monto: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    justificacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDIENTE",
        server_default="PENDIENTE",
    )
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )

    solicitud = relationship("OpenRequest", foreign_keys=[solicitud_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])
