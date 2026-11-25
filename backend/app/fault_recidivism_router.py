"""HU-36 ST-06: endpoints de cómputo de faltas y alertas administrativas de tercera reincidencia."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.actors import require_admin, require_user
from app.database import get_db
from app.fault_recidivism import (
    FaultComputationService,
    list_recidivism_alerts_for_admin,
)
from app.security import get_current_user

router = APIRouter(tags=["Faltas y Reincidencia (HU-36)"])
NO_STORE = "no-store, private"


def get_fault_service() -> FaultComputationService:
    return FaultComputationService()


@router.get("/users/me/faults")
def get_my_faults_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: FaultComputationService = Depends(get_fault_service),
):
    response.headers["Cache-Control"] = NO_STORE
    user = require_user(db, claims)
    return service.compute_summary(db, user.firebase_uid)


@router.get("/admin/users/{user_uid}/faults")
def get_user_faults_for_admin_endpoint(
    user_uid: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: FaultComputationService = Depends(get_fault_service),
):
    response.headers["Cache-Control"] = NO_STORE
    require_admin(db, claims)
    return service.compute_summary(db, user_uid)


@router.get("/admin/recidivism-alerts")
def list_recidivism_alerts_endpoint(
    response: Response,
    estado: str = "TODOS",
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = NO_STORE
    require_admin(db, claims)
    return list_recidivism_alerts_for_admin(db, estado=estado)