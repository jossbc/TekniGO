import uuid
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, TypeDecorator, Uuid, func, Numeric, UniqueConstraint, JSON, Column, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CONFIRMATION_WINDOW_HOURS = 48
CONFIRMATION_WINDOW = timedelta(hours=CONFIRMATION_WINDOW_HOURS)
TEGUCIGALPA_TZ_NAME = "America/Tegucigalpa"
try:
    TEGUCIGALPA_TZ = ZoneInfo(TEGUCIGALPA_TZ_NAME)
except ZoneInfoNotFoundError:
    TEGUCIGALPA_TZ = timezone(timedelta(hours=-6), name=TEGUCIGALPA_TZ_NAME)


def ensure_utc_datetime(dt: datetime | None) -> datetime | None:
    """Normaliza cualquier instante a UTC con referencia horaria inequívoca."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_tegucigalpa_datetime(dt: datetime | None) -> datetime | None:
    """Convierte un instante con zona horaria a America/Tegucigalpa (-06:00)."""
    utc_dt = ensure_utc_datetime(dt)
    if utc_dt is None:
        return None
    return utc_dt.astimezone(TEGUCIGALPA_TZ)


def format_tegucigalpa_iso(dt: datetime | None) -> str | None:
    """Devuelve el instante en formato ISO-8601 expresado en America/Tegucigalpa."""
    local_dt = to_tegucigalpa_datetime(dt)
    return local_dt.isoformat() if local_dt is not None else None


def calculate_confirmation_expiration(
    reported_at: datetime,
    *,
    window_hours: int = CONFIRMATION_WINDOW_HOURS,
) -> datetime:
    """HU-34 ST-01 (CA-03): Calcula el vencimiento de 48 horas desde el instante reportado."""
    normalized = ensure_utc_datetime(reported_at)
    assert normalized is not None
    return normalized + timedelta(hours=window_hours)


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
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuggestedTariff(Base):
    __tablename__ = "tarifas_sugeridas"
    __table_args__ = (CheckConstraint("promedio > 0", name="ck_tarifa_sugerida_positiva"),)

    especialidad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("especialidades.id"), primary_key=True)
    unidad: Mapped[str] = mapped_column(String(20), primary_key=True)
    promedio: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class OperationalSettings(Base):
    __tablename__ = "configuracion_operativa"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_configuracion_unica"),
        CheckConstraint("comision_porcentaje BETWEEN 0 AND 100", name="ck_configuracion_comision"),
        CheckConstraint("limite_deuda > 0 AND minimo_retiro > 0", name="ck_configuracion_umbrales"),
        CheckConstraint("limite_sin_liquidar = 5", name="ck_configuracion_liquidaciones"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comision_porcentaje: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    limite_deuda: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    minimo_retiro: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    limite_sin_liquidar: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    valores_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class CatalogChange(Base):
    __tablename__ = "historial_configuracion_catalogo"

    id: Mapped[int] = mapped_column(primary_key=True)
    recurso: Mapped[str] = mapped_column(String(90))
    anterior: Mapped[dict | None] = mapped_column(JSON)
    nuevo: Mapped[dict] = mapped_column(JSON)
    administrador_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"))
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class _CompletionTrackingMixin:
    """HU-34 ST-01: Propiedades y utilidades compartidas de finalización reportada y vencimiento de confirmación."""

    seguimiento_recepcion = Column(JSON, nullable=True)

    @property
    def finalizacion_reportada(self) -> datetime | None:
        return self.finalizacion_reportada_en

    @finalizacion_reportada.setter
    def finalizacion_reportada(self, value: datetime | None) -> None:
        self.finalizacion_reportada_en = ensure_utc_datetime(value)

    @property
    def vencimiento_confirmacion(self) -> datetime | None:
        return self.vencimiento_confirmacion_en

    @vencimiento_confirmacion.setter
    def vencimiento_confirmacion(self, value: datetime | None) -> None:
        self.vencimiento_confirmacion_en = ensure_utc_datetime(value)

    @property
    def confirmacion_vence_en(self) -> datetime | None:
        return self.vencimiento_confirmacion_en

    @confirmacion_vence_en.setter
    def confirmacion_vence_en(self, value: datetime | None) -> None:
        self.vencimiento_confirmacion_en = ensure_utc_datetime(value)

    @property
    def finalizacion_reportada_local(self) -> str | None:
        return format_tegucigalpa_iso(self.finalizacion_reportada_en)

    @property
    def vencimiento_confirmacion_local(self) -> str | None:
        return format_tegucigalpa_iso(self.vencimiento_confirmacion_en)

    def registrar_finalizacion_reportada(
        self,
        now: datetime | None = None,
        *,
        window_hours: int = CONFIRMATION_WINDOW_HOURS,
    ) -> bool:
        """Registra por única vez el instante de finalización y el plazo de 48h sin reiniciar en reintentos."""
        if self.finalizacion_reportada_en is not None and self.vencimiento_confirmacion_en is not None:
            self.finalizacion_reportada_en = ensure_utc_datetime(self.finalizacion_reportada_en)
            self.vencimiento_confirmacion_en = ensure_utc_datetime(self.vencimiento_confirmacion_en)
            self.pendiente_recepcion = True
            return False

        instante = ensure_utc_datetime(now or datetime.now(timezone.utc))
        assert instante is not None
        self.finalizacion_reportada_en = instante
        self.vencimiento_confirmacion_en = calculate_confirmation_expiration(
            instante,
            window_hours=window_hours,
        )
        self.pendiente_recepcion = True
        return True


class DirectRequest(_CompletionTrackingMixin, Base):
    __tablename__ = 'solicitudes_directas'
    __table_args__ = (
        Index("ix_solicitudes_directas_vencimiento_confirmacion", "vencimiento_confirmacion_en"),
    )

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

    # Presupuesto (HU-21, HU-20 y HU-37)
    cantidad = Column(Numeric(10, 2), nullable=False)
    tarifa_aplicada = Column(Numeric(10, 2), nullable=False)
    unidad_cobro = Column(String, nullable=False)
    monto_base = Column(Numeric(10, 2), nullable=False)
    metodo_pago = Column(String, nullable=False) # 'efectivo' o 'tarjeta'
    referencia_pasarela = Column(String(100), nullable=True)
    monto_ajuste = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    metodo_pago_ajuste = Column(String(20), nullable=True) # 'efectivo' o 'tarjeta'
    referencia_pasarela_ajuste = Column(String(100), nullable=True)
    monto_total = Column(Numeric(10, 2), nullable=True)
    estado = Column(String, nullable=False, default="PENDIENTE") # PENDIENTE, ACEPTADA, EN_PROCESO, RECHAZADA, CANCELADA, COMPLETADA
    creada_en = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # HU-34 ST-01: Finalización reportada y vencimiento de confirmación (48 horas)
    finalizacion_reportada_en = Column(DateTime(timezone=True), nullable=True, default=None)
    vencimiento_confirmacion_en = Column(DateTime(timezone=True), nullable=True, default=None)
    pendiente_recepcion = Column(Boolean, nullable=False, default=False, server_default="false")
    # Relaciones para facilitar consultas en SQLAlchemy
    cliente = relationship("User", foreign_keys=[cliente_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])
    servicio = relationship("Service", foreign_keys=[servicio_id])

    @property
    def desglose_medios(self) -> dict:
        base = Decimal(str(self.monto_base or 0)).quantize(Decimal("0.01"))
        ajuste = Decimal(str(self.monto_ajuste or 0)).quantize(Decimal("0.01"))
        total = (self.monto_total if self.monto_total is not None else (base + ajuste))
        total_dec = Decimal(str(total)).quantize(Decimal("0.01"))
        return {
            "moneda": "HNL",
            "monto_base": str(base),
            "metodo_pago_base": self.metodo_pago,
            "referencia_pasarela_base": self.referencia_pasarela,
            "monto_ajuste": str(ajuste),
            "metodo_pago_ajuste": self.metodo_pago_ajuste,
            "referencia_pasarela_ajuste": self.referencia_pasarela_ajuste,
            "monto_total": str(total_dec),
            "permite_cambio_metodo": self.estado in ["PENDIENTE"],
        }


class OpenRequest(_CompletionTrackingMixin, Base):
    __tablename__ = "solicitudes_abiertas"
    __table_args__ = (
        Index("ix_solicitudes_abiertas_vencimiento_confirmacion", "vencimiento_confirmacion_en"),
    )

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
    referencia_pasarela = Column(String(100), nullable=True)
    monto_ajuste = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    metodo_pago_ajuste = Column(String(20), nullable=True)  # 'efectivo' o 'tarjeta'
    referencia_pasarela_ajuste = Column(String(100), nullable=True)
    monto_total = Column(Numeric(10, 2), nullable=True)
    estado = Column(String(30), nullable=False, default="Publicada", server_default="Publicada")
    creada_en = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())
    # HU-36 ST-02: enlace a la solicitud cancelada que originó esta licitación
    solicitud_origen_id = Column(String(36), nullable=True, index=True)
    solicitud_origen_modalidad = Column(String(10), nullable=True)
    # HU-34 ST-01: Finalización reportada y vencimiento de confirmación (48 horas)
    finalizacion_reportada_en = Column(DateTime(timezone=True), nullable=True, default=None)
    vencimiento_confirmacion_en = Column(DateTime(timezone=True), nullable=True, default=None)
    pendiente_recepcion = Column(Boolean, nullable=False, default=False, server_default="false")

    cliente = relationship("User", foreign_keys=[cliente_id])
    tecnico = relationship("User", foreign_keys=[tecnico_id])
    especialidad = relationship("Specialty", foreign_keys=[especialidad_id])

    @property
    def desglose_medios(self) -> dict:
        base = Decimal(str(self.monto_base or self.presupuesto or 0)).quantize(Decimal("0.01"))
        ajuste = Decimal(str(self.monto_ajuste or 0)).quantize(Decimal("0.01"))
        total = (self.monto_total if self.monto_total is not None else (base + ajuste))
        total_dec = Decimal(str(total)).quantize(Decimal("0.01"))
        return {
            "moneda": "HNL",
            "monto_base": str(base),
            "metodo_pago_base": self.metodo_pago,
            "referencia_pasarela_base": self.referencia_pasarela,
            "monto_ajuste": str(ajuste),
            "metodo_pago_ajuste": self.metodo_pago_ajuste,
            "referencia_pasarela_ajuste": self.referencia_pasarela_ajuste,
            "monto_total": str(total_dec),
            "permite_cambio_metodo": (self.estado or "").strip().lower().replace("_", " ") in {"publicada", "con ofertas", "negociacion"},
        }


class RequestStateEvent(Base):
    __tablename__ = "historial_estados_solicitud"
    __table_args__ = (
        Index("ix_historial_estado_solicitud", "modalidad", "solicitud_id", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    modalidad = Column(String(10), nullable=False)
    solicitud_id = Column(String(36), nullable=False)
    estado = Column(String(30), nullable=False)
    fecha = Column(DateTime(timezone=True), nullable=True)
    responsable = Column(String(20), nullable=True)
    responsable_id = Column(String(128), nullable=True)


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


class TradeCertificate(Base):
    __tablename__ = "certificaciones_oficio"
    __table_args__ = (
        CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'REVOCADO')", name="ck_certificacion_estado"),
        Index("ix_certificacion_tecnico_estado", "usuario_id", "estado"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_tecnicos.usuario_id"))
    titulo: Mapped[str] = mapped_column(String(150))
    ruta_privada: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(50))
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    observaciones: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TradeCertificateDecision(Base):
    __tablename__ = "decisiones_certificaciones_oficio"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    certificacion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("certificaciones_oficio.id"), index=True)
    administrador_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"))
    estado: Mapped[str] = mapped_column(String(20))
    observaciones: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class IncidentTicketRecord(Base):
    __tablename__ = "tickets_incidencias"
    __table_args__ = (
        Index("ix_tickets_incidencias_solicitud_estado", "solicitud_id", "estado"),
    )

    ticket_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    solicitud_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    modalidad: Mapped[str] = mapped_column(String(20), nullable=False)
    reportante_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    contraparte_uid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rol_reportante: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    prioridad: Mapped[str] = mapped_column(String(20), nullable=False, default="ALTA")
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ABIERTO", index=True)
    es_apelacion_justificada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    falta_computada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    bloquea_cierre_automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    bloquea_liberacion_fondos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )

class RequestCancellation(Base):
    """HU-36 ST-01: auditoría de cancelación (autor, fecha, estado previo, evidencia, falta)."""
    __tablename__ = "cancelaciones_solicitud"
    __table_args__ = (
        UniqueConstraint("modalidad", "solicitud_id", name="uq_cancelacion_solicitud"),
        UniqueConstraint("solicitud_reabierta_id", name="uq_cancelaciones_reabierta"),
        Index("ix_cancelaciones_responsable", "responsable_uid", "estado_incumplimiento"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    modalidad = Column(String(10), nullable=False)
    solicitud_id = Column(String(36), nullable=False)
    autor_uid = Column(String(128), nullable=False)
    autor_rol = Column(String(20), nullable=False)
    estado_previo = Column(String(30), nullable=False)
    causa = Column(String(40), nullable=False)
    motivo = Column(Text, nullable=False)
    evidencias = Column(JSON, nullable=False, default=list)
    regla_aplicada = Column(String(60), nullable=False)
    genera_incumplimiento = Column(Boolean, nullable=False, default=False, server_default="false")
    responsable_rol = Column(String(20), nullable=True)
    responsable_uid = Column(String(128), nullable=True)
    estado_incumplimiento = Column(String(30), nullable=False, default="NO_APLICA", server_default="NO_APLICA")
    permite_reapertura = Column(Boolean, nullable=False, default=False, server_default="false")
    cancelada_en = Column(DateTime(timezone=True), nullable=False)
    solicitud_reabierta_id = Column(String(36), nullable=True)
    reabierta_en = Column(DateTime(timezone=True), nullable=True)

class ServiceAdjustment(Base):
    """HU-36 ST-03: ajuste único de presupuesto durante En proceso.

    El nombre y las columnas solicitud_id/estado los consume DatabaseAdjustmentStrategy (HU-34).
    No definir columna `activo`.
    """
    __tablename__ = "ajustes_servicio"
    __table_args__ = (
        UniqueConstraint("modalidad", "solicitud_id", name="uq_ajuste_unico_solicitud"),
        CheckConstraint("monto_base > 0", name="ck_ajuste_monto_base_positivo"),
        CheckConstraint("nuevo_total > 0", name="ck_ajuste_nuevo_total_positivo"),
        CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO')", name="ck_ajuste_estado"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    modalidad = Column(String(10), nullable=False)
    solicitud_id = Column(String(36), nullable=False)
    tecnico_uid = Column(String(128), nullable=False)
    cliente_uid = Column(String(128), nullable=False)
    motivo = Column(Text, nullable=False)
    monto_base = Column(Numeric(10, 2), nullable=False)
    nuevo_total = Column(Numeric(10, 2), nullable=False)
    diferencia = Column(Numeric(10, 2), nullable=False)
    estado = Column(String(12), nullable=False, default="PENDIENTE", server_default="PENDIENTE")
    solicitado_en = Column(DateTime(timezone=True), nullable=False)
    respondido_en = Column(DateTime(timezone=True), nullable=True)
    respuesta_uid = Column(String(128), nullable=True)
    comentario_respuesta = Column(Text, nullable=True)

class CancellationJustification(Base):
    """HU-36 ST-04 (CA-09): justificación con evidencia enviada a administración."""
    __tablename__ = "justificaciones_cancelacion"
    __table_args__ = (UniqueConstraint("cancelacion_id", name="uq_justificacion_cancelacion"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cancelacion_id = Column(Integer, ForeignKey("cancelaciones_solicitud.id"), nullable=False)
    autor_uid = Column(String(128), nullable=False)
    motivo = Column(Text, nullable=False)
    evidencias = Column(JSON, nullable=False, default=list)
    estado = Column(String(12), nullable=False, default="PENDIENTE", server_default="PENDIENTE")
    enviada_en = Column(DateTime(timezone=True), nullable=False)
    resuelta_en = Column(DateTime(timezone=True), nullable=True)
    admin_uid = Column(String(128), nullable=True)
    observaciones_admin = Column(Text, nullable=True)

class PaymentRefund(Base):
    """HU-36 ST-05 (CA-06, CA-08): devolución idempotente del 100% digital cobrado, sin comisión."""
    __tablename__ = "reembolsos_solicitud"
    __table_args__ = (
        UniqueConstraint("modalidad", "solicitud_id", name="uq_reembolso_solicitud"),
        CheckConstraint("monto_reembolso > 0", name="ck_reembolso_monto_positivo"),
        CheckConstraint("monto_comision = 0", name="ck_reembolso_sin_comision"),
        CheckConstraint(
            "estado_pasarela IN ('EN_PROCESO_PASARELA', 'CONFIRMADO_PASARELA')",
            name="ck_reembolso_estado_pasarela",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    modalidad = Column(String(10), nullable=False)
    solicitud_id = Column(String(36), nullable=False)
    cliente_uid = Column(String(128), nullable=False)
    monto_base_reembolsado = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    monto_ajuste_reembolsado = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    monto_reembolso = Column(Numeric(10, 2), nullable=False)
    monto_comision = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    referencia_pasarela_origen = Column(String(220), nullable=False)
    referencia_reembolso_pasarela = Column(String(120), nullable=False)
    estado_pasarela = Column(String(30), nullable=False, default="EN_PROCESO_PASARELA", server_default="EN_PROCESO_PASARELA")
    iniciado_en = Column(DateTime(timezone=True), nullable=False)
    confirmado_en = Column(DateTime(timezone=True), nullable=True)


class FinancialLedgerEntry(Base):
    """HU-36 ST-05 (Condición transversal): movimientos idempotentes del libro financiero."""
    __tablename__ = "movimientos_libro_financiero"
    __table_args__ = (
        UniqueConstraint("clave_idempotencia", name="uq_libro_financiero_idempotencia"),
        Index("ix_libro_financiero_solicitud", "modalidad", "solicitud_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    clave_idempotencia = Column(String(120), nullable=False)
    modalidad = Column(String(10), nullable=False)
    solicitud_id = Column(String(36), nullable=False)
    tipo_movimiento = Column(String(40), nullable=False)
    moneda = Column(String(5), nullable=False, default="HNL", server_default="HNL")
    monto_base = Column(Numeric(10, 2), nullable=False)
    monto_ajuste = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    monto_total = Column(Numeric(10, 2), nullable=False)
    tasa_comision = Column(Numeric(6, 4), nullable=False, default=Decimal("0.0000"))
    monto_comision = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    metodo_pago = Column(String(30), nullable=False)
    referencia_pasarela = Column(String(220), nullable=True)
    detalle = Column(JSON, nullable=False, default=dict)
    creado_en = Column(DateTime(timezone=True), nullable=False)