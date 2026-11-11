from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.direct_requests_schemas import CreateDirectRequest, EstimateRequest, EstimateResponse
from app.direct_requests_service import DirectRequestService
from app.security import get_current_user

router = APIRouter(prefix="/direct-requests", tags=["Solicitudes Directas"])


@router.post("/estimate", response_model=EstimateResponse)
def get_budget_estimate(
    request: EstimateRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """HU-21: Calcular presupuesto estimado para un servicio"""
    response.headers["Cache-Control"] = "no-store, private"
    return DirectRequestService.calculate_estimate(db, request)


@router.post("/")
@router.post("", include_in_schema=False)
def create_request(
    request: CreateDirectRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """HU-20: Crear una solicitud directa a un técnico"""
    response.headers["Cache-Control"] = "no-store, private"
    nueva_solicitud = DirectRequestService.create_direct_request(db, request, current_user)
    return {
        "message": "Solicitud creada exitosamente",
        "id": str(nueva_solicitud.id),
        "estado": nueva_solicitud.estado,
    }