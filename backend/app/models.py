import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
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
