"""Lifecycle and audit trail shared by direct requests and bids (HU-32)."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DirectRequest, OpenRequest, RequestStateEvent, User

Request = DirectRequest | OpenRequest
INITIAL = {"DIRECTA": "PENDIENTE", "ABIERTA": "PUBLICADA"}
CONTACT_STATES = {"ACEPTADA", "EN PROCESO", "COMPLETADA"}
FLOW = {
    "DIRECTA": {"PENDIENTE": ("ACEPTADA", "TECNICO")},
    "ABIERTA": {
        "PUBLICADA": ("CON OFERTAS", "TECNICO"),
        "CON OFERTAS": ("ACEPTADA", "CLIENTE"),
    },
}
SERVICE_FLOW = {
    "ACEPTADA": ("EN PROCESO", "TECNICO"),
    "EN PROCESO": ("COMPLETADA", "CLIENTE"),
}
DESCRIPTIONS = {
    "PENDIENTE": "Solicitud registrada y pendiente de respuesta.",
    "PUBLICADA": "Solicitud abierta publicada.",
    "CON OFERTAS": "La licitación recibió una propuesta.",
    "ACEPTADA": "Contratación confirmada.",
    "EN PROCESO": "El técnico inició el servicio.",
    "COMPLETADA": "Servicio completado.",
    "CANCELADA": "Solicitud cancelada.",
    "RECHAZADA": "Solicitud rechazada por el técnico.",
}


def state_key(value: str) -> str:
    return value.strip().upper().replace("_", " ")


def modality(record: Request) -> str:
    return "ABIERTA" if isinstance(record, OpenRequest) else "DIRECTA"


def client_actions(record: Request) -> list[str]:
    current = state_key(record.estado)
    return ["CANCELAR"] if current in FLOW[modality(record)] or current in SERVICE_FLOW else []


def _event_query(record: Request):
    return select(RequestStateEvent).where(
        RequestStateEvent.modalidad == modality(record),
        RequestStateEvent.solicitud_id == str(record.id),
    )


def _event(
    record: Request, state: str, date: datetime | None,
    role: str | None = None, uid: str | None = None,
) -> RequestStateEvent:
    return RequestStateEvent(
        modalidad=modality(record), solicitud_id=str(record.id), estado=state,
        fecha=date, responsable=role, responsable_id=uid,
    )


def _initial_event(record: Request) -> RequestStateEvent:
    return _event(record, INITIAL[modality(record)], record.creada_en, "CLIENTE", record.cliente_id)


def record_creation(db: Session, record: Request) -> None:
    db.flush()
    db.add(_initial_event(record))


def _previous_events(record: Request) -> list[RequestStateEvent]:
    initial = _initial_event(record)
    current = state_key(record.estado)
    if current == initial.estado:
        return [initial]
    return [initial, _event(record, current, None)]


def transition_request(
    db: Session, record: Request, target: str, *, role: str, uid: str | None = None,
    now: datetime | None = None,
) -> None:
    """Validate a transition on a locked row and write its event in the same transaction."""
    mode = modality(record)
    current = state_key(record.estado)
    target = state_key(target)
    if role in {"CLIENTE", "TECNICO"}:
        actor = db.scalar(select(User).where(User.firebase_uid == uid))
        if actor is None or not any(entry.rol == role for entry in actor.roles):
            raise HTTPException(403, "No tienes el rol requerido para modificar esta solicitud")
    if role == "CLIENTE" and record.cliente_id != uid:
        raise HTTPException(403, "No tienes permiso para modificar esta solicitud")
    if role == "TECNICO":
        participates = record.tecnico_id in (uid, str(actor.id))
        first_offer = mode == "ABIERTA" and current == "PUBLICADA" and target == "CON OFERTAS"
        if not participates and not first_offer:
            raise HTTPException(403, "No tienes permiso para modificar esta solicitud")

    allowed = FLOW[mode].get(current) or SERVICE_FLOW.get(current)
    valid = allowed == (target, role) or (
        (mode, current, target, role) == ("ABIERTA", "CON OFERTAS", "PUBLICADA", "CLIENTE")
    )
    if target == "CANCELADA" and allowed is not None:
        technician_may_cancel = (
            (mode == "DIRECTA" and current == "PENDIENTE")
            or current in {"ACEPTADA", "EN PROCESO"}  # HU-36 CA-01/CA-03
        )
        valid = role == "CLIENTE" or (role == "TECNICO" and technician_may_cancel)
    if (current, target, role) == ("EN PROCESO", "COMPLETADA", "SISTEMA"):
        valid = True
    if current == "EN PROCESO" and target in {"COMPLETADA", "CANCELADA"} and role == "ADMIN":
        valid = True
    if not valid:
        raise HTTPException(409, f"No se permite pasar de {current} a {target}.")

    last_event = db.scalar(_event_query(record).order_by(RequestStateEvent.id.desc()).limit(1))
    if last_event is None:
        db.add_all(_previous_events(record))
    elif last_event.estado != current:
        db.add(_event(record, current, None))

    record.estado = {"CON OFERTAS": "Con ofertas", "PUBLICADA": "Publicada"}.get(target, target)
    db.add(_event(record, target, now or datetime.now(timezone.utc), role, uid))


def start_request_service(db: Session, record: Request, uid: str) -> None:
    """Start an authorized request whose row is locked by the caller."""
    current = state_key(record.estado)
    if current == "EN PROCESO":
        started = db.scalar(
            _event_query(record).where(
                RequestStateEvent.estado == "EN PROCESO",
                RequestStateEvent.fecha.is_not(None),
            ).limit(1)
        )
        if started is not None:
            return
    if current != "ACEPTADA":
        raise HTTPException(409, "Solo se puede iniciar una solicitud aceptada.")
    if state_key(record.metodo_pago or "") == "TARJETA" and not record.referencia_pasarela:
        raise HTTPException(409, "El cobro con tarjeta debe estar confirmado antes de iniciar el servicio.")
    transition_request(db, record, "EN PROCESO", role="TECNICO", uid=uid)
    db.commit()


def request_timeline(db: Session, record: Request) -> list[dict]:
    events = list(db.scalars(_event_query(record).order_by(RequestStateEvent.id)).all())
    current = state_key(record.estado)
    if not events:
        events = _previous_events(record)
    elif events[-1].estado != current:
        events.append(_event(record, current, None))

    items = []
    for event in events:
        date = event.fecha
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        items.append({
            "estado": event.estado,
            "descripcion": DESCRIPTIONS.get(event.estado, "Cambio de estado registrado."),
            "fecha": date.isoformat() if date is not None else None,
            "responsable": event.responsable,
        })
    return items
