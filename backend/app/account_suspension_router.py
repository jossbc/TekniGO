"""HU-52 ST-02: Endpoints administrativos de moderación/suspensiones y consulta propia de restricciones."""
from __future__ import annotations

from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.account_closure_service import AccountClosureService
from app.account_suspension_domain import (
    SuspensionConflictError,
    SuspensionDomainError,
    SuspensionForbiddenError,
    SuspensionNotFoundError,
    SuspensionValidationError,
)
from app.account_suspension_service import AccountSuspensionService
from app.database import get_db
from app.models import ACCOUNT_DELETED, User
from app.security import get_current_user

router = APIRouter(tags=["Moderación y Suspensiones"])
NO_STORE = {"Cache-Control": "no-store, private"}


class ApplySuspensionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: str = Field(..., description="TEMPORAL o DEFINITIVA")
    causa: str = Field(..., description="CONDUCTA, REINCIDENCIA_FALTAS, INCUMPLIMIENTO_REGLAS, FRAUDE_IDENTIDAD o INCIDENCIA_GRAVE")
    motivo: str = Field(..., min_length=5, max_length=1500)
    vence_en: datetime | None = None
    alerta_reincidencia_id: str | None = Field(default=None, max_length=36)

class ResolveRecidivismAlertSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observaciones: str = Field(..., min_length=5, max_length=1500)

class LiftSuspensionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo_levantamiento: str = Field(..., min_length=5, max_length=1500)

class CloseAccountSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(..., min_length=5, max_length=1500)

def _require_registered_user(db: Session, claims: dict) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada.")
    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO.")
    if user.estado_identidad == ACCOUNT_DELETED:
        raise HTTPException(410, "Esta cuenta de TekniGO fue cerrada.")
    return user


def _raise_http(exc: SuspensionDomainError) -> None:
    if isinstance(exc, SuspensionValidationError):
        raise HTTPException(400, str(exc)) from None
    if isinstance(exc, SuspensionForbiddenError):
        raise HTTPException(403, str(exc)) from None
    if isinstance(exc, SuspensionNotFoundError):
        raise HTTPException(404, str(exc)) from None
    if isinstance(exc, SuspensionConflictError):
        raise HTTPException(409, str(exc)) from None
    raise HTTPException(500, "No pudimos procesar la operación de moderación.") from None


@router.get("/users/me/account-restrictions")
def get_my_account_restrictions_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _require_registered_user(db, claims)
    return AccountSuspensionService(db).get_my_account_restrictions(user)


@router.get("/admin/accounts/moderation")
def list_accounts_moderation_endpoint(
    response: Response,
    search: str = Query(default="", max_length=120),
    filtro: str = Query(default="TODOS", max_length=40),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).list_accounts_for_admin(
            admin,
            search=search,
            filtro=filtro,
            offset=offset,
            limit=limit,
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)


@router.get("/admin/accounts/{user_id}/moderation")
def get_account_moderation_detail_endpoint(
    user_id: uuid.UUID,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).get_account_moderation_detail(admin, user_id)
    except SuspensionDomainError as exc:
        _raise_http(exc)


@router.post("/admin/accounts/{user_id}/suspensions", status_code=201)
def apply_account_suspension_endpoint(
    user_id: uuid.UUID,
    payload: ApplySuspensionSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).apply_suspension(
            admin,
            user_id,
            tipo=payload.tipo,
            causa=payload.causa,
            motivo=payload.motivo,
            vence_en=payload.vence_en,
            alerta_reincidencia_id=payload.alerta_reincidencia_id,
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)


@router.post("/admin/recidivism-alerts/{alert_id}/resolve")
def resolve_recidivism_alert_endpoint(
    alert_id: str,
    payload: ResolveRecidivismAlertSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).resolve_recidivism_alert_without_suspension(
            admin,
            alert_id,
            observaciones=payload.observaciones,
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)

@router.post("/admin/suspensions/{suspension_id}/lift")
def lift_temporal_suspension_endpoint(
    suspension_id: uuid.UUID,
    payload: LiftSuspensionSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).lift_temporal_suspension(
            admin,
            suspension_id,
            motivo_levantamiento=payload.motivo_levantamiento,
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)


@router.post("/admin/suspensions/expire-overdue")
def expire_overdue_suspensions_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountSuspensionService(db).expire_overdue_suspensions(
            admin,
            proceso_responsable="EXPIRACION_MANUAL_ADMIN",
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)

@router.post("/admin/accounts/{user_id}/close")
def close_account_admin_endpoint(
    user_id: uuid.UUID,
    payload: CloseAccountSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    admin = _require_registered_user(db, claims)
    try:
        return AccountClosureService(db).close_account_by_admin(
            admin,
            user_id,
            motivo=payload.motivo,
        )
    except SuspensionDomainError as exc:
        _raise_http(exc)