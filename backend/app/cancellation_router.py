"""HU-36 ST-01: endpoint de cancelación."""
from typing import Annotated
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session
from app.actors import require_user
from app.adjustment_repository import SqlAlchemyAdjustmentRepository
from app.cancellation_evidence import EvidenceStore, get_evidence_store
from app.cancellation_policy import CancellationCause, CancellationPolicy, default_cancellation_policy
from app.cancellation_repository import CancellationRepository, SqlAlchemyCancellationRepository
from app.cancellation_service import CancellationObserver, cancel_request, RejectedAdjustmentLookup, _resolve_actor_role
from app.client_requests import _find_request_by_id
from app.database import get_db
from app.financial_settlement import (
    RefundOnCancellationObserver,
    confirm_gateway_refund,
    get_refund_for_request,
)
from app.reopen_service import reopen_cancelled_request
from app.reopen_sources import ReopenSource, default_reopen_sources
from app.security import get_current_user

router = APIRouter(tags=["Cancelación (HU-36)"])


class CancelRequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    causa: CancellationCause = CancellationCause.VOLUNTARIA
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1500)]
    evidencias: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=10,
    )


def get_cancellation_policy() -> CancellationPolicy:
    return default_cancellation_policy()


def get_cancellation_repository() -> CancellationRepository:
    return SqlAlchemyCancellationRepository()


def get_cancellation_observers() -> list[CancellationObserver]:
    return [RefundOnCancellationObserver()] #ST-05 OBSERVADOR DE REEMBOLSO

def get_rejected_adjustment_lookup() -> RejectedAdjustmentLookup:
    return SqlAlchemyAdjustmentRepository()


@router.post("/service-requests/{solicitud_id}/cancel", status_code=200)
def cancel_request_endpoint(
    solicitud_id: str,
    payload: CancelRequestSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    policy: CancellationPolicy = Depends(get_cancellation_policy),
    repository: CancellationRepository = Depends(get_cancellation_repository),
    observers: list[CancellationObserver] = Depends(get_cancellation_observers),
    adjustments: RejectedAdjustmentLookup = Depends(get_rejected_adjustment_lookup),
    evidence: EvidenceStore = Depends(get_evidence_store),
):
    response.headers["Cache-Control"] = "no-store, private"
    return cancel_request(
        db, solicitud_id, causa=payload.causa, motivo=payload.motivo,
        evidencias=payload.evidencias, claims=claims,
        policy=policy, repository=repository, observers=observers,
        adjustments=adjustments,
        evidence=evidence,
    )

class ReopenRequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha_hora: datetime
    metodo_pago: str | None = None
    presupuesto: Decimal | None = None


def get_reopen_sources() -> list[ReopenSource]:
    return default_reopen_sources()


@router.post("/service-requests/{solicitud_id}/reopen", status_code=201)
def reopen_request_endpoint(
    solicitud_id: str,
    payload: ReopenRequestSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    repository: CancellationRepository = Depends(get_cancellation_repository),
    sources: list[ReopenSource] = Depends(get_reopen_sources),
):
    response.headers["Cache-Control"] = "no-store, private"
    data = reopen_cancelled_request(
        db, solicitud_id, fecha_hora=payload.fecha_hora, metodo_pago=payload.metodo_pago,
        presupuesto=payload.presupuesto, claims=claims, repository=repository, sources=sources,
    )
    if data.get("reabierta_existente"):
        response.status_code = 200
    return data

class ConfirmRefundSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referencia_confirmacion: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None


@router.post("/service-requests/{solicitud_id}/refund/confirm", status_code=200)
def confirm_refund_endpoint(
    solicitud_id: str,
    response: Response,
    payload: ConfirmRefundSchema = ConfirmRefundSchema(),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    user = require_user(db, claims)
    modalidad, record = _find_request_by_id(db, solicitud_id)
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(404, "No se encontró la solicitud")
    if not any(r.rol == "ADMIN" for r in user.roles):
        _resolve_actor_role(record, user)
    return confirm_gateway_refund(
        db, modalidad, str(record.id),
        referencia_confirmacion=payload.referencia_confirmacion,
    )