"""HU-52 ST-05 (CA-06, CA-08): Cierre de cuenta con baja lógica documental, revocación operativa y bloqueo de identidad."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Sequence
import uuid

from firebase_admin import auth as firebase_auth
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.account_suspension_domain import (
    AccountModerationEvent,
    BlockedIdentityReason,
    SuspensionConflictError,
    SuspensionForbiddenError,
    SuspensionNotFoundError,
    SuspensionValidationError,
)
from app.account_suspension_service import register_identity_blocks_for_user
from app.models import (
    ACCOUNT_DELETED,
    AccountSuspensionAudit,
    IdentityDocument,
    Service,
    TechnicianProfile,
    TradeCertificate,
    User,
    UserRole,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)


def _utc_now(now: datetime | None = None) -> datetime:
    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert moment is not None
    return moment


class DocumentSoftDeleteHandler(ABC):
    """Contrato (OCP vía interfaz): aplica baja lógica y revoca acceso operativo sin borrar registros."""

    @property
    @abstractmethod
    def category(self) -> str: ...

    @abstractmethod
    def soft_delete_for_user(
        self,
        db: Session,
        user_id: uuid.UUID,
        *,
        motivo: str,
        moment: datetime,
    ) -> int: ...


class IdentityDocumentSoftDeleteHandler(DocumentSoftDeleteHandler):
    @property
    def category(self) -> str:
        return "DOCUMENTOS_IDENTIDAD"

    def soft_delete_for_user(
        self,
        db: Session,
        user_id: uuid.UUID,
        *,
        motivo: str,
        moment: datetime,
    ) -> int:
        docs = list(
            db.scalars(
                select(IdentityDocument)
                .where(IdentityDocument.usuario_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        affected = 0
        for doc in docs:
            if not doc.baja_logica or not doc.acceso_operativo_revocado:
                doc.baja_logica = True
                doc.baja_logica_en = doc.baja_logica_en or moment
                doc.baja_logica_motivo = motivo
                doc.acceso_operativo_revocado = True
                doc.estado = "INVALIDADO"
                doc.observaciones = f"Baja lógica por cierre de cuenta: {motivo}"
                affected += 1
        return affected


class TradeCertificateSoftDeleteHandler(DocumentSoftDeleteHandler):
    @property
    def category(self) -> str:
        return "CERTIFICACIONES_OFICIO"

    def soft_delete_for_user(
        self,
        db: Session,
        user_id: uuid.UUID,
        *,
        motivo: str,
        moment: datetime,
    ) -> int:
        certs = list(
            db.scalars(
                select(TradeCertificate)
                .where(TradeCertificate.usuario_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        affected = 0
        for cert in certs:
            if not cert.baja_logica or not cert.acceso_operativo_revocado:
                cert.baja_logica = True
                cert.baja_logica_en = cert.baja_logica_en or moment
                cert.baja_logica_motivo = motivo
                cert.acceso_operativo_revocado = True
                cert.estado = "REVOCADO"
                cert.observaciones = f"Baja lógica por cierre de cuenta: {motivo}"
                affected += 1
        return affected


DEFAULT_SOFT_DELETE_HANDLERS: tuple[DocumentSoftDeleteHandler, ...] = (
    IdentityDocumentSoftDeleteHandler(),
    TradeCertificateSoftDeleteHandler(),
)


class AccountClosureService:
    """Ejecuta el cierre de cuenta conservando historial, deudas, auditoría y bloqueo irreversible de identidad."""

    def __init__(
        self,
        db: Session,
        handlers: Sequence[DocumentSoftDeleteHandler] = DEFAULT_SOFT_DELETE_HANDLERS,
    ) -> None:
        self._db = db
        self._handlers = tuple(handlers)

    def apply_closure_to_user(
        self,
        user: User,
        *,
        motivo: str,
        actor_admin: User | None = None,
        proceso_responsable: str = "CIERRE_CUENTA",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Aplica baja lógica documental, bloqueo de identidad y revocación operativa sin hacer commit externo si el caller lo controla."""
        moment = _utc_now(now)
        clean_motivo = (motivo or "Cierre de cuenta solicitado").strip()
        already_closed = user.estado_identidad == ACCOUNT_DELETED

        # 1. CA-06 / CA-08: Registrar bloqueo permanente de DNI y teléfono ANTES de anonimizar usuarios
        blocked_kinds = register_identity_blocks_for_user(
            self._db,
            user,
            reason=BlockedIdentityReason.CIERRE_CUENTA,
            moment=moment,
        )

        # 2. CA-08: Baja lógica y revocación de acceso operativo sobre todos los documentos (sin borrar filas ni hacerlos públicos)
        soft_deleted_by_category: dict[str, int] = {}
        total_docs_soft_deleted = 0
        for handler in self._handlers:
            count = handler.soft_delete_for_user(
                self._db,
                user.id,
                motivo=clean_motivo,
                moment=moment,
            )
            soft_deleted_by_category[handler.category] = count
            total_docs_soft_deleted += count

        # 3. Revocar acceso operativo y anonimizar PII visible manteniendo referencias de historial y deudas
        previous_identity_state = user.estado_identidad
        if not already_closed:
            user.nombre_completo = "Cuenta eliminada"
            user.correo = f"{user.id.hex}@cuenta-eliminada.invalid"
            user.dni = f"X{user.id.hex[:12]}"
            user.telefono = f"X{user.id.hex[:15]}"
            user.telefono_verificado = False
            user.estado_identidad = ACCOUNT_DELETED
            user.suspendido = True
            user.cierre_cuenta_en = moment
            user.motivo_cierre_cuenta = clean_motivo

            technician = self._db.get(TechnicianProfile, user.id)
            if technician is not None:
                technician.disponible = False
                technician.suspendido = True
                self._db.execute(update(Service).where(Service.usuario_id == user.id).values(activo=False))
            self._db.execute(delete(UserRole).where(UserRole.usuario_id == user.id))

            self._db.add(
                AccountSuspensionAudit(
                    usuario_id=user.id,
                    suspension_id=None,
                    evento=AccountModerationEvent.CIERRE_CUENTA_APLICADO.value,
                    tipo_suspension=None,
                    causa=None,
                    estado_anterior=previous_identity_state,
                    estado_nuevo=ACCOUNT_DELETED,
                    motivo=clean_motivo,
                    actor_admin_id=actor_admin.id if actor_admin is not None else None,
                    proceso_responsable=proceso_responsable,
                    metadatos={
                        "identidades_bloqueadas": blocked_kinds,
                        "documentos_baja_logica": soft_deleted_by_category,
                        "total_documentos_baja_logica": total_docs_soft_deleted,
                        "acceso_operativo_revocado": True,
                    },
                    creado_en=moment,
                )
            )

        if total_docs_soft_deleted > 0:
            self._db.add(
                AccountSuspensionAudit(
                    usuario_id=user.id,
                    suspension_id=None,
                    evento=AccountModerationEvent.BAJA_LOGICA_DOCUMENTAL.value,
                    tipo_suspension=None,
                    causa=None,
                    estado_anterior="ACTIVO_PRIVADO",
                    estado_nuevo="BAJA_LOGICA_REVOCADA",
                    motivo=clean_motivo,
                    actor_admin_id=actor_admin.id if actor_admin is not None else None,
                    proceso_responsable=proceso_responsable,
                    metadatos={
                        "detalle_por_categoria": soft_deleted_by_category,
                        "total_documentos": total_docs_soft_deleted,
                        "conservacion_privada_mantenida": True,
                    },
                    creado_en=moment,
                )
            )

        self._db.flush()
        return {
            "usuario_id": str(user.id),
            "estado_cuenta": user.estado_identidad,
            "cierre_cuenta_en": format_tegucigalpa_iso(user.cierre_cuenta_en),
            "ya_cerrada": already_closed,
            "identidades_bloqueadas": blocked_kinds,
            "documentos_baja_logica": soft_deleted_by_category,
            "total_documentos_baja_logica": total_docs_soft_deleted,
            "acceso_operativo_revocado": True,
        }

    def close_account_by_admin(
        self,
        admin_user: User,
        target_user_id: uuid.UUID,
        *,
        motivo: str,
        revoke_firebase: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not any(r.rol == "ADMIN" for r in admin_user.roles):
            raise SuspensionForbiddenError("Solo administración puede tramitar el cierre administrativo de una cuenta.")
        clean_motivo = (motivo or "").strip()
        if len(clean_motivo) < 5 or len(clean_motivo) > 1500:
            raise SuspensionValidationError("El motivo de cierre de cuenta debe tener entre 5 y 1500 caracteres.")

        target = self._db.scalar(
            select(User).where(User.id == target_user_id).with_for_update().execution_options(populate_existing=True)
        )
        if target is None:
            raise SuspensionNotFoundError("Cuenta de usuario no encontrada.")
        if target.id == admin_user.id or any(r.rol == "ADMIN" for r in target.roles):
            raise SuspensionConflictError("No se puede cerrar una cuenta administradora.")

        firebase_uid = target.firebase_uid
        result = self.apply_closure_to_user(
            target,
            motivo=clean_motivo,
            actor_admin=admin_user,
            proceso_responsable="CIERRE_CUENTA_ADMIN",
            now=now,
        )
        self._db.commit()

        # Revocación best-effort de tokens de sesión en Firebase sin fallar en entornos sin credenciales Firebase
        token_revoked = False
        if revoke_firebase and firebase_uid:
            try:
                firebase_auth.revoke_refresh_tokens(firebase_uid)
                token_revoked = True
            except Exception:
                token_revoked = False
        result["tokens_sesion_revocados"] = token_revoked
        return result