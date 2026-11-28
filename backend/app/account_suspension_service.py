"""HU-52 ST-02: Servicio de suspensiones de cuenta y permisos operativos centralizados (SOLID + OCP)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Sequence
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.account_suspension_domain import (
    AccountModerationEvent,
    BlockedIdentityKind,
    BlockedIdentityReason,
    DimensionEvaluationResult,
    OperationalDimension,
    OperationalDimensionEvaluator,
    SuspensionCause,
    SuspensionConflictError,
    SuspensionForbiddenError,
    SuspensionNotFoundError,
    SuspensionStateRegistry,
    SuspensionStatus,
    SuspensionType,
    SuspensionValidationError,
    default_suspension_state_registry,
    hash_identity_value,
    normalize_suspension_cause,
    normalize_suspension_type,
    validate_suspension_schedule,
)
from app.fault_recidivism import FaultComputationService
from app.models import (
    ACCOUNT_DELETED,
    AccountSuspension,
    AccountSuspensionAudit,
    BlockedIdentity,
    RecidivismAlertEvent,
    TechnicianProfile,
    TechnicianStatusAudit,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.notifications_events import (
    notify_account_suspended,
    notify_temporal_suspension_ended,
)


def _utc_now(now: datetime | None = None) -> datetime:
    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    assert moment is not None
    return moment


def _is_admin(user: User) -> bool:
    return any(role.rol == "ADMIN" for role in user.roles)


def _assert_admin(user: User) -> None:
    if not _is_admin(user):
        raise SuspensionForbiddenError("Solo administración puede gestionar suspensiones y moderación de cuentas.")


# --- Evaluadores concretos de dimensiones independientes (OCP vía interfaz OperationalDimensionEvaluator) ---

class AccountActiveDimensionEvaluator(OperationalDimensionEvaluator):
    @property
    def dimension(self) -> OperationalDimension:
        return OperationalDimension.CUENTA_ACTIVA

    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult:
        closed = getattr(user, "estado_identidad", None) == ACCOUNT_DELETED
        return DimensionEvaluationResult(
            dimension=self.dimension,
            habilitada=not closed,
            causa_codigo="CUENTA_CERRADA" if closed else None,
            motivo="Esta cuenta fue cerrada y su acceso operativo está revocado." if closed else None,
        )


class ConductSuspensionDimensionEvaluator(OperationalDimensionEvaluator):
    """Dimensión disciplinaria/conducta: completamente independiente de restricción financiera (CA-07)."""

    @property
    def dimension(self) -> OperationalDimension:
        return OperationalDimension.SUSPENSION_CONDUCTA

    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult:
        suspended = bool(
            getattr(user, "suspendido", False)
            or (profile is not None and getattr(profile, "suspendido", False))
            or (active_suspension is not None and active_suspension.estado == SuspensionStatus.ACTIVA.value)
        )
        if not suspended:
            return DimensionEvaluationResult(dimension=self.dimension, habilitada=True)

        cause_code = active_suspension.causa if active_suspension is not None else SuspensionCause.CONDUCTA.value
        tipo = active_suspension.tipo if active_suspension is not None else getattr(user, "suspension_tipo", None)
        vence = (
            format_tegucigalpa_iso(active_suspension.vence_en)
            if active_suspension is not None and active_suspension.vence_en is not None
            else format_tegucigalpa_iso(getattr(user, "suspension_vence_en", None))
        )
        if tipo == SuspensionType.TEMPORAL.value and vence:
            motivo = f"La cuenta tiene una suspensión temporal por conducta vigente hasta {vence}."
        elif tipo == SuspensionType.DEFINITIVA.value:
            motivo = "La cuenta tiene una suspensión definitiva por conducta."
        else:
            motivo = "La cuenta presenta una suspensión por conducta activa."
        return DimensionEvaluationResult(
            dimension=self.dimension,
            habilitada=False,
            causa_codigo=cause_code,
            motivo=motivo,
            es_suspension_conducta=True,
            es_restriccion_financiera=False,
        )


class FinancialRestrictionDimensionEvaluator(OperationalDimensionEvaluator):
    """Dimensión financiera por saldo/comisiones: nunca equivale a suspensión por conducta (CA-07)."""

    @property
    def dimension(self) -> OperationalDimension:
        return OperationalDimension.RESTRICCION_FINANCIERA

    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult:
        restricted = bool(profile is not None and getattr(profile, "restriccion_financiera", False))
        return DimensionEvaluationResult(
            dimension=self.dimension,
            habilitada=not restricted,
            causa_codigo="RESTRICCION_FINANCIERA" if restricted else None,
            motivo=(
                "La cuenta presenta una restricción financiera activa por saldo o comisiones pendientes (distinta de una suspensión por conducta)."
                if restricted
                else None
            ),
            es_restriccion_financiera=restricted,
            es_suspension_conducta=False,
        )


class VerificationDimensionEvaluator(OperationalDimensionEvaluator):
    @property
    def dimension(self) -> OperationalDimension:
        return OperationalDimension.VERIFICACION

    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult:
        identity_ok = getattr(user, "estado_identidad", None) == "VERIFICADO"
        phone_ok = bool(getattr(user, "telefono_verificado", False))
        tech_ok = True if profile is None else getattr(profile, "estado_verificacion", None) == "VERIFICADO"
        ok = identity_ok and phone_ok and tech_ok
        reasons: list[str] = []
        if not identity_ok:
            reasons.append("identidad no verificada")
        if not phone_ok:
            reasons.append("teléfono no verificado")
        if not tech_ok:
            reasons.append("verificación técnica pendiente o restringida")
        return DimensionEvaluationResult(
            dimension=self.dimension,
            habilitada=ok,
            causa_codigo=None if ok else "VERIFICACION_INCOMPLETA",
            motivo=None if ok else f"Requisitos de verificación pendientes: {', '.join(reasons)}.",
        )


class AvailabilityDimensionEvaluator(OperationalDimensionEvaluator):
    @property
    def dimension(self) -> OperationalDimension:
        return OperationalDimension.DISPONIBILIDAD

    def evaluate(self, *, user: Any, profile: Any | None, active_suspension: Any | None) -> DimensionEvaluationResult:
        if profile is None:
            return DimensionEvaluationResult(dimension=self.dimension, habilitada=True)
        available = bool(getattr(profile, "disponible", False))
        return DimensionEvaluationResult(
            dimension=self.dimension,
            habilitada=available,
            causa_codigo=None if available else "NO_DISPONIBLE",
            motivo=None if available else "El técnico se encuentra configurado como No disponible.",
        )


DEFAULT_DIMENSION_EVALUATORS: tuple[OperationalDimensionEvaluator, ...] = (
    AccountActiveDimensionEvaluator(),
    ConductSuspensionDimensionEvaluator(),
    FinancialRestrictionDimensionEvaluator(),
    VerificationDimensionEvaluator(),
    AvailabilityDimensionEvaluator(),
)


# --- Repositorio (DIP) ---

class AccountSuspensionRepository(ABC):
    @abstractmethod
    def get_user_by_id(self, user_id: uuid.UUID, *, lock: bool = False) -> User | None: ...

    @abstractmethod
    def get_user_by_uid(self, firebase_uid: str, *, lock: bool = False) -> User | None: ...

    @abstractmethod
    def get_active_suspension(self, user_id: uuid.UUID, *, lock: bool = False) -> AccountSuspension | None: ...

    @abstractmethod
    def get_suspension_by_id(self, suspension_id: uuid.UUID, *, lock: bool = False) -> AccountSuspension | None: ...

    @abstractmethod
    def list_suspensions_for_user(self, user_id: uuid.UUID) -> list[AccountSuspension]: ...

    @abstractmethod
    def list_overdue_temporal_suspensions(self, now: datetime) -> list[AccountSuspension]: ...

    @abstractmethod
    def list_audits_for_user(self, user_id: uuid.UUID) -> list[AccountSuspensionAudit]: ...

    @abstractmethod
    def add_suspension(self, item: AccountSuspension) -> None: ...

    @abstractmethod
    def add_audit(self, item: AccountSuspensionAudit) -> None: ...

    @abstractmethod
    def upsert_blocked_identity(
        self,
        *,
        user_id: uuid.UUID,
        kind: BlockedIdentityKind,
        value_hash: str,
        reason: BlockedIdentityReason,
        moment: datetime,
    ) -> BlockedIdentity: ...

    @abstractmethod
    def deactivate_suspension_identity_blocks(self, user_id: uuid.UUID, moment: datetime) -> int: ...

    @abstractmethod
    def find_active_blocked_identity(
        self,
        kind: BlockedIdentityKind,
        value_hash: str,
        *,
        exclude_user_id: uuid.UUID | None = None,
    ) -> BlockedIdentity | None: ...


class SqlAlchemyAccountSuspensionRepository(AccountSuspensionRepository):
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_user_by_id(self, user_id: uuid.UUID, *, lock: bool = False) -> User | None:
        query = select(User).where(User.id == user_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def get_user_by_uid(self, firebase_uid: str, *, lock: bool = False) -> User | None:
        query = select(User).where(User.firebase_uid == firebase_uid)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def get_active_suspension(self, user_id: uuid.UUID, *, lock: bool = False) -> AccountSuspension | None:
        query = (
            select(AccountSuspension)
            .where(
                AccountSuspension.usuario_id == user_id,
                AccountSuspension.estado == SuspensionStatus.ACTIVA.value,
            )
            .order_by(AccountSuspension.iniciada_en.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def get_suspension_by_id(self, suspension_id: uuid.UUID, *, lock: bool = False) -> AccountSuspension | None:
        query = select(AccountSuspension).where(AccountSuspension.id == suspension_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def list_suspensions_for_user(self, user_id: uuid.UUID) -> list[AccountSuspension]:
        return list(
            self._db.scalars(
                select(AccountSuspension)
                .where(AccountSuspension.usuario_id == user_id)
                .order_by(AccountSuspension.iniciada_en.desc(), AccountSuspension.id.desc())
            ).all()
        )

    def list_overdue_temporal_suspensions(self, now: datetime) -> list[AccountSuspension]:
        eval_now = _utc_now(now)
        return list(
            self._db.scalars(
                select(AccountSuspension)
                .where(
                    AccountSuspension.estado == SuspensionStatus.ACTIVA.value,
                    AccountSuspension.tipo == SuspensionType.TEMPORAL.value,
                    AccountSuspension.vence_en.is_not(None),
                    AccountSuspension.vence_en <= eval_now,
                )
                .order_by(AccountSuspension.vence_en.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )

    def list_audits_for_user(self, user_id: uuid.UUID) -> list[AccountSuspensionAudit]:
        return list(
            self._db.scalars(
                select(AccountSuspensionAudit)
                .where(AccountSuspensionAudit.usuario_id == user_id)
                .order_by(AccountSuspensionAudit.creado_en.desc(), AccountSuspensionAudit.id.desc())
            ).all()
        )

    def add_suspension(self, item: AccountSuspension) -> None:
        self._db.add(item)

    def add_audit(self, item: AccountSuspensionAudit) -> None:
        self._db.add(item)

    def upsert_blocked_identity(
        self,
        *,
        user_id: uuid.UUID,
        kind: BlockedIdentityKind,
        value_hash: str,
        reason: BlockedIdentityReason,
        moment: datetime,
    ) -> BlockedIdentity:
        existing = self._db.scalar(
            select(BlockedIdentity).where(
                BlockedIdentity.tipo_identificador == kind.value,
                BlockedIdentity.valor_hash == value_hash,
            )
        )
        if existing is not None:
            existing.activo = True
            # CIERRE_CUENTA prevalece como bloqueo permanente sobre SUSPENSION_ACTIVA
            if reason is BlockedIdentityReason.CIERRE_CUENTA or existing.motivo_bloqueo != BlockedIdentityReason.CIERRE_CUENTA.value:
                existing.motivo_bloqueo = reason.value
            existing.actualizado_en = moment
            return existing
        row = BlockedIdentity(
            usuario_id=user_id,
            tipo_identificador=kind.value,
            valor_hash=value_hash,
            motivo_bloqueo=reason.value,
            activo=True,
            creado_en=moment,
            actualizado_en=moment,
        )
        self._db.add(row)
        return row

    def deactivate_suspension_identity_blocks(self, user_id: uuid.UUID, moment: datetime) -> int:
        rows = list(
            self._db.scalars(
                select(BlockedIdentity).where(
                    BlockedIdentity.usuario_id == user_id,
                    BlockedIdentity.motivo_bloqueo == BlockedIdentityReason.SUSPENSION_ACTIVA.value,
                    BlockedIdentity.activo.is_(True),
                )
            ).all()
        )
        for row in rows:
            row.activo = False
            row.actualizado_en = moment
        return len(rows)

    def find_active_blocked_identity(
        self,
        kind: BlockedIdentityKind,
        value_hash: str,
        *,
        exclude_user_id: uuid.UUID | None = None,
    ) -> BlockedIdentity | None:
        query = select(BlockedIdentity).where(
            BlockedIdentity.tipo_identificador == kind.value,
            BlockedIdentity.valor_hash == value_hash,
            BlockedIdentity.activo.is_(True),
        )
        if exclude_user_id is not None:
            query = query.where(BlockedIdentity.usuario_id != exclude_user_id)
        return self._db.scalar(query.limit(1))


# --- Serializadores ---

def serialize_suspension(
    item: AccountSuspension,
    *,
    admin_names: dict[uuid.UUID, str] | None = None,
) -> dict[str, Any]:
    names = admin_names or {}
    return {
        "id": str(item.id),
        "usuario_id": str(item.usuario_id),
        "usuario_uid": item.usuario_uid,
        "tipo": item.tipo,
        "causa": item.causa,
        "motivo": item.motivo,
        "estado": item.estado,
        "responsable_admin_id": str(item.responsable_admin_id),
        "responsable_admin_uid": item.responsable_admin_uid,
        "responsable_admin_nombre": names.get(item.responsable_admin_id),
        "iniciada_en": format_tegucigalpa_iso(item.iniciada_en),
        "vence_en": format_tegucigalpa_iso(item.vence_en),
        "levantada_en": format_tegucigalpa_iso(item.levantada_en),
        "levantada_por_admin_id": str(item.levantada_por_admin_id) if item.levantada_por_admin_id else None,
        "levantada_por_admin_nombre": names.get(item.levantada_por_admin_id) if item.levantada_por_admin_id else None,
        "motivo_levantamiento": item.motivo_levantamiento,
        "expirada_en": format_tegucigalpa_iso(item.expirada_en),
        "alerta_reincidencia_id": item.alerta_reincidencia_id,
        "actualizado_en": format_tegucigalpa_iso(item.actualizado_en),
    }


def serialize_suspension_audit(
    item: AccountSuspensionAudit,
    *,
    admin_names: dict[uuid.UUID, str] | None = None,
) -> dict[str, Any]:
    names = admin_names or {}
    return {
        "id": str(item.id),
        "usuario_id": str(item.usuario_id),
        "suspension_id": str(item.suspension_id) if item.suspension_id else None,
        "evento": item.evento,
        "tipo_suspension": item.tipo_suspension,
        "causa": item.causa,
        "estado_anterior": item.estado_anterior,
        "estado_nuevo": item.estado_nuevo,
        "motivo": item.motivo,
        "actor_admin_id": str(item.actor_admin_id) if item.actor_admin_id else None,
        "actor_admin_nombre": names.get(item.actor_admin_id) if item.actor_admin_id else None,
        "proceso_responsable": item.proceso_responsable,
        "metadatos": dict(item.metadatos or {}),
        "creado_en": format_tegucigalpa_iso(item.creado_en),
    }


# --- Política operativa centralizada (CA-02, CA-05, CA-06, CA-07) ---

class AccountOperationalPolicy:
    """Evalúa en tiempo real todas las dimensiones operativas independientes de una cuenta."""

    def __init__(
        self,
        evaluators: Sequence[OperationalDimensionEvaluator] = DEFAULT_DIMENSION_EVALUATORS,
    ) -> None:
        self._evaluators = tuple(evaluators)

    def sync_expired_if_needed(
        self,
        db: Session,
        user: User,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Si la suspensión temporal del usuario ya venció, la expira en línea (ST-04 hook)."""
        repo = SqlAlchemyAccountSuspensionRepository(db)
        active = repo.get_active_suspension(user.id)
        if active is None:
            return False
        eval_now = _utc_now(now)
        if active.tipo == SuspensionType.TEMPORAL.value and active.vence_en is not None:
            vence_utc = ensure_utc_datetime(active.vence_en)
            if vence_utc is not None and vence_utc <= eval_now:
                _expire_single_suspension_locked(
                    db,
                    repo,
                    user=user,
                    suspension=active,
                    moment=eval_now,
                    proceso_responsable="EXPIRACION_EN_LINEA_OPERATIVA",
                )
                db.flush()
                return True
        return False

    def evaluate_dimensions(
        self,
        db: Session,
        user: User,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self.sync_expired_if_needed(db, user, now=now)
        repo = SqlAlchemyAccountSuspensionRepository(db)
        profile = db.get(TechnicianProfile, user.id)
        active_suspension = repo.get_active_suspension(user.id)

        results = [
            evaluator.evaluate(user=user, profile=profile, active_suspension=active_suspension)
            for evaluator in self._evaluators
        ]
        by_dim = {res.dimension: res for res in results}

        closed = not by_dim[OperationalDimension.CUENTA_ACTIVA].habilitada
        suspended = not by_dim[OperationalDimension.SUSPENSION_CONDUCTA].habilitada
        financial_restricted = not by_dim[OperationalDimension.RESTRICCION_FINANCIERA].habilitada
        verified = by_dim[OperationalDimension.VERIFICACION].habilitada
        available = by_dim[OperationalDimension.DISPONIBILIDAD].habilitada

        roles = sorted(r.rol for r in user.roles)
        has_client_role = "CLIENTE" in roles
        has_tech_role = "TECNICO" in roles and profile is not None

        can_client_hire = bool(
            has_client_role
            and not closed
            and not suspended
            and user.estado_identidad == "VERIFICADO"
            and user.telefono_verificado
        )
        can_tech_operate = bool(
            has_tech_role
            and not closed
            and not suspended
            and not financial_restricted
            and verified
            and available
        )

        blockers = [res.motivo for res in results if not res.habilitada and res.motivo]
        return {
            "usuario_id": str(user.id),
            "roles": roles,
            "cuenta_cerrada": closed,
            "suspendido_por_conducta": suspended,
            "restriccion_financiera": financial_restricted,
            "verificacion_vigente": verified,
            "disponible": available if profile is not None else None,
            "puede_contratar_como_cliente": can_client_hire,
            "puede_operar_como_tecnico": can_tech_operate,
            "habilitado_operativamente": (can_tech_operate if has_tech_role else can_client_hire),
            "motivos_bloqueo": blockers,
            "dimensiones": [res.to_dict() for res in results],
            "suspension_activa": serialize_suspension(active_suspension) if active_suspension is not None else None,
        }


default_operational_policy = AccountOperationalPolicy()


def _expire_single_suspension_locked(
    db: Session,
    repo: AccountSuspensionRepository,
    *,
    user: User,
    suspension: AccountSuspension,
    moment: datetime,
    proceso_responsable: str,
) -> dict[str, Any]:
    """Transiciona una suspensión temporal vencida a EXPIRADA y evalúa restitución condicionada (CA-05)."""
    profile = db.get(TechnicianProfile, user.id)
    prev_enabled_tech = bool(
        profile is not None
        and user.telefono_verificado
        and profile.estado_verificacion == "VERIFICADO"
        and profile.disponible
        and not user.suspendido
        and not profile.suspendido
        and not profile.restriccion_financiera
    )

    suspension.estado = SuspensionStatus.EXPIRADA.value
    suspension.expirada_en = moment
    suspension.actualizado_en = moment

    user.suspendido = False
    user.suspension_tipo = None
    user.suspension_vence_en = None
    if profile is not None:
        profile.suspendido = False

    repo.deactivate_suspension_identity_blocks(user.id, moment)

    new_enabled_tech = bool(
        profile is not None
        and user.estado_identidad != ACCOUNT_DELETED
        and user.telefono_verificado
        and profile.estado_verificacion == "VERIFICADO"
        and profile.disponible
        and not profile.restriccion_financiera
    )
    remaining_blockers: list[str] = []
    if user.estado_identidad != "VERIFICADO":
        remaining_blockers.append("VERIFICACION_IDENTIDAD")
    if not user.telefono_verificado:
        remaining_blockers.append("TELEFONO_NO_VERIFICADO")
    if profile is not None:
        if profile.estado_verificacion != "VERIFICADO":
            remaining_blockers.append("VERIFICACION_TECNICA")
        if not profile.disponible:
            remaining_blockers.append("NO_DISPONIBLE")
        if profile.restriccion_financiera:
            remaining_blockers.append("RESTRICCION_FINANCIERA")

    repo.add_audit(
        AccountSuspensionAudit(
            usuario_id=user.id,
            suspension_id=suspension.id,
            evento=AccountModerationEvent.SUSPENSION_EXPIRADA.value,
            tipo_suspension=suspension.tipo,
            causa=suspension.causa,
            estado_anterior=SuspensionStatus.ACTIVA.value,
            estado_nuevo=SuspensionStatus.EXPIRADA.value,
            motivo="Vencimiento automático de suspensión temporal.",
            actor_admin_id=None,
            proceso_responsable=proceso_responsable,
            metadatos={
                "operaciones_restituidas": len(remaining_blockers) == 0,
                "bloqueos_pendientes": remaining_blockers,
            },
            creado_en=moment,
        )
    )
    if profile is not None:
        db.add(
            TechnicianStatusAudit(
                usuario_id=profile.usuario_id,
                estado_anterior=profile.estado_verificacion,
                estado_nuevo=profile.estado_verificacion,
                habilitado_anterior=prev_enabled_tech,
                habilitado_nuevo=new_enabled_tech,
                causa=(
                    "Suspensión temporal expirada: operaciones restituidas."
                    if new_enabled_tech
                    else f"Suspensión temporal expirada; persisten bloqueos independientes: {', '.join(remaining_blockers)}."
                ),
                actor_usuario_id=None,
                proceso_responsable=proceso_responsable,
                creado_en=moment,
            )
        )
    notify_temporal_suspension_ended(
        db,
        user,
        suspension_id=suspension.id,
        ended_by="EXPIRADA",
        operaciones_restituidas=(len(remaining_blockers) == 0),
        now=moment,
    )
    return {
        "suspension_id": str(suspension.id),
        "usuario_id": str(user.id),
        "operaciones_restituidas": len(remaining_blockers) == 0,
        "bloqueos_pendientes": remaining_blockers,
    }


def assert_client_can_operate(
    db: Session,
    user: User,
    *,
    action_label: str = "crear contrataciones",
    now: datetime | None = None,
) -> None:
    """Permiso operativo centralizado para cliente (CA-02): re-evalúa en tiempo real cada acción."""
    if default_operational_policy.sync_expired_if_needed(db, user, now=now):
        db.commit()
    if user.estado_identidad == ACCOUNT_DELETED:
        raise HTTPException(410, "Esta cuenta de TekniGO fue cerrada.")
    profile = db.get(TechnicianProfile, user.id)
    if getattr(user, "suspendido", False) or (profile is not None and profile.suspendido):
        raise HTTPException(
            403,
            f"Tu cuenta presenta una suspensión por conducta activa y no puede {action_label}.",
        )


def assert_technician_can_operate(
    db: Session,
    user: User,
    profile: TechnicianProfile | None,
    *,
    action_label: str = "aceptar trabajos o postular",
    now: datetime | None = None,
) -> None:
    """Permiso operativo centralizado para técnico separando conducta vs finanzas (CA-02, CA-07)."""
    if default_operational_policy.sync_expired_if_needed(db, user, now=now):
        db.commit()
    if user.estado_identidad == ACCOUNT_DELETED:
        raise HTTPException(410, "Esta cuenta de TekniGO fue cerrada.")
    if getattr(user, "suspendido", False) or (profile is not None and profile.suspendido):
        raise HTTPException(
            403,
            f"Tu cuenta presenta una suspensión por conducta activa y no puede {action_label}.",
        )
    if profile is not None and profile.restriccion_financiera:
        raise HTTPException(
            403,
            "Tu perfil técnico presenta una restricción financiera activa por saldo (distinta de una suspensión por conducta).",
        )


def assert_identity_not_blocked(
    db: Session,
    *,
    dni: str | None = None,
    telefono: str | None = None,
    exclude_user_id: uuid.UUID | None = None,
) -> None:
    """Impide reutilizar DNI o teléfono de cuentas suspendidas o cerradas (CA-06, CA-08)."""
    repo = SqlAlchemyAccountSuspensionRepository(db)
    if dni:
        dni_hash = hash_identity_value(BlockedIdentityKind.DNI, dni)
        if dni_hash:
            blocked = repo.find_active_blocked_identity(
                BlockedIdentityKind.DNI,
                dni_hash,
                exclude_user_id=exclude_user_id,
            )
            if blocked is not None:
                raise HTTPException(
                    409,
                    "El DNI indicado pertenece a una cuenta suspendida o cerrada y no puede reutilizarse.",
                )
    if telefono:
        phone_hash = hash_identity_value(BlockedIdentityKind.TELEFONO, telefono)
        if phone_hash:
            blocked = repo.find_active_blocked_identity(
                BlockedIdentityKind.TELEFONO,
                phone_hash,
                exclude_user_id=exclude_user_id,
            )
            if blocked is not None:
                raise HTTPException(
                    409,
                    "El teléfono indicado pertenece a una cuenta suspendida o cerrada y no puede reutilizarse.",
                )


def register_identity_blocks_for_user(
    db: Session,
    user: User,
    *,
    reason: BlockedIdentityReason,
    moment: datetime,
) -> list[str]:
    """Registra los hashes SHA-256 de DNI y teléfono del usuario en identidades_bloqueadas."""
    repo = SqlAlchemyAccountSuspensionRepository(db)
    blocked_kinds: list[str] = []
    dni_hash = hash_identity_value(BlockedIdentityKind.DNI, user.dni)
    if dni_hash:
        repo.upsert_blocked_identity(
            user_id=user.id,
            kind=BlockedIdentityKind.DNI,
            value_hash=dni_hash,
            reason=reason,
            moment=moment,
        )
        blocked_kinds.append(BlockedIdentityKind.DNI.value)
    phone_hash = hash_identity_value(BlockedIdentityKind.TELEFONO, user.telefono)
    if phone_hash:
        repo.upsert_blocked_identity(
            user_id=user.id,
            kind=BlockedIdentityKind.TELEFONO,
            value_hash=phone_hash,
            reason=reason,
            moment=moment,
        )
        blocked_kinds.append(BlockedIdentityKind.TELEFONO.value)
    return blocked_kinds


# --- Servicio de casos de uso administrativos (ST-02) ---

class AccountSuspensionService:
    """Casos de uso de administración de suspensiones, auditoría y dimensiones operativas."""

    def __init__(
        self,
        db: Session,
        repository: AccountSuspensionRepository | None = None,
        *,
        policy: AccountOperationalPolicy | None = None,
        state_registry: SuspensionStateRegistry | None = None,
        fault_service: FaultComputationService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository or SqlAlchemyAccountSuspensionRepository(db)
        self._policy = policy or default_operational_policy
        self._states = state_registry or default_suspension_state_registry
        self._faults = fault_service or FaultComputationService()

    def _admin_names_map(self, admin_ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
        clean_ids = {aid for aid in admin_ids if aid is not None}
        if not clean_ids:
            return {}
        users = self._db.scalars(select(User).where(User.id.in_(clean_ids))).all()
        return {u.id: u.nombre_completo for u in users}

    def apply_suspension(
        self,
        admin_user: User,
        target_user_id: uuid.UUID,
        *,
        tipo: str,
        causa: str,
        motivo: str,
        vence_en: datetime | None = None,
        alerta_reincidencia_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """CA-01, CA-02, CA-06, CA-07: Solo administración aplica suspensión temporal o definitiva."""
        _assert_admin(admin_user)
        moment = _utc_now(now)

        norm_type = normalize_suspension_type(tipo)
        norm_cause = normalize_suspension_cause(causa)
        clean_motivo = (motivo or "").strip()
        if len(clean_motivo) < 5 or len(clean_motivo) > 1500:
            raise SuspensionValidationError("El motivo debe tener entre 5 y 1500 caracteres.")
        normalized_vence = validate_suspension_schedule(norm_type, moment, vence_en)

        target = self._repo.get_user_by_id(target_user_id, lock=True)
        if target is None:
            raise SuspensionNotFoundError("Cuenta de usuario no encontrada.")
        if target.id == admin_user.id or _is_admin(target):
            raise SuspensionConflictError("No es posible suspender una cuenta administradora.")
        if target.estado_identidad == ACCOUNT_DELETED:
            raise SuspensionConflictError("La cuenta ya se encuentra cerrada y no admite nuevas suspensiones.")

        # Si tenía una temporal vencida, primero la expira antes de evaluar si existe otra activa
        self._policy.sync_expired_if_needed(self._db, target, now=moment)
        existing_active = self._repo.get_active_suspension(target.id, lock=True)
        if existing_active is not None:
            raise SuspensionConflictError("La cuenta ya tiene una suspensión activa.")

        linked_alert: RecidivismAlertEvent | None = None
        if alerta_reincidencia_id:
            linked_alert = self._db.get(RecidivismAlertEvent, alerta_reincidencia_id)
            if linked_alert is None or linked_alert.usuario_uid != target.firebase_uid:
                raise SuspensionNotFoundError("Alerta de reincidencia no encontrada para esta cuenta.")

        profile = self._db.get(TechnicianProfile, target.id)
        prev_enabled_tech = bool(
            profile is not None
            and target.telefono_verificado
            and profile.estado_verificacion == "VERIFICADO"
            and profile.disponible
            and not target.suspendido
            and not profile.suspendido
            and not profile.restriccion_financiera
        )

        suspension = AccountSuspension(
            usuario_id=target.id,
            usuario_uid=target.firebase_uid,
            tipo=norm_type.value,
            causa=norm_cause.value,
            motivo=clean_motivo,
            estado=SuspensionStatus.ACTIVA.value,
            responsable_admin_id=admin_user.id,
            responsable_admin_uid=admin_user.firebase_uid,
            iniciada_en=moment,
            vence_en=normalized_vence,
            alerta_reincidencia_id=linked_alert.id if linked_alert is not None else None,
            actualizado_en=moment,
        )
        self._repo.add_suspension(suspension)
        self._db.flush()

        # CA-02 / CA-06 / CA-07: Suspende operativamente sin tocar DNI, teléfono, deudas ni historial
        target.suspendido = True
        target.suspension_tipo = norm_type.value
        target.suspension_vence_en = normalized_vence
        if profile is not None:
            profile.suspendido = True

        blocked_kinds = register_identity_blocks_for_user(
            self._db,
            target,
            reason=BlockedIdentityReason.SUSPENSION_ACTIVA,
            moment=moment,
        )

        if linked_alert is not None and linked_alert.estado == "PENDIENTE_DECISION_HU52":
            linked_alert.estado = "EVALUADO_HU52"
            linked_alert.requiere_decision_hu52 = False
            linked_alert.actualizado_en = moment

        self._repo.add_audit(
            AccountSuspensionAudit(
                usuario_id=target.id,
                suspension_id=suspension.id,
                evento=AccountModerationEvent.SUSPENSION_APLICADA.value,
                tipo_suspension=norm_type.value,
                causa=norm_cause.value,
                estado_anterior="ACTIVA_SIN_SUSPENSION",
                estado_nuevo=SuspensionStatus.ACTIVA.value,
                motivo=clean_motivo,
                actor_admin_id=admin_user.id,
                proceso_responsable="MODERACION_ADMIN_MANUAL",
                metadatos={
                    "vence_en": format_tegucigalpa_iso(normalized_vence),
                    "identidades_bloqueadas": blocked_kinds,
                    "alerta_reincidencia_id": linked_alert.id if linked_alert is not None else None,
                    "restriccion_financiera_independiente": bool(profile.restriccion_financiera) if profile else False,
                },
                creado_en=moment,
            )
        )

        if profile is not None:
            self._db.add(
                TechnicianStatusAudit(
                    usuario_id=profile.usuario_id,
                    estado_anterior=profile.estado_verificacion,
                    estado_nuevo=profile.estado_verificacion,
                    habilitado_anterior=prev_enabled_tech,
                    habilitado_nuevo=False,
                    causa=f"Suspensión {norm_type.value.lower()} por {norm_cause.value}: {clean_motivo}",
                    actor_usuario_id=admin_user.id,
                    proceso_responsable="SUSPENSION_ADMIN_CUENTA",
                    creado_en=moment,
                )
            )
        notify_account_suspended(
            self._db,
            target,
            suspension_id=suspension.id,
            tipo=norm_type.value,
            vence_en=normalized_vence,
            now=moment,
        )
        self._db.commit()
        self._db.refresh(suspension)
        names = {admin_user.id: admin_user.nombre_completo}
        return {
            "suspension": serialize_suspension(suspension, admin_names=names),
            "estado_operativo": self._policy.evaluate_dimensions(self._db, target, now=moment),
        }

    def list_accounts_for_admin(
        self,
        admin_user: User,
        *,
        search: str = "",
        filtro: str = "TODOS",
        offset: int = 0,
        limit: int = 25,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _assert_admin(admin_user)
        moment = _utc_now(now)

        # Sincronizar cualquier temporal vencida antes de construir el listado
        overdue = self._repo.list_overdue_temporal_suspensions(moment)
        for item in overdue:
            user_row = self._repo.get_user_by_id(item.usuario_id, lock=True)
            if user_row is not None:
                _expire_single_suspension_locked(
                    self._db,
                    self._repo,
                    user=user_row,
                    suspension=item,
                    moment=moment,
                    proceso_responsable="EXPIRACION_CONSULTA_PANEL_ADMIN",
                )
        if overdue:
            self._db.commit()

        users = list(self._db.scalars(select(User).order_by(User.creado_en.desc())).all())
        clean_search = (search or "").strip().lower()
        norm_filter = (filtro or "TODOS").strip().upper()

        rows: list[dict[str, Any]] = []
        for u in users:
            if _is_admin(u):
                continue
            if clean_search:
                haystack = f"{u.nombre_completo} {u.correo} {u.dni} {u.telefono}".lower()
                if clean_search not in haystack:
                    continue

            profile = self._db.get(TechnicianProfile, u.id)
            dimensions = self._policy.evaluate_dimensions(self._db, u, now=moment)
            fault_summary = self._faults.compute_summary(self._db, u.firebase_uid)

            if norm_filter == "SUSPENDIDOS" and not dimensions["suspendido_por_conducta"]:
                continue
            if norm_filter == "RESTRICCION_FINANCIERA" and not dimensions["restriccion_financiera"]:
                continue
            if norm_filter == "CON_ALERTA_FALTAS" and not fault_summary["alerta_tercera_reincidencia_activa"]:
                continue
            if norm_filter == "CERRADAS" and not dimensions["cuenta_cerrada"]:
                continue

            rows.append(
                {
                    "usuario_id": str(u.id),
                    "firebase_uid": u.firebase_uid,
                    "nombre_completo": u.nombre_completo,
                    "correo": u.correo,
                    "roles": dimensions["roles"],
                    "estado_identidad": u.estado_identidad,
                    "estado_verificacion": profile.estado_verificacion if profile else None,
                    "disponible": bool(profile.disponible) if profile else None,
                    "cuenta_cerrada": dimensions["cuenta_cerrada"],
                    "suspendido_por_conducta": dimensions["suspendido_por_conducta"],
                    "restriccion_financiera": dimensions["restriccion_financiera"],
                    "habilitado_operativamente": dimensions["habilitado_operativamente"],
                    "suspension_activa": dimensions["suspension_activa"],
                    "faltas_no_justificadas": fault_summary["total_faltas_no_justificadas"],
                    "alerta_tercera_falta_activa": fault_summary["alerta_tercera_reincidencia_activa"],
                    "evento_reincidencia_activo": fault_summary["evento_reincidencia_activo"],
                }
            )

        total = len(rows)
        sliced = rows[offset : offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "filtro": norm_filter,
            "cuentas": sliced,
        }

    def get_account_moderation_detail(
        self,
        admin_user: User,
        target_user_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _assert_admin(admin_user)
        moment = _utc_now(now)
        target = self._repo.get_user_by_id(target_user_id)
        if target is None:
            raise SuspensionNotFoundError("Cuenta de usuario no encontrada.")

        if self._policy.sync_expired_if_needed(self._db, target, now=moment):
            self._db.commit()

        profile = self._db.get(TechnicianProfile, target.id)
        suspensions = self._repo.list_suspensions_for_user(target.id)
        audits = self._repo.list_audits_for_user(target.id)
        admin_ids: set[uuid.UUID | None] = set()
        for s in suspensions:
            admin_ids.add(s.responsable_admin_id)
            admin_ids.add(s.levantada_por_admin_id)
        for a in audits:
            admin_ids.add(a.actor_admin_id)
        names = self._admin_names_map(admin_ids)

        dimensions = self._policy.evaluate_dimensions(self._db, target, now=moment)
        fault_summary = self._faults.compute_summary(self._db, target.firebase_uid)

        return {
            "usuario": {
                "usuario_id": str(target.id),
                "firebase_uid": target.firebase_uid,
                "nombre_completo": target.nombre_completo,
                "correo": target.correo,
                "dni": target.dni,
                "telefono": target.telefono,
                "telefono_verificado": bool(target.telefono_verificado),
                "estado_identidad": target.estado_identidad,
                "estado_verificacion": profile.estado_verificacion if profile else None,
                "disponible": bool(profile.disponible) if profile else None,
                "cierre_cuenta_en": format_tegucigalpa_iso(target.cierre_cuenta_en),
                "motivo_cierre_cuenta": target.motivo_cierre_cuenta,
                "roles": dimensions["roles"],
            },
            "estado_operativo": dimensions,
            "resumen_faltas": fault_summary,
            "historial_suspensiones": [serialize_suspension(s, admin_names=names) for s in suspensions],
            "auditoria": [serialize_suspension_audit(a, admin_names=names) for a in audits],
        }

    def get_my_account_restrictions(self, user: User, *, now: datetime | None = None) -> dict[str, Any]:
        moment = _utc_now(now)
        if self._policy.sync_expired_if_needed(self._db, user, now=moment):
            self._db.commit()
        return self._policy.evaluate_dimensions(self._db, user, now=moment)

    def resolve_recidivism_alert_without_suspension(
        self,
        admin_user: User,
        alert_id: str,
        *,
        observaciones: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """CA-03: Permite a administración atender/desestimar la alerta de 3.ª falta sin aplicar suspensión."""
        _assert_admin(admin_user)
        clean_obs = (observaciones or "").strip()
        if len(clean_obs) < 5 or len(clean_obs) > 1500:
            raise SuspensionValidationError("Las observaciones deben tener entre 5 y 1500 caracteres.")

        moment = _utc_now(now)
        alert = self._db.scalar(
            select(RecidivismAlertEvent)
            .where(RecidivismAlertEvent.id == alert_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if alert is None:
            raise SuspensionNotFoundError("Alerta de reincidencia no encontrada.")

        target = self._repo.get_user_by_uid(alert.usuario_uid)
        if target is None:
            raise SuspensionNotFoundError("Usuario asociado a la alerta no encontrado.")

        if alert.estado == "EVALUADO_HU52":
            return {
                "alerta_id": alert.id,
                "estado": alert.estado,
                "suspension_aplicada": False,
                "reintento_idempotente": True,
                "resumen_faltas": self._faults.compute_summary(self._db, target.firebase_uid),
            }
        if alert.estado == "INVALIDADO_POR_JUSTIFICACION":
            raise SuspensionConflictError(
                "La alerta fue invalidada tras aprobar una justificación y ya no requiere decisión."
            )

        previous_state = alert.estado
        alert.estado = "EVALUADO_HU52"
        alert.requiere_decision_hu52 = False
        alert.suspension_automatica_aplicada = False
        alert.actualizado_en = moment

        self._repo.add_audit(
            AccountSuspensionAudit(
                usuario_id=target.id,
                suspension_id=None,
                evento=AccountModerationEvent.ALERTA_REINCIDENCIA_ATENDIDA.value,
                tipo_suspension=None,
                causa=SuspensionCause.REINCIDENCIA_FALTAS.value,
                estado_anterior=previous_state,
                estado_nuevo=alert.estado,
                motivo=clean_obs,
                actor_admin_id=admin_user.id,
                proceso_responsable="DECISION_ADMIN_ALERTA_REINCIDENCIA",
                metadatos={
                    "alerta_reincidencia_id": alert.id,
                    "suspension_aplicada": False,
                    "total_faltas_computadas": alert.total_faltas_computadas,
                },
                creado_en=moment,
            )
        )
        self._db.commit()
        return {
            "alerta_id": alert.id,
            "estado": alert.estado,
            "suspension_aplicada": False,
            "reintento_idempotente": False,
            "resumen_faltas": self._faults.compute_summary(self._db, target.firebase_uid),
        }

    def lift_temporal_suspension(
        self,
        admin_user: User,
        suspension_id: uuid.UUID,
        *,
        motivo_levantamiento: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """CA-05: Levanta una suspensión temporal activa; solo restituye operaciones si verificación, disponibilidad y saldo lo permiten."""
        _assert_admin(admin_user)
        clean_motivo = (motivo_levantamiento or "").strip()
        if len(clean_motivo) < 5 or len(clean_motivo) > 1500:
            raise SuspensionValidationError("El motivo de levantamiento debe tener entre 5 y 1500 caracteres.")

        moment = _utc_now(now)
        suspension = self._repo.get_suspension_by_id(suspension_id, lock=True)
        if suspension is None:
            raise SuspensionNotFoundError("Suspensión no encontrada.")

        target = self._repo.get_user_by_id(suspension.usuario_id, lock=True)
        if target is None:
            raise SuspensionNotFoundError("Cuenta asociada a la suspensión no encontrada.")

        if suspension.estado == SuspensionStatus.LEVANTADA.value:
            estado_op = self._policy.evaluate_dimensions(self._db, target, now=moment)
            names = self._admin_names_map({suspension.responsable_admin_id, suspension.levantada_por_admin_id})
            return {
                "suspension": serialize_suspension(suspension, admin_names=names),
                "reintento_idempotente": True,
                "operaciones_restituidas": estado_op["habilitado_operativamente"],
                "estado_operativo": estado_op,
            }

        susp_type = SuspensionType(suspension.tipo)
        state = self._states.resolve(suspension.estado)
        if not state.can_lift(susp_type):
            if suspension.estado == SuspensionStatus.EXPIRADA.value:
                raise SuspensionConflictError("La suspensión temporal ya expiró y no requiere levantamiento.")
            raise SuspensionConflictError(
                "Solo una suspensión temporal activa puede levantarse mediante este flujo."
            )

        # Si el plazo ya venció por reloj, se registra como EXPIRADA en lugar de levantamiento anticipado
        vence_utc = ensure_utc_datetime(suspension.vence_en)
        if vence_utc is not None and vence_utc <= moment:
            _expire_single_suspension_locked(
                self._db,
                self._repo,
                user=target,
                suspension=suspension,
                moment=moment,
                proceso_responsable="EXPIRACION_AL_INTENTAR_LEVANTAR",
            )
            self._db.commit()
            self._db.refresh(suspension)
            estado_op = self._policy.evaluate_dimensions(self._db, target, now=moment)
            names = self._admin_names_map({suspension.responsable_admin_id})
            return {
                "suspension": serialize_suspension(suspension, admin_names=names),
                "reintento_idempotente": False,
                "operaciones_restituidas": estado_op["habilitado_operativamente"],
                "estado_operativo": estado_op,
            }

        profile = self._db.get(TechnicianProfile, target.id)
        prev_enabled_tech = bool(
            profile is not None
            and target.telefono_verificado
            and profile.estado_verificacion == "VERIFICADO"
            and profile.disponible
            and not target.suspendido
            and not profile.suspendido
            and not profile.restriccion_financiera
        )

        suspension.estado = SuspensionStatus.LEVANTADA.value
        suspension.levantada_en = moment
        suspension.levantada_por_admin_id = admin_user.id
        suspension.motivo_levantamiento = clean_motivo
        suspension.actualizado_en = moment

        # Nunca modifica restriccion_financiera, disponible ni estado_verificacion (CA-05, CA-07)
        target.suspendido = False
        target.suspension_tipo = None
        target.suspension_vence_en = None
        if profile is not None:
            profile.suspendido = False

        self._repo.deactivate_suspension_identity_blocks(target.id, moment)
        self._db.flush()

        estado_op = self._policy.evaluate_dimensions(self._db, target, now=moment)
        restored = bool(estado_op["habilitado_operativamente"])

        self._repo.add_audit(
            AccountSuspensionAudit(
                usuario_id=target.id,
                suspension_id=suspension.id,
                evento=AccountModerationEvent.SUSPENSION_LEVANTADA.value,
                tipo_suspension=suspension.tipo,
                causa=suspension.causa,
                estado_anterior=SuspensionStatus.ACTIVA.value,
                estado_nuevo=SuspensionStatus.LEVANTADA.value,
                motivo=clean_motivo,
                actor_admin_id=admin_user.id,
                proceso_responsable="LEVANTAMIENTO_ADMIN_TEMPORAL",
                metadatos={
                    "operaciones_restituidas": restored,
                    "motivos_bloqueo_pendientes": estado_op["motivos_bloqueo"],
                    "restriccion_financiera_activa": estado_op["restriccion_financiera"],
                },
                creado_en=moment,
            )
        )

        if profile is not None:
            self._db.add(
                TechnicianStatusAudit(
                    usuario_id=profile.usuario_id,
                    estado_anterior=profile.estado_verificacion,
                    estado_nuevo=profile.estado_verificacion,
                    habilitado_anterior=prev_enabled_tech,
                    habilitado_nuevo=bool(estado_op["puede_operar_como_tecnico"]),
                    causa=(
                        f"Suspensión temporal levantada por administración ({clean_motivo}): operaciones restituidas."
                        if estado_op["puede_operar_como_tecnico"]
                        else f"Suspensión temporal levantada ({clean_motivo}), pero persisten restricciones independientes: "
                             f"{' '.join(estado_op['motivos_bloqueo'])}"
                    ),
                    actor_usuario_id=admin_user.id,
                    proceso_responsable="LEVANTAMIENTO_ADMIN_TEMPORAL",
                    creado_en=moment,
                )
            )
        notify_temporal_suspension_ended(
            self._db,
            target,
            suspension_id=suspension.id,
            ended_by="LEVANTADA",
            operaciones_restituidas=restored,
            now=moment,
        )
        self._db.commit()
        self._db.refresh(suspension)
        names = self._admin_names_map({suspension.responsable_admin_id, admin_user.id})
        return {
            "suspension": serialize_suspension(suspension, admin_names=names),
            "reintento_idempotente": False,
            "operaciones_restituidas": restored,
            "estado_operativo": estado_op,
        }

    def expire_overdue_suspensions(
        self,
        admin_user: User | None = None,
        *,
        now: datetime | None = None,
        proceso_responsable: str = "EXPIRACION_PROGRAMADA_SUSPENSIONES",
    ) -> dict[str, Any]:
        """CA-05: Expira suspensiones temporales vencidas y reporta si las operaciones quedaron restituidas."""
        if admin_user is not None:
            _assert_admin(admin_user)
        moment = _utc_now(now)
        overdue = self._repo.list_overdue_temporal_suspensions(moment)
        results: list[dict[str, Any]] = []
        for item in overdue:
            target = self._repo.get_user_by_id(item.usuario_id, lock=True)
            if target is None:
                continue
            results.append(
                _expire_single_suspension_locked(
                    self._db,
                    self._repo,
                    user=target,
                    suspension=item,
                    moment=moment,
                    proceso_responsable=proceso_responsable,
                )
            )
        if results:
            self._db.commit()
        return {
            "evaluado_en": format_tegucigalpa_iso(moment),
            "total_expiradas": len(results),
            "detalle": results,
        }

def run_scheduled_suspension_expiration(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Ejecuta el barrido de vencimiento de suspensiones temporales (worker periódico / catálogo)."""
    return AccountSuspensionService(db).expire_overdue_suspensions(
        admin_user=None,
        now=now,
        proceso_responsable="EXPIRACION_PROGRAMADA_SUSPENSIONES",
    )