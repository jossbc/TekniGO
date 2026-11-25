"""HU-35: Confirmar recepción del servicio, cierre automático a las 48h, bloqueo por incidencia y evento financiero único."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import DirectRequest, OpenRequest, User
from app.notifications_events import (
    notify_incident_opened,
    notify_incident_resolved,
    notify_service_auto_completed,
    notify_service_reception_confirmed,
)
from app.request_states import transition_request
from app.security import get_current_user

router = APIRouter(tags=["Confirmación de Recepción (HU-35)"])

# Condición transversal: Zona horaria de usuario America/Tegucigalpa (UTC-06:00) y plazos independientes de 48 horas
TEGUCIGALPA_TZ = timezone(timedelta(hours=-6), name="America/Tegucigalpa")
CONFIRMATION_WINDOW_HOURS = 48
REVIEW_WINDOW_HOURS = 48

def _ensure_utc(dt: datetime | None = None) -> datetime:
    """Garantiza que todo instante se almacene con referencia horaria inequívoca en UTC."""
    target = dt or datetime.now(timezone.utc)
    if target.tzinfo is None:
        return target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc)


def _to_tegucigalpa_iso(dt: datetime | None) -> str | None:
    """Convierte un instante UTC al formato ISO-8601 en America/Tegucigalpa (-06:00)."""
    if dt is None:
        return None
    return _ensure_utc(dt).astimezone(TEGUCIGALPA_TZ).isoformat()


def _canonical_state(raw_estado: str | None) -> str:
    tokens = (raw_estado or "").strip().upper().replace("-", " ").replace("_", " ").split()
    return "_".join(tokens)


def _find_locked_request(db: Session, solicitud_id: str) -> tuple[str, Any]:
    """Busca y bloquea con FOR UPDATE la solicitud (Directa o Abierta) para cierre transaccional."""
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        raise HTTPException(404, "No se encontró la solicitud")

    direct_row = db.scalar(
        select(DirectRequest)
        .where(DirectRequest.id == clean_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if direct_row is not None:
        return "DIRECTA", direct_row

    open_request_model = getattr(models, "OpenRequest", None)
    if open_request_model is not None:
        open_row = db.scalar(
            select(open_request_model)
            .where(open_request_model.id == clean_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if open_row is not None:
            return "ABIERTA", open_row

    raise HTTPException(404, "No se encontró la solicitud")


def _save_tracking(record: Any, tracking: dict[str, Any]) -> None:
    record.seguimiento_recepcion = {
        key: value for key, value in tracking.items() if not key.startswith("_")
    }


def _require_reception_admin(claims: dict, db: Session) -> User:
    user = db.scalar(select(User).where(User.firebase_uid == claims.get("uid")))
    if user is None or not any(role.rol == "ADMIN" for role in user.roles):
        raise HTTPException(403, "Se requiere el rol administrador")
    return user


def get_service_reception_tracking(solicitud_id: str, record: Any = None) -> dict[str, Any]:
    """Devuelve el estado de seguimiento, plazos, evento único y efecto financiero de una contratación."""
    clean_id = (solicitud_id or "").strip()
    state = dict(getattr(record, "seguimiento_recepcion", None) or {})
    for internal, public in (
        ("_reported_at_dt", "finalizacion_reportada_en"),
        ("_confirmation_deadline_dt", "plazo_confirmacion_vence_en"),
        ("_closed_at_dt", "fecha_cierre"),
        ("_review_deadline_dt", "plazo_resena_vence_en"),
    ):
        if state.get(public):
            state[internal] = datetime.fromisoformat(state[public])

    # Sincronizar con las columnas persistidas por HU-34 en base de datos (DirectRequest / OpenRequest)
    if record is not None:
        db_reported_at = getattr(record, "finalizacion_reportada_en", None)
        db_deadline_at = getattr(record, "vencimiento_confirmacion_en", None)
        db_pending_reception = bool(getattr(record, "pendiente_recepcion", False))
        if (db_reported_at is not None or db_pending_reception) and not state.get("finalizacion_reportada"):
            reported_utc = _ensure_utc(db_reported_at) if db_reported_at is not None else _ensure_utc()
            deadline_utc = (
                _ensure_utc(db_deadline_at)
                if db_deadline_at is not None
                else reported_utc + timedelta(hours=CONFIRMATION_WINDOW_HOURS)
            )
            state.update(
                {
                    "solicitud_id": clean_id,
                    "finalizacion_reportada": True,
                    "finalizacion_reportada_en": reported_utc.isoformat(),
                    "finalizacion_reportada_tegucigalpa": _to_tegucigalpa_iso(reported_utc),
                    "plazo_confirmacion_vence_en": deadline_utc.isoformat(),
                    "plazo_confirmacion_vence_tegucigalpa": _to_tegucigalpa_iso(deadline_utc),
                    "_reported_at_dt": reported_utc,
                    "_confirmation_deadline_dt": deadline_utc,
                }
            )

    if not state:
        state = {
            "solicitud_id": clean_id,
            "finalizacion_reportada": False,
            "finalizacion_reportada_en": None,
            "finalizacion_reportada_tegucigalpa": None,
            "plazo_confirmacion_vence_en": None,
            "plazo_confirmacion_vence_tegucigalpa": None,
            "incidencia_abierta": False,
            "incidencia": None,
            "completada": False,
            "fecha_cierre": None,
            "fecha_cierre_tegucigalpa": None,
            "autor_cierre": None,
            "acuerdo_inmutable": False,
            "resenas_habilitadas": False,
            "plazo_resena_vence_en": None,
            "plazo_resena_vence_tegucigalpa": None,
            "evento_cierre": None,
            "efecto_financiero": None,
        }
    # Sincronización inequívoca con el registro de tickets de incidencias
    from app.incident_management import _TICKETS_BY_SERVICE, _INCIDENT_TICKETS
    active_ticket_ids = _TICKETS_BY_SERVICE.get(clean_id, [])
    active_tickets = [
        _INCIDENT_TICKETS[tid] for tid in active_ticket_ids
        if tid in _INCIDENT_TICKETS and _INCIDENT_TICKETS[tid].get("estado") in {"ABIERTO", "EN_REVISION"}
    ]
    tech_ticket = next((t for t in active_tickets if t.get("rol_reportante") == "TECNICO"), None)
    client_ticket = next((t for t in active_tickets if t.get("rol_reportante") == "CLIENTE"), None)

    state["tecnico_reporto_incidencia"] = tech_ticket is not None
    state["cliente_reporto_incidencia"] = client_ticket is not None
    state["es_disputa_cruzada"] = (tech_ticket is not None and client_ticket is not None)

    if active_tickets:
        state["incidencia_abierta"] = True
        state["cierre_automatico_detenido"] = True
        state["liberacion_fondos_retenida"] = True
        primary = tech_ticket or client_ticket or active_tickets[0]
        state["incidencia"] = {
            "incidencia_id": primary.get("ticket_id"),
            "motivo": primary.get("motivo"),
            "estado": primary.get("estado"),
            "reportante_rol": primary.get("rol_reportante"),
            "reportante_nombre": primary.get("reportante_nombre"),
            "abierta_en_tegucigalpa": primary.get("creado_en_tegucigalpa"),
            "es_apelacion_justificada": primary.get("es_apelacion_justificada", False),
        }

    return dict(state)

class ReportCompletionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notas_finalizacion: str | None = Field(
        default=None,
        max_length=500,
        description="Detalle opcional del técnico al reportar la finalización del trabajo.",
    )


class ConfirmReceptionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comentario: str | None = Field(
        default=None,
        max_length=500,
        description="Comentario opcional del cliente al confirmar la recepción del servicio.",
    )


def report_service_completion_by_technician(
    solicitud_id: str,
    claims: dict,
    db: Session,
    *,
    payload: ReportCompletionSchema | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reuse HU-34 validation and preserve the reception response contract."""
    from app.service_completion import report_service_completion

    reported = report_service_completion(
        solicitud_id, claims, db, now=now,
        comentario=payload.notas_finalizacion if payload else None,
    )
    _, record = _find_locked_request(db, solicitud_id)
    return {
        **reported,
        "estado": "EN PROCESO",
        "seguimiento": get_service_reception_tracking(str(record.id), record=record),
    }


