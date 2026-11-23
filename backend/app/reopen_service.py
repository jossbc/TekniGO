"""HU-36 ST-02: reapertura como nueva licitación vinculada a la cancelada."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.cancellation_repository import CancellationRepository
from app.models import OpenRequest, ensure_utc_datetime
from app.open_requests import (
    CreateOpenRequestSchema,
    require_verified_unsuspended_client,
    serialize_open_request_for_owner,
    validate_open_request_data,
)
from app.reopen_sources import ReopenSource
from app.request_states import record_creation, state_key
from app.service_reception import _find_locked_request


def reopen_cancelled_request(
    db: Session,
    solicitud_id: str,
    *,
    fecha_hora: datetime,
    metodo_pago: str | None,
    presupuesto: Decimal | None,
    claims: dict,
    repository: CancellationRepository,
    sources: Sequence[ReopenSource],
    now: datetime | None = None,
) -> dict:
    client = require_verified_unsuspended_client(claims, db)
    modalidad, record = _find_locked_request(db, solicitud_id)  # FOR UPDATE

    if record.cliente_id != client.firebase_uid:
        raise HTTPException(403, "Solo el cliente propietario puede reabrir la solicitud.")

    cancellation = repository.get(db, modalidad, str(record.id))
    if cancellation is None or state_key(record.estado) != "CANCELADA":
        raise HTTPException(409, "Solo se puede reabrir una solicitud cancelada.")
    if not cancellation.permite_reapertura:
        raise HTTPException(409, "Esta cancelación no habilita la reapertura.")

    if cancellation.solicitud_reabierta_id:  # reintento idempotente
        existing = db.get(OpenRequest, cancellation.solicitud_reabierta_id)
        data = serialize_open_request_for_owner(existing)
        data.update(solicitud_origen_id=str(record.id), reabierta_existente=True)
        return data

    source = next((item for item in sources if item.supports(record)), None)
    if source is None:
        raise HTTPException(409, "La modalidad de la solicitud no admite reapertura.")
    draft = source.to_draft(record)

    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    specialty, validated = validate_open_request_data(
        CreateOpenRequestSchema(
            especialidad_id=draft.especialidad_id,
            descripcion=draft.descripcion,
            municipio=draft.municipio,
            sector=draft.sector,
            direccion_exacta=draft.direccion_exacta,
            fecha_hora=fecha_hora,
            metodo_pago=metodo_pago or draft.metodo_pago,
            presupuesto=presupuesto if presupuesto is not None else draft.presupuesto,
        ),
        db,
        now=moment,
    )

    reopened = OpenRequest(
        id=str(uuid.uuid4()),
        cliente_id=client.firebase_uid,
        tecnico_id=None,
        modalidad="ABIERTA",
        estado="Publicada",
        creada_en=moment,
        solicitud_origen_id=str(record.id),
        solicitud_origen_modalidad=modalidad,
        **validated,
    )
    db.add(reopened)
    record_creation(db, reopened)  # hace flush
    cancellation.solicitud_reabierta_id = reopened.id
    cancellation.reabierta_en = moment
    db.commit()
    db.refresh(reopened)

    data = serialize_open_request_for_owner(reopened, specialty)
    data.update(
        solicitud_origen_id=str(record.id),
        reabierta_existente=False,
        mensaje="Licitación reabierta; la solicitud original permanece cancelada.",
    )
    return data