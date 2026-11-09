from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.security import get_current_user
from app.direct_requests_schemas import EstimateRequest, EstimateResponse
from app.direct_requests_service import DirectRequestService
from app.direct_requests_schemas import CreateDirectRequest

router = APIRouter(prefix="/direct-requests", tags=["Solicitudes Directas"])

@router.post("/estimate", response_model=EstimateResponse)
def get_budget_estimate(
    request: EstimateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    
    """HU-21: Calcular presupuesto estimado para un servicio"""
    return DirectRequestService.calculate_estimate(db, request)

@router.post("/")
def create_request(
    request: CreateDirectRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """HU-20: Crear una solicitud directa a un técnico"""
    nueva_solicitud = DirectRequestService.create_direct_request(db, request, current_user)
    return {"message": "Solicitud creada exitosamente", "id": nueva_solicitud.id}