def confirm_service_reception_by_client(
    solicitud_id: str,
    claims: dict,
    db: Session,
    *,
    payload: ConfirmReceptionSchema | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-35 ST-01 (CA-01, CA-02, CA-07):
    - Solo el cliente propietario puede confirmar un servicio 'En proceso' con finalización reportada y sin incidencia abierta.
    - Cambia a 'Completada', registra la fecha de cierre y deja el acuerdo y sus estados inmutables.
    - Inicia el plazo independiente de 48 horas para publicación de reseñas.
    """
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    modalidad, record = _find_locked_request(db, solicitud_id)

    # CA-01: Solo el cliente propietario puede confirmar la recepción
    if getattr(record, "cliente_id", None) != user.firebase_uid:
        raise HTTPException(403, "Solo el cliente propietario puede confirmar la recepción de este servicio")

    current_state = _canonical_state(getattr(record, "estado", None))
    tracking = get_service_reception_tracking(str(record.id), record=record)

        # CA-02 y CA-07: Si ya fue completada (por el cliente o por cierre automático concurrente), el acuerdo es inmutable
    if current_state == "COMPLETADA" or tracking.get("completada"):
        completion_bundle = emit_and_process_idempotent_completion_event(
            record,
            modalidad=modalidad,
            autor_cierre=tracking.get("autor_cierre") or "CLIENTE",
            closed_at=tracking.get("_closed_at_dt") or _ensure_utc(now),
        ) if tracking.get("evento_cierre") else {
            "evento_cierre": None, "efecto_financiero": None,
        }
        return {
            "mensaje": "El servicio ya se encontraba en estado Completada y el acuerdo es inmutable.",
            "reintento_idempotente": True,
            "solicitud_id": str(record.id),
            "modalidad": modalidad,
            "estado": "COMPLETADA",
            "fecha_cierre": tracking.get("fecha_cierre"),
            "fecha_cierre_tegucigalpa": tracking.get("fecha_cierre_tegucigalpa"),
            "autor_cierre": tracking.get("autor_cierre") or "CLIENTE",
            "acuerdo_inmutable": True,
            "evento_cierre": completion_bundle["evento_cierre"],
            "efecto_financiero": completion_bundle["efecto_financiero"],
            "seguimiento": get_service_reception_tracking(str(record.id), record=record),
        }

    # CA-01: Debe estar en estado 'En proceso'
    if current_state != "EN_PROCESO":
        raise HTTPException(
            409,
            f"Solo puedes confirmar la recepción de un servicio en estado En proceso (estado actual: {getattr(record, 'estado', None)}).",
        )

    # CA-01: Debe contar con finalización reportada por el técnico
    if not tracking.get("finalizacion_reportada") or not tracking.get("finalizacion_reportada_en"):
        raise HTTPException(
            409,
            "No se puede confirmar la recepción porque el técnico aún no ha reportado la finalización del servicio.",
        )

    # CA-01 y CA-04: No debe existir una incidencia abierta que bloquee el cierre
    if tracking.get("incidencia_abierta"):
        raise HTTPException(
            409,
            "No se puede confirmar la recepción mientras exista una incidencia abierta pendiente de resolución administrativa.",
        )

    closed_at = _ensure_utc(now)
    review_deadline = closed_at + timedelta(hours=REVIEW_WINDOW_HOURS)

        # CA-02: Transición transaccional a Completada e inmutabilidad
    transition_request(db, record, "COMPLETADA", role="CLIENTE", uid=uid, now=closed_at)
    if hasattr(record, "pendiente_recepcion"):
        record.pendiente_recepcion = False

    monto_pactado = Decimal(
        str(getattr(record, "monto_total", None) or getattr(record, "presupuesto", None) or getattr(record, "monto_base", 0))
    ).quantize(Decimal("0.01"))

    updated_tracking = {
        **get_service_reception_tracking(str(record.id), record=record),
        **tracking,
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "completada": True,
        "fecha_cierre": closed_at.isoformat(),
        "fecha_cierre_tegucigalpa": _to_tegucigalpa_iso(closed_at),
        "autor_cierre": "CLIENTE",
        "autor_cierre_uid": user.firebase_uid,
        "comentario_confirmacion": payload.comentario if payload else None,
        "acuerdo_inmutable": True,
        "monto_pactado_inmutable": float(monto_pactado),
        "metodo_pago_inmutable": getattr(record, "metodo_pago", None),
        "tecnico_id_inmutable": getattr(record, "tecnico_id", None),
        "resenas_habilitadas": True,
        "plazo_resena_vence_en": review_deadline.isoformat(),
        "plazo_resena_vence_tegucigalpa": _to_tegucigalpa_iso(review_deadline),
        "_closed_at_dt": closed_at,
        "_review_deadline_dt": review_deadline,
    }
    _save_tracking(record, updated_tracking)
    completion_bundle = emit_and_process_idempotent_completion_event(
        record,
        modalidad=modalidad,
        autor_cierre="CLIENTE",
        closed_at=closed_at,
    )
    notify_service_reception_confirmed(db, record, modalidad=modalidad, now=closed_at)

    db.commit()
    return {
        "mensaje": "Recepción confirmada exitosamente. La contratación ha pasado a estado Completada.",
        "reintento_idempotente": False,
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado": "COMPLETADA",
        "fecha_cierre": updated_tracking["fecha_cierre"],
        "fecha_cierre_tegucigalpa": updated_tracking["fecha_cierre_tegucigalpa"],
        "autor_cierre": "CLIENTE",
        "acuerdo_inmutable": True,
        "evento_cierre": completion_bundle["evento_cierre"],
        "efecto_financiero": completion_bundle["efecto_financiero"],
        "seguimiento": get_service_reception_tracking(str(record.id), record=record),
    }


@router.post("/service-requests/{solicitud_id}/report-completion", status_code=200)
def report_service_completion_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ReportCompletionSchema = ReportCompletionSchema(),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return report_service_completion_by_technician(solicitud_id, claims, db, payload=payload)


@router.post("/service-requests/{solicitud_id}/confirm-reception", status_code=200)
@router.post("/users/me/requests/{solicitud_id}/confirm-reception", status_code=200)
def confirm_service_reception_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ConfirmReceptionSchema = ConfirmReceptionSchema(),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return confirm_service_reception_by_client(solicitud_id, claims, db, payload=payload)

# --- HU-35 ST-02: Proceso programado de vencimiento (48h) con reloj comprobable ---


def is_review_window_open(
    solicitud_id: str,
    db: Session,
    *,
    now: datetime | None = None,
) -> bool:
    """
    Condición transversal (Plazos independientes):
    Verifica con reloj controlado si el plazo de 48 horas para publicar reseñas
    (que inicia cuando el servicio llega a Completada) continúa vigente.
    """
    clean_id = (solicitud_id or "").strip()
    _, record = _find_locked_request(db, clean_id)
    tracking = get_service_reception_tracking(clean_id, record=record)
    if not tracking or not tracking.get("completada") or not tracking.get("resenas_habilitadas"):
        return False

    closed_at_dt: datetime | None = tracking.get("_closed_at_dt")
    review_deadline_dt: datetime | None = tracking.get("_review_deadline_dt")
    if closed_at_dt is None or review_deadline_dt is None:
        return False

    eval_now = _ensure_utc(now)
    return closed_at_dt <= eval_now <= review_deadline_dt


def run_scheduled_auto_completion(
    db: Session,
    *,
    now: datetime | None = None,
    proceso_responsable: str = "CIERRE_AUTOMATICO_48H",
) -> dict[str, Any]:
    """
    HU-35 ST-02 (CA-03, CA-07):
    - Si transcurren 48 horas desde el reporte de finalización sin confirmación ni incidencia abierta,
      completa el servicio una sola vez y registra como autor al SISTEMA.
    - Acepta un reloj controlado `now` para probar el vencimiento exacto sin esperar dos días reales.
    """
    eval_now = _ensure_utc(now)
    completed_ids: list[str] = []
    skipped_by_incident_ids: list[str] = []
    already_completed_ids: list[str] = []

    candidates = [
        (str(record.id), get_service_reception_tracking(str(record.id), record=record))
        for model in (DirectRequest, OpenRequest)
        for record in db.scalars(select(model).where(model.finalizacion_reportada_en.is_not(None)))
    ]
    for solicitud_id, snapshot in candidates:
        if not snapshot.get("finalizacion_reportada"):
            continue

        if snapshot.get("completada"):
            already_completed_ids.append(solicitud_id)
            continue

    # Patrón Interceptor (HU-51 CA-03): Comprobar bloqueo tanto en estado local como en tickets de moderación
        from app.incident_management import has_active_incident_ticket

        tiene_ticket_bloqueante = has_active_incident_ticket(solicitud_id)
        if snapshot.get("incidencia_abierta") or tiene_ticket_bloqueante:
            skipped_by_incident_ids.append(solicitud_id)
            continue

        deadline_dt: datetime | None = snapshot.get("_confirmation_deadline_dt")
        if deadline_dt is None or eval_now < deadline_dt:
            continue

        # Bloqueo transaccional por solicitud para evitar carrera con confirmación manual del cliente (CA-07)
        try:
            modalidad, record = _find_locked_request(db, solicitud_id)
        except HTTPException:
            continue

        locked_tracking = get_service_reception_tracking(solicitud_id, record=record)
        current_state = _canonical_state(getattr(record, "estado", None))

        if locked_tracking.get("completada") or current_state == "COMPLETADA":
            already_completed_ids.append(solicitud_id)
            continue

        if locked_tracking.get("incidencia_abierta") or has_active_incident_ticket(solicitud_id):
            skipped_by_incident_ids.append(solicitud_id)
            continue

        locked_deadline = locked_tracking.get("_confirmation_deadline_dt")
        if current_state != "EN_PROCESO" or locked_deadline is None or eval_now < locked_deadline:
            continue

        review_deadline = eval_now + timedelta(hours=REVIEW_WINDOW_HOURS)
        transition_request(db, record, "COMPLETADA", role="SISTEMA", now=eval_now)
        record.pendiente_recepcion = False

        monto_pactado = Decimal(
            str(
                getattr(record, "monto_total", None)
                or getattr(record, "presupuesto", None)
                or getattr(record, "monto_base", 0)
            )
        ).quantize(Decimal("0.01"))

        updated_tracking = {
            **get_service_reception_tracking(solicitud_id, record=record),
            **locked_tracking,
            "solicitud_id": solicitud_id,
            "modalidad": modalidad,
            "completada": True,
            "fecha_cierre": eval_now.isoformat(),
            "fecha_cierre_tegucigalpa": _to_tegucigalpa_iso(eval_now),
            "autor_cierre": "SISTEMA",
            "autor_cierre_uid": "SISTEMA",
            "proceso_responsable": proceso_responsable,
            "acuerdo_inmutable": True,
            "monto_pactado_inmutable": float(monto_pactado),
            "metodo_pago_inmutable": getattr(record, "metodo_pago", None),
            "tecnico_id_inmutable": getattr(record, "tecnico_id", None),
            "resenas_habilitadas": True,
            "plazo_resena_vence_en": review_deadline.isoformat(),
            "plazo_resena_vence_tegucigalpa": _to_tegucigalpa_iso(review_deadline),
            "_closed_at_dt": eval_now,
            "_review_deadline_dt": review_deadline,
        }
        _save_tracking(record, updated_tracking)
        emit_and_process_idempotent_completion_event(
            record,
            modalidad=modalidad,
            autor_cierre="SISTEMA",
            closed_at=eval_now,
        )
        notify_service_auto_completed(db, record, modalidad=modalidad, now=eval_now)
        db.commit()
        completed_ids.append(solicitud_id)

    return {
        "fecha_ejecucion": eval_now.isoformat(),
        "fecha_ejecucion_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "proceso_responsable": proceso_responsable,
        "total_completadas_automaticamente": len(completed_ids),
        "solicitudes_completadas": completed_ids,
        "solicitudes_bloqueadas_por_incidencia": skipped_by_incident_ids,
        "solicitudes_ya_completadas": already_completed_ids,
    }


@router.post("/service-requests/scheduled/auto-complete", status_code=200)
def run_scheduled_auto_completion_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    _require_reception_admin(claims, db)
    return run_scheduled_auto_completion(db)

# --- HU-35 ST-03: Bloqueo por incidencia dentro del plazo de 48h y resolución administrativa ---

VALID_ADMIN_INCIDENT_DECISIONS = {
    "COMPLETAR_SERVICIO",
    "DESBLOQUEAR_CONFIRMACION",
    "CANCELAR_SERVICIO",
}


class OpenServiceIncidentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Motivo detallado de la incidencia reportada por el cliente dentro de las 48h.",
    )
    evidencia_url: str | None = Field(
        default=None,
        max_length=500,
        description="Referencia opcional de evidencia adjunta.",
    )


class AdminResolveIncidentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(
        ...,
        description="Dictamen administrativo: COMPLETAR_SERVICIO, DESBLOQUEAR_CONFIRMACION o CANCELAR_SERVICIO.",
    )
    resolucion: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Justificación de la resolución administrativa.",
    )


def open_service_incident_by_client(
    solicitud_id: str,
    payload: OpenServiceIncidentSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-35 ST-03 (CA-01, CA-04):
    - Si el cliente propietario abre una incidencia dentro del plazo de 48h desde el reporte de finalización,
      se detiene el cierre automático y la liberación de fondos hasta la resolución administrativa.
    """
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    modalidad, record = _find_locked_request(db, solicitud_id)
    if getattr(record, "cliente_id", None) != user.firebase_uid:
        raise HTTPException(403, "Solo el cliente propietario puede reportar una incidencia en este servicio")

    current_state = _canonical_state(getattr(record, "estado", None))
    tracking = get_service_reception_tracking(str(record.id), record=record)

    if tracking.get("completada") or current_state == "COMPLETADA":
        raise HTTPException(409, "No se puede abrir una incidencia porque el servicio ya fue completado y es inmutable")

    if current_state != "EN_PROCESO" or not tracking.get("finalizacion_reportada"):
        raise HTTPException(
            409,
            "Solo puedes abrir una incidencia de recepción cuando el servicio está En proceso con finalización reportada.",
        )

    if tracking.get("incidencia_abierta"):
        raise HTTPException(409, "Ya existe una incidencia abierta para esta contratación")

    eval_now = _ensure_utc(now)
    deadline_dt: datetime | None = tracking.get("_confirmation_deadline_dt")
    if deadline_dt is not None and eval_now > deadline_dt:
        raise HTTPException(
            409,
            "El plazo de 48 horas desde el reporte de finalización para abrir una incidencia ha vencido.",
        )

    motivo_clean = (payload.motivo or "").strip()
    if len(motivo_clean) < 5:
        raise HTTPException(400, "El motivo de la incidencia debe tener al menos 5 caracteres")

    incident_data = {
        "incidencia_id": str(uuid.uuid4()),
        "solicitud_id": str(record.id),
        "cliente_id": user.firebase_uid,
        "tecnico_id": getattr(record, "tecnico_id", None),
        "motivo": motivo_clean,
        "evidencia_url": payload.evidencia_url,
        "estado": "ABIERTA",
        "cierre_automatico_detenido": True,
        "liberacion_fondos_retenida": True,
        "abierta_en": eval_now.isoformat(),
        "abierta_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "resuelta_en": None,
        "resolucion_administrativa": None,
        "decision_administrativa": None,
        "administrador_uid": None,
    }

    updated_tracking = {
        **get_service_reception_tracking(str(record.id), record=record),
        **tracking,
        "incidencia_abierta": True,
        "cierre_automatico_detenido": True,
        "liberacion_fondos_retenida": True,
        "incidencia": incident_data,
    }
    _save_tracking(record, updated_tracking)
    db.commit()

    # Patrón Adapter / Bridge (HU-51 CA-01): Registra el ticket en la bandeja prioritaria de administración
    from app.incident_management import (
        _INCIDENT_TICKETS,
        _TICKETS_BY_SERVICE,
        PrioridadTicketEnum,
        EstadoTicketEnum,
        _persist_ticket_to_db,
    )
    
    ticket_id = incident_data["incidencia_id"]
    if not str(ticket_id).startswith("TICK-"):
        ticket_id = f"TICK-{str(ticket_id)[:8].upper()}"

    ticket_data = {
        "ticket_id": ticket_id,
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado_servicio_al_reportar": current_state,
        "reportante_uid": user.firebase_uid,
        "reportante_nombre": user.nombre_completo or "Cliente",
        "rol_reportante": "CLIENTE",
        "contraparte_uid": getattr(record, "tecnico_id", None),
        "tipo": "INCUMPLIMIENTO_SERVICIO",
        "motivo": motivo_clean,
        "prioridad": PrioridadTicketEnum.ALTA.value,
        "estado": EstadoTicketEnum.ABIERTO.value,
        "es_apelacion_justificada": False,
        "falta_computada": True,
        "bloquea_cierre_automatico": True,
        "bloquea_liberacion_fondos": True,
        "creado_en": eval_now.isoformat(),
        "creado_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        "actualizado_en": eval_now.isoformat(),
        "evidencias_privadas": [payload.evidencia_url] if payload.evidencia_url else [],
        "historial_actuaciones": [
            {
                "actuacion_id": str(uuid.uuid4()),
                "instante": eval_now.isoformat(),
                "instante_tegucigalpa": _to_tegucigalpa_iso(eval_now),
                "autor_uid": user.firebase_uid,
                "autor_rol": "CLIENTE",
                "accion": "CREACION_TICKET",
                "observaciones": motivo_clean,
                "evidencias": [payload.evidencia_url] if payload.evidencia_url else [],
            }
        ],
        "resolucion_final": None,
    }
    notify_incident_opened(
        db,
        solicitud_id=str(record.id),
        modalidad=modalidad,
        ticket_id=ticket_id,
        counterpart_uid=getattr(record, "tecnico_id", None),
        counterpart_role="TECNICO",
        now=eval_now,
    )
    _persist_ticket_to_db(db, ticket_data)
    _INCIDENT_TICKETS[ticket_id] = ticket_data
    if str(record.id) not in _TICKETS_BY_SERVICE:
        _TICKETS_BY_SERVICE[str(record.id)] = []
    if ticket_id not in _TICKETS_BY_SERVICE[str(record.id)]:
        _TICKETS_BY_SERVICE[str(record.id)].append(ticket_id)

    return {
        "mensaje": "Incidencia registrada. Se ha detenido el cierre automático y la liberación de fondos hasta resolución administrativa.",
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado": "EN PROCESO",
        "incidencia": incident_data,
        "seguimiento": get_service_reception_tracking(str(record.id), record=record),
    }


def resolve_service_incident_by_admin(
    solicitud_id: str,
    payload: AdminResolveIncidentSchema,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-35 ST-03 (CA-04 y dependencia HU-51):
    Resolución administrativa de una incidencia que bloqueaba el cierre:
    - COMPLETAR_SERVICIO: cierra en Completada, libera efectos financieros y habilita reseñas.
    - DESBLOQUEAR_CONFIRMACION: levanta el bloqueo para permitir confirmar o cerrar.
    - CANCELAR_SERVICIO: cancela la contratación sin liberar fondos al técnico.
    """
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada")

    admin_user = db.scalar(select(User).where(User.firebase_uid == uid))
    if admin_user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")

    if not any(role.rol == "ADMIN" for role in admin_user.roles):
        raise HTTPException(403, "Solo un administrador puede resolver incidencias de servicio")

    decision_norm = (payload.decision or "").strip().upper()
    if decision_norm not in VALID_ADMIN_INCIDENT_DECISIONS:
        raise HTTPException(
            400,
            f"Decisión inválida. Opciones permitidas: {', '.join(sorted(VALID_ADMIN_INCIDENT_DECISIONS))}",
        )

    modalidad, record = _find_locked_request(db, solicitud_id)
    tracking = get_service_reception_tracking(str(record.id), record=record)

    if not tracking.get("incidencia_abierta") or not tracking.get("incidencia"):
        raise HTTPException(409, "Esta solicitud no tiene una incidencia abierta pendiente de resolución")

    eval_now = _ensure_utc(now)
    incident = dict(tracking["incidencia"])
    incident.update(
        {
            "estado": "RESUELTA",
            "decision_administrativa": decision_norm,
            "resolucion_administrativa": payload.resolucion.strip(),
            "administrador_uid": admin_user.firebase_uid,
            "resuelta_en": eval_now.isoformat(),
            "resuelta_en_tegucigalpa": _to_tegucigalpa_iso(eval_now),
        }
    )
    from app.incident_management import _INCIDENT_TICKETS, _TICKETS_BY_SERVICE, _save_persistent_incidents, _persist_ticket_to_db
    for tid in _TICKETS_BY_SERVICE.get(str(record.id), []):
        if tid in _INCIDENT_TICKETS:
            _INCIDENT_TICKETS[tid]["estado"] = "RESUELTO"
            _INCIDENT_TICKETS[tid]["bloquea_cierre_automatico"] = False
            _INCIDENT_TICKETS[tid]["bloquea_liberacion_fondos"] = False
            _persist_ticket_to_db(db, _INCIDENT_TICKETS[tid])
    _save_persistent_incidents()

    if decision_norm == "COMPLETAR_SERVICIO":
        review_deadline = eval_now + timedelta(hours=REVIEW_WINDOW_HOURS)
        transition_request(db, record, "COMPLETADA", role="ADMIN", uid=uid, now=eval_now)
        if hasattr(record, "pendiente_recepcion"):
            record.pendiente_recepcion = False

        incident["cierre_automatico_detenido"] = False
        incident["liberacion_fondos_retenida"] = False

        monto_pactado = Decimal(
            str(
                getattr(record, "monto_total", None)
                or getattr(record, "presupuesto", None)
                or getattr(record, "monto_base", 0)
            )
        ).quantize(Decimal("0.01"))

        updated_tracking = {
            **get_service_reception_tracking(str(record.id), record=record),
            **tracking,
            "incidencia_abierta": False,
            "cierre_automatico_detenido": False,
            "liberacion_fondos_retenida": False,
            "incidencia": incident,
            "completada": True,
            "fecha_cierre": eval_now.isoformat(),
            "fecha_cierre_tegucigalpa": _to_tegucigalpa_iso(eval_now),
            "autor_cierre": "ADMIN",
            "autor_cierre_uid": admin_user.firebase_uid,
            "acuerdo_inmutable": True,
            "monto_pactado_inmutable": float(monto_pactado),
            "metodo_pago_inmutable": getattr(record, "metodo_pago", None),
            "tecnico_id_inmutable": getattr(record, "tecnico_id", None),
            "resenas_habilitadas": True,
            "plazo_resena_vence_en": review_deadline.isoformat(),
            "plazo_resena_vence_tegucigalpa": _to_tegucigalpa_iso(review_deadline),
            "_closed_at_dt": eval_now,
            "_review_deadline_dt": review_deadline,
        }
        _save_tracking(record, updated_tracking)
        completion_bundle = emit_and_process_idempotent_completion_event(
            record,
            modalidad=modalidad,
            autor_cierre="ADMIN",
            closed_at=eval_now,
        )
        notify_incident_resolved(
            db,
            solicitud_id=str(record.id),
            modalidad=modalidad,
            cliente_uid=getattr(record, "cliente_id", None),
            tecnico_uid=getattr(record, "tecnico_id", None),
            decision=decision_norm,
            resolution_key=str(incident.get("incidencia_id") or decision_norm),
            now=eval_now,
        )
        db.commit()
        return {
            "mensaje": "Incidencia resuelta por administración: el servicio ha pasado a Completada.",
            "solicitud_id": str(record.id),
            "modalidad": modalidad,
            "estado": "COMPLETADA",
            "incidencia": incident,
            "evento_cierre": completion_bundle["evento_cierre"],
            "efecto_financiero": completion_bundle["efecto_financiero"],
            "seguimiento": get_service_reception_tracking(str(record.id), record=record),
        }

    if decision_norm == "CANCELAR_SERVICIO":
        transition_request(db, record, "CANCELADA", role="ADMIN", uid=uid, now=eval_now)
        record.pendiente_recepcion = False

        incident["cierre_automatico_detenido"] = True
        incident["liberacion_fondos_retenida"] = True

        updated_tracking = {
            **get_service_reception_tracking(str(record.id), record=record),
            **tracking,
            "incidencia_abierta": False,
            "cierre_automatico_detenido": True,
            "liberacion_fondos_retenida": True,
            "incidencia": incident,
            "completada": False,
            "acuerdo_inmutable": True,
            "resenas_habilitadas": False,
        }
        _save_tracking(record, updated_tracking)
        notify_incident_resolved(
            db,
            solicitud_id=str(record.id),
            modalidad=modalidad,
            cliente_uid=getattr(record, "cliente_id", None),
            tecnico_uid=getattr(record, "tecnico_id", None),
            decision=decision_norm,
            resolution_key=str(incident.get("incidencia_id") or decision_norm),
            now=eval_now,
        )
        db.commit()
        return {
            "mensaje": "Incidencia resuelta por administración: el servicio fue cancelado sin liberación de fondos.",
            "solicitud_id": str(record.id),
            "modalidad": modalidad,
            "estado": "CANCELADA",
            "incidencia": incident,
            "seguimiento": get_service_reception_tracking(str(record.id), record=record),
        }

    # DESBLOQUEAR_CONFIRMACION
    incident["cierre_automatico_detenido"] = False
    incident["liberacion_fondos_retenida"] = False
    updated_tracking = {
        **get_service_reception_tracking(str(record.id), record=record),
        **tracking,
        "incidencia_abierta": False,
        "cierre_automatico_detenido": False,
        "liberacion_fondos_retenida": False,
        "incidencia": incident,
    }
    _save_tracking(record, updated_tracking)
    notify_incident_resolved(
        db,
        solicitud_id=str(record.id),
        modalidad=modalidad,
        cliente_uid=getattr(record, "cliente_id", None),
        tecnico_uid=getattr(record, "tecnico_id", None),
        decision=decision_norm,
        resolution_key=str(incident.get("incidencia_id") or decision_norm),
        now=eval_now,
    )
    db.commit()
    return {
        "mensaje": "Incidencia desbloqueada por administración. El servicio puede ser confirmado.",
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "estado": "EN PROCESO",
        "incidencia": incident,
        "seguimiento": get_service_reception_tracking(str(record.id), record=record),
    }


@router.post("/service-requests/{solicitud_id}/incidents", status_code=201)
@router.post("/users/me/requests/{solicitud_id}/incidents", status_code=201)
def open_service_incident_endpoint(
    solicitud_id: str,
    payload: OpenServiceIncidentSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return open_service_incident_by_client(solicitud_id, payload, claims, db)


@router.post("/service-requests/{solicitud_id}/incidents/resolve", status_code=200)
def resolve_service_incident_endpoint(
    solicitud_id: str,
    payload: AdminResolveIncidentSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return resolve_service_incident_by_admin(solicitud_id, payload, claims, db)

# --- HU-35 ST-04: Persistir evento único de cierre y consumidores financieros idempotentes ---

DEFAULT_COMMISSION_RATE = Decimal("0.10")  # 10% de comisión de plataforma

class FinancialEffectStrategy(Protocol):
    """
    Patrón Strategy + Principios SOLID (SRP, OCP, LSP, DIP):
    Contrato desacoplado para calcular el efecto financiero del cierre según el medio de pago.
    """

    def matches(self, metodo_pago: str) -> bool: ...

    def compute_effect(
        self,
        monto_comision: Decimal,
        monto_neto_tecnico: Decimal,
    ) -> tuple[str, Decimal, Decimal]: ...


class CashCommissionDebtStrategy:
    """Estrategia para pagos en efectivo (Sprint 2 - CA-05 y CA-06): registra deuda por comisión al técnico."""

    def matches(self, metodo_pago: str) -> bool:
        return metodo_pago == "efectivo"

    def compute_effect(
        self,
        monto_comision: Decimal,
        monto_neto_tecnico: Decimal,
    ) -> tuple[str, Decimal, Decimal]:
        return ("DEUDA_COMISION_EFECTIVO_REGISTRADA", Decimal("0.00"), monto_comision)


class DigitalFundsAccreditationStrategy:
    """Estrategia para pagos digitales/tarjeta (CA-05): acredita fondos digitales netos al técnico."""

    def matches(self, metodo_pago: str) -> bool:
        return metodo_pago != "efectivo"

    def compute_effect(
        self,
        monto_comision: Decimal,
        monto_neto_tecnico: Decimal,
    ) -> tuple[str, Decimal, Decimal]:
        return ("FONDOS_DIGITALES_ACREDITADOS", monto_neto_tecnico, Decimal("0.00"))


_FINANCIAL_STRATEGIES: tuple[FinancialEffectStrategy, ...] = (
    CashCommissionDebtStrategy(),
    DigitalFundsAccreditationStrategy(),
)


def _resolve_financial_strategy(metodo_pago: str) -> FinancialEffectStrategy:
    for strategy in _FINANCIAL_STRATEGIES:
        if strategy.matches(metodo_pago):
            return strategy
    return CashCommissionDebtStrategy()


def emit_and_process_idempotent_completion_event(
    record: Any,
    *,
    modalidad: str,
    autor_cierre: str,
    closed_at: datetime | None = None,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
) -> dict[str, Any]:
    """
    HU-35 ST-04 (CA-05, CA-06, CA-07):
    - Emite y persiste un ÚNICO evento `SERVICIO_COMPLETADO` por solicitud_id.
    - Delega el cálculo financiero al patrón Strategy (`FinancialEffectStrategy`) de forma idempotente.
    - Si ocurre una confirmación simultánea con el cierre automático o un reintento, reutiliza el
      evento y efecto financiero existentes sin generar doble comisión, doble crédito ni doble evento.
    """
    solicitud_id = str(getattr(record, "id", "")).strip()
    eval_closed_at = _ensure_utc(closed_at)

    tracking = get_service_reception_tracking(solicitud_id, record=record)
    existing_event = tracking.get("evento_cierre")
    existing_effect = tracking.get("efecto_financiero")

    if existing_event is not None and existing_effect is not None:
        reused_event = {
            **existing_event,
            "reutilizado_idempotente": True,
        }
        reused_effect = {
            **existing_effect,
            "reutilizado_idempotente": True,
        }
        return {
            "evento_cierre": reused_event,
            "efecto_financiero": reused_effect,
            "reutilizado_idempotente": True,
        }

    from app.financial_settlement import calculate_payment_split

    split = calculate_payment_split(record, commission_rate=commission_rate)
    monto_bruto = split.monto_total
    monto_comision = split.monto_comision
    monto_neto_tecnico = split.monto_neto_tecnico
    raw_metodo = split.metodo_pago_base
    if split.estrategia == "MIXTO_TARJETA_EFECTIVO":
        tipo_efecto = "REPARTO_MIXTO_TARJETA_EFECTIVO"
        credito_digital_tecnico = split.credito_digital_tecnico
        deuda_comision_efectivo = split.deuda_comision_efectivo
    else:
        strategy = _resolve_financial_strategy(raw_metodo)
        tipo_efecto, credito_digital_tecnico, deuda_comision_efectivo = strategy.compute_effect(
            monto_comision,
            monto_neto_tecnico,
        )

    event_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"teknigo-cierre-{solicitud_id}"))
    idempotency_key = f"CIERRE-SERVICIO-{solicitud_id}"

    event_payload = {
        "evento_id": event_id,
        "clave_idempotencia": idempotency_key,
        "tipo_evento": "SERVICIO_COMPLETADO",
        "solicitud_id": solicitud_id,
        "modalidad": modalidad,
        "cliente_id": getattr(record, "cliente_id", None),
        "tecnico_id": getattr(record, "tecnico_id", None),
        "autor_cierre": autor_cierre,
        "metodo_pago": raw_metodo,
        "monto_bruto": float(monto_bruto),
        "tasa_comision": float(commission_rate),
        "monto_comision": float(monto_comision),
        "monto_neto_tecnico": float(monto_neto_tecnico),
        "resenas_habilitadas": True,
        "sprint_origen": "SPRINT_2",
        "emitido_en": eval_closed_at.isoformat(),
        "emitido_en_tegucigalpa": _to_tegucigalpa_iso(eval_closed_at),
        "veces_emitido": 1,
        "reutilizado_idempotente": False,
    }

    financial_effect = {
        "efecto_id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"teknigo-finanzas-{solicitud_id}")),
        "evento_id": event_id,
        "clave_idempotencia": idempotency_key,
        "solicitud_id": solicitud_id,
        "tecnico_id": getattr(record, "tecnico_id", None),
        "metodo_pago": raw_metodo,
        "tipo_efecto": tipo_efecto,
        "monto_bruto": float(monto_bruto),
        "monto_comision": float(monto_comision),
        "monto_neto_tecnico": float(monto_neto_tecnico),
        "credito_digital_acreditado": float(credito_digital_tecnico),
        "deuda_comision_efectivo": float(deuda_comision_efectivo),
        "estado_liquidacion": "REGISTRADO_SPRINT_2",
        "validado_sprint_3": False,
        "validado_sprint_3_en": None,
        "aplicaciones_financieras": 1,
        "reutilizado_idempotente": False,
        "reparto_medios": split.to_dict(),
    }

    _save_tracking(record, {**tracking, "evento_cierre": event_payload, "efecto_financiero": financial_effect})

    return {
        "evento_cierre": event_payload,
        "efecto_financiero": financial_effect,
        "reutilizado_idempotente": False,
    }


def validate_sprint3_financial_settlement(
    solicitud_id: str,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    HU-35 ST-04 (CA-06):
    En el Sprint 2 el cierre funciona con efectivo y eventos persistidos;
    en el Sprint 3 se valida el efecto financiero completo sin duplicar cierres anteriores.
    """
    clean_id = (solicitud_id or "").strip()
    _, record = _find_locked_request(db, clean_id)
    tracking = get_service_reception_tracking(clean_id, record=record)
    event_payload = tracking.get("evento_cierre")
    financial_effect = (tracking.get("efecto_financiero") or {}).copy()

    if event_payload is None or not financial_effect:
        raise HTTPException(
            404,
            "No existe un evento de cierre persistido para validar la liquidación financiera de esta solicitud.",
        )

    eval_now = _ensure_utc(now)
    if not financial_effect.get("validado_sprint_3"):
        financial_effect["validado_sprint_3"] = True
        financial_effect["estado_liquidacion"] = "CONSOLIDADO_SPRINT_3"
        financial_effect["validado_sprint_3_en"] = eval_now.isoformat()
        financial_effect["validado_sprint_3_en_tegucigalpa"] = _to_tegucigalpa_iso(eval_now)
        # Garantiza que aplicaciones_financieras permanezca en 1 (sin duplicar cierres anteriores)
        financial_effect["aplicaciones_financieras"] = 1

    _save_tracking(record, {**tracking, "efecto_financiero": financial_effect})
    db.commit()

    return {
        "mensaje": "Efecto financiero consolidado en Sprint 3 sin duplicar el cierre ni los cargos previos.",
        "solicitud_id": clean_id,
        "duplicado_generado": False,
        "evento_cierre": dict(event_payload),
        "efecto_financiero": dict(financial_effect),
    }


@router.get("/service-requests/{solicitud_id}/completion-event", status_code=200)
def get_completion_event_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    clean_id = (solicitud_id or "").strip()
    _, record = _find_locked_request(db, clean_id)
    user = db.scalar(select(User).where(User.firebase_uid == claims.get("uid")))
    if user is None or (
        user.firebase_uid not in (record.cliente_id, record.tecnico_id)
        and str(user.id) != record.tecnico_id
        and not any(role.rol == "ADMIN" for role in user.roles)
    ):
        raise HTTPException(403, "No tienes acceso a esta contratación")
    tracking = get_service_reception_tracking(clean_id, record=record)
    event_payload = tracking.get("evento_cierre")
    financial_effect = (tracking.get("efecto_financiero") or {}).copy()
    if event_payload is None:
        raise HTTPException(404, "Aún no se ha emitido un evento de cierre para esta contratación")
    return {
        "solicitud_id": clean_id,
        "evento_cierre": event_payload,
        "efecto_financiero": financial_effect,
    }


@router.post("/service-requests/{solicitud_id}/financial-settlement/validate-sprint3", status_code=200)
def validate_sprint3_financial_settlement_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    _require_reception_admin(claims, db)
    return validate_sprint3_financial_settlement(solicitud_id, db)