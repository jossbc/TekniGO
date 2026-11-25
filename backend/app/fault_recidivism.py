"""HU-36 ST-06 (CA-09, CA-10): cómputo de faltas y evento idempotente de tercera reincidencia."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    RecidivismAlertEvent,
    RequestCancellation,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.notifications_events import notify_third_fault_admin_alert

RECIDIVISM_THRESHOLD = 3
COMPUTABLE_FAULT_STATES = frozenset({"PENDIENTE_JUSTIFICACION", "NO_JUSTIFICADA"})
EXCLUDED_FAULT_STATES = frozenset({"JUSTIFICADA"})


@dataclass(frozen=True)
class FaultRecord:
    origen: str
    registro_id: str
    solicitud_id: str
    modalidad: str
    rol_infractor: str | None
    causa: str
    estado_incumplimiento: str
    computable: bool
    ocurrida_en: datetime

    def to_dict(self) -> dict:
        return {
            "origen": self.origen,
            "registro_id": self.registro_id,
            "solicitud_id": self.solicitud_id,
            "modalidad": self.modalidad,
            "rol_infractor": self.rol_infractor,
            "causa": self.causa,
            "estado_incumplimiento": self.estado_incumplimiento,
            "computable": self.computable,
            "ocurrida_en": format_tegucigalpa_iso(self.ocurrida_en),
        }


class FaultSource(ABC):
    """Contrato (OCP vía interfaz): permite sumar fuentes de faltas sin modificar el cómputo."""

    @abstractmethod
    def collect_for_user(self, db: Session, user_uid: str) -> list[FaultRecord]: ...


class CancellationFaultSource(FaultSource):
    """Fuente de faltas derivadas de cancelaciones registradas en HU-36."""

    def collect_for_user(self, db: Session, user_uid: str) -> list[FaultRecord]:
        rows = list(
            db.scalars(
                select(RequestCancellation)
                .where(
                    RequestCancellation.responsable_uid == user_uid,
                    RequestCancellation.genera_incumplimiento.is_(True),
                )
                .order_by(RequestCancellation.cancelada_en.asc(), RequestCancellation.id.asc())
            ).all()
        )
        items: list[FaultRecord] = []
        for row in rows:
            estado = (row.estado_incumplimiento or "").strip().upper()
            if estado not in COMPUTABLE_FAULT_STATES and estado not in EXCLUDED_FAULT_STATES:
                continue
            items.append(
                FaultRecord(
                    origen="CANCELACION",
                    registro_id=str(row.id),
                    solicitud_id=row.solicitud_id,
                    modalidad=row.modalidad,
                    rol_infractor=row.responsable_rol,
                    causa=row.causa,
                    estado_incumplimiento=estado,
                    computable=estado in COMPUTABLE_FAULT_STATES,
                    ocurrida_en=ensure_utc_datetime(row.cancelada_en),
                )
            )
        return items


class RecidivismEventRepository(Protocol):
    def get_active_for_user(self, db: Session, user_uid: str) -> RecidivismAlertEvent | None: ...
    def get_by_key(self, db: Session, clave_idempotencia: str) -> RecidivismAlertEvent | None: ...
    def list_all(self, db: Session, *, estado: str | None = None) -> list[RecidivismAlertEvent]: ...
    def add(self, db: Session, event: RecidivismAlertEvent) -> None: ...


class SqlAlchemyRecidivismEventRepository:
    def get_active_for_user(self, db: Session, user_uid: str) -> RecidivismAlertEvent | None:
        return db.scalar(
            select(RecidivismAlertEvent)
            .where(
                RecidivismAlertEvent.usuario_uid == user_uid,
                RecidivismAlertEvent.estado == "PENDIENTE_DECISION_HU52",
            )
            .order_by(RecidivismAlertEvent.generado_en.desc())
            .limit(1)
        )

    def get_by_key(self, db: Session, clave_idempotencia: str) -> RecidivismAlertEvent | None:
        return db.scalar(
            select(RecidivismAlertEvent).where(RecidivismAlertEvent.clave_idempotencia == clave_idempotencia)
        )

    def list_all(self, db: Session, *, estado: str | None = None) -> list[RecidivismAlertEvent]:
        query = select(RecidivismAlertEvent).order_by(RecidivismAlertEvent.generado_en.desc())
        if estado and estado.upper() != "TODOS":
            query = query.where(RecidivismAlertEvent.estado == estado.upper())
        return list(db.scalars(query).all())

    def add(self, db: Session, event: RecidivismAlertEvent) -> None:
        db.add(event)


def serialize_recidivism_event(event: RecidivismAlertEvent, *, usuario_nombre: str | None = None) -> dict:
    payload = {
        "id": event.id,
        "tipo_evento": "TERCERA_FALTA_NO_JUSTIFICADA",
        "clave_idempotencia": event.clave_idempotencia,
        "usuario_uid": event.usuario_uid,
        "rol_infractor": event.rol_infractor,
        "umbral_faltas": event.umbral_faltas,
        "total_faltas_computadas": event.total_faltas_computadas,
        "faltas_ids": list(event.faltas_ids or []),
        "cancelacion_disparadora_id": event.cancelacion_disparadora_id,
        "solicitud_disparadora_id": event.solicitud_disparadora_id,
        "estado": event.estado,
        "requiere_decision_hu52": bool(event.requiere_decision_hu52),
        "suspension_automatica_aplicada": bool(event.suspension_automatica_aplicada),
        "generado_en": format_tegucigalpa_iso(event.generado_en),
        "actualizado_en": format_tegucigalpa_iso(event.actualizado_en),
    }
    if usuario_nombre is not None:
        payload["usuario_nombre"] = usuario_nombre
    return payload


class RecidivismEventDispatcher(ABC):
    """Contrato (OCP vía interfaz): despacha o actualiza el evento de tercera reincidencia."""

    @abstractmethod
    def emit_if_threshold_reached(
        self,
        db: Session,
        *,
        user_uid: str,
        rol_infractor: str | None,
        computable_faults: Sequence[FaultRecord],
        trigger_cancellation_id: int | None,
        trigger_solicitud_id: str | None,
        now: datetime,
    ) -> tuple[RecidivismAlertEvent | None, bool]: ...


class PersistentRecidivismDispatcher(RecidivismEventDispatcher):
    """Persiste un evento idempotente en BD y notifica a administración (HU-47) sin suspender."""

    def __init__(
        self,
        repository: RecidivismEventRepository | None = None,
        threshold: int = RECIDIVISM_THRESHOLD,
    ):
        self._repo = repository or SqlAlchemyRecidivismEventRepository()
        self._threshold = threshold

    def emit_if_threshold_reached(
        self,
        db: Session,
        *,
        user_uid: str,
        rol_infractor: str | None,
        computable_faults: Sequence[FaultRecord],
        trigger_cancellation_id: int | None,
        trigger_solicitud_id: str | None,
        now: datetime,
    ) -> tuple[RecidivismAlertEvent | None, bool]:
        total = len(computable_faults)
        if total < self._threshold:
            return None, False

        moment = ensure_utc_datetime(now)
        fault_ids = [item.registro_id for item in computable_faults]

        # Idempotencia por alerta pendiente activa: no duplica el evento ante 4.ª falta o reintentos
        active = self._repo.get_active_for_user(db, user_uid)
        if active is not None:
            active.total_faltas_computadas = total
            active.faltas_ids = fault_ids
            active.actualizado_en = moment
            db.flush()
            return active, True

        key = f"REINCIDENCIA-{self._threshold}-{user_uid}-{trigger_cancellation_id or fault_ids[-1]}"
        existing = self._repo.get_by_key(db, key)
        if existing is not None:
            return existing, True

        event = RecidivismAlertEvent(
            clave_idempotencia=key,
            usuario_uid=user_uid,
            rol_infractor=rol_infractor,
            umbral_faltas=self._threshold,
            total_faltas_computadas=total,
            faltas_ids=fault_ids,
            cancelacion_disparadora_id=trigger_cancellation_id,
            solicitud_disparadora_id=trigger_solicitud_id,
            estado="PENDIENTE_DECISION_HU52",
            requiere_decision_hu52=True,
            suspension_automatica_aplicada=False,
            generado_en=moment,
            actualizado_en=moment,
        )
        self._repo.add(db, event)
        db.flush()

        if trigger_solicitud_id:
            notify_third_fault_admin_alert(
                db,
                responsible_uid=user_uid,
                solicitud_id=trigger_solicitud_id,
                now=moment,
            )
        return event, False


class FaultComputationService:
    """Computa faltas no justificadas vs. excluidas (CA-09) y coordina eventos de 3.ª reincidencia (CA-10)."""

    def __init__(
        self,
        sources: Sequence[FaultSource] | None = None,
        repository: RecidivismEventRepository | None = None,
        dispatcher: RecidivismEventDispatcher | None = None,
        threshold: int = RECIDIVISM_THRESHOLD,
    ):
        self._sources = tuple(sources) if sources is not None else (CancellationFaultSource(),)
        self._repo = repository or SqlAlchemyRecidivismEventRepository()
        self._threshold = threshold
        self._dispatcher = dispatcher or PersistentRecidivismDispatcher(self._repo, threshold=threshold)

    def compute_summary(self, db: Session, user_uid: str | None) -> dict:
        if not user_uid:
            return {
                "usuario_uid": None,
                "umbral_reincidencia": self._threshold,
                "total_faltas_no_justificadas": 0,
                "total_faltas_excluidas_justificadas": 0,
                "alerta_tercera_reincidencia_activa": False,
                "requiere_decision_hu52": False,
                "suspension_automatica_aplicada": False,
                "evento_reincidencia_activo": None,
                "faltas_computables": [],
                "faltas_excluidas": [],
            }

        all_records: list[FaultRecord] = []
        for source in self._sources:
            all_records.extend(source.collect_for_user(db, user_uid))
        all_records.sort(key=lambda item: item.ocurrida_en)

        computable = [item for item in all_records if item.computable]
        excluded = [item for item in all_records if not item.computable]
        active_event = self._repo.get_active_for_user(db, user_uid)
        reached = len(computable) >= self._threshold

        return {
            "usuario_uid": user_uid,
            "umbral_reincidencia": self._threshold,
            "total_faltas_no_justificadas": len(computable),
            "total_faltas_excluidas_justificadas": len(excluded),
            "alerta_tercera_reincidencia_activa": reached,
            "requiere_decision_hu52": reached,
            "suspension_automatica_aplicada": False,
            "evento_reincidencia_activo": (
                serialize_recidivism_event(active_event) if active_event is not None else None
            ),
            "faltas_computables": [item.to_dict() for item in computable],
            "faltas_excluidas": [item.to_dict() for item in excluded],
        }

    def evaluate_after_cancellation(
        self,
        db: Session,
        cancellation: RequestCancellation,
        *,
        now: datetime | None = None,
    ) -> tuple[RecidivismAlertEvent | None, bool]:
        if not cancellation.genera_incumplimiento or not cancellation.responsable_uid:
            return None, False
        computable = [
            item
            for source in self._sources
            for item in source.collect_for_user(db, cancellation.responsable_uid)
            if item.computable
        ]
        moment = ensure_utc_datetime(now or cancellation.cancelada_en or datetime.now(timezone.utc))
        return self._dispatcher.emit_if_threshold_reached(
            db,
            user_uid=cancellation.responsable_uid,
            rol_infractor=cancellation.responsable_rol,
            computable_faults=computable,
            trigger_cancellation_id=cancellation.id,
            trigger_solicitud_id=cancellation.solicitud_id,
            now=moment,
        )

    def sync_after_justification_resolution(
        self,
        db: Session,
        user_uid: str | None,
        *,
        now: datetime | None = None,
    ) -> RecidivismAlertEvent | None:
        """CA-09: si una justificación aprobada baja el cómputo debajo de 3, invalida el evento pendiente conservando auditoría."""
        if not user_uid:
            return None
        active = self._repo.get_active_for_user(db, user_uid)
        if active is None:
            return None
        computable = [
            item
            for source in self._sources
            for item in source.collect_for_user(db, user_uid)
            if item.computable
        ]
        moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
        active.total_faltas_computadas = len(computable)
        active.faltas_ids = [item.registro_id for item in computable]
        active.actualizado_en = moment
        if len(computable) < self._threshold:
            active.estado = "INVALIDADO_POR_JUSTIFICACION"
            active.requiere_decision_hu52 = False
        db.flush()
        return active


class RecidivismOnCancellationObserver:
    """Observador de cancelación (ST-06): evalúa cómputo y emite evento persistente en la 3.ª falta."""

    def __init__(self, service: FaultComputationService | None = None):
        self._service = service or FaultComputationService()

    def on_cancelled(self, db: Session, record, cancellation: RequestCancellation) -> None:
        self._service.evaluate_after_cancellation(db, cancellation)


def list_recidivism_alerts_for_admin(
    db: Session,
    *,
    estado: str = "TODOS",
    repository: RecidivismEventRepository | None = None,
) -> list[dict]:
    repo = repository or SqlAlchemyRecidivismEventRepository()
    events = repo.list_all(db, estado=estado)
    uids = {ev.usuario_uid for ev in events if ev.usuario_uid}
    users = (
        {u.firebase_uid: u.nombre_completo for u in db.scalars(select(User).where(User.firebase_uid.in_(uids))).all()}
        if uids
        else {}
    )
    return [serialize_recidivism_event(ev, usuario_nombre=users.get(ev.usuario_uid, "Usuario")) for ev in events]