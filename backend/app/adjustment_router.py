"""HU-36 ST-03: endpoints de ajuste único."""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.adjustment_policy import AdjustmentPolicy, default_adjustment_policy
from app.adjustment_repository import AdjustmentRepository, SqlAlchemyAdjustmentRepository
from app.adjustment_service import (
    AdjustmentDecision, AdjustmentObserver, get_adjustment, request_adjustment, respond_adjustment,
)
from app.database import get_db
from app.security import get_current_user

router = APIRouter(tags=["Ajuste de presupuesto (HU-36)"])

Money = Annotated[Decimal, Field(gt=Decimal("0"), le=Decimal("999999.99"), allow_inf_nan=False)]


class RequestAdjustmentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1500)]
    monto_base: Money
    nuevo_total: Money


class AdjustmentResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: AdjustmentDecision
    comentario: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    metodo_pago_diferencia: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    referencia_pasarela_diferencia: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None


def get_adjustment_policy() -> AdjustmentPolicy:
    return default_adjustment_policy()


def get_adjustment_repository() -> AdjustmentRepository:
    return SqlAlchemyAdjustmentRepository()


def get_adjustment_observers() -> list[AdjustmentObserver]:
    return []  # ST-05 agrega aquí el observador de cobro complementario


@router.post("/service-requests/{solicitud_id}/adjustment", status_code=201)
def request_adjustment_endpoint(
    solicitud_id: str,
    payload: RequestAdjustmentSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    policy: AdjustmentPolicy = Depends(get_adjustment_policy),
    repository: AdjustmentRepository = Depends(get_adjustment_repository),
):
    response.headers["Cache-Control"] = "no-store, private"
    return request_adjustment(
        db, solicitud_id, motivo=payload.motivo, monto_base=payload.monto_base,
        nuevo_total=payload.nuevo_total, claims=claims, policy=policy, repository=repository,
    )


@router.post("/service-requests/{solicitud_id}/adjustment/response", status_code=200)
def respond_adjustment_endpoint(
    solicitud_id: str,
    payload: AdjustmentResponseSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    repository: AdjustmentRepository = Depends(get_adjustment_repository),
    observers: list[AdjustmentObserver] = Depends(get_adjustment_observers),
):
    response.headers["Cache-Control"] = "no-store, private"
    return respond_adjustment(
        db, solicitud_id, decision=payload.decision, comentario=payload.comentario,
        metodo_pago_diferencia=payload.metodo_pago_diferencia,
        referencia_pasarela_diferencia=payload.referencia_pasarela_diferencia,
        claims=claims, repository=repository, observers=observers,
    )


@router.get("/service-requests/{solicitud_id}/adjustment", status_code=200)
def get_adjustment_endpoint(
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    repository: AdjustmentRepository = Depends(get_adjustment_repository),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_adjustment(db, solicitud_id, claims=claims, repository=repository)