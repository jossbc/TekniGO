from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.direct_requests_repository import DirectRequestRepository
from app.direct_requests_schemas import EstimateRequest, EstimateResponse
from app.models import User
from app.direct_requests_schemas import CreateDirectRequest
from datetime import datetime, timezone

class DirectRequestService:
    @staticmethod
    def calculate_estimate(db: Session, request: EstimateRequest) -> EstimateResponse:
        servicio = DirectRequestRepository.get_service_by_id(db, request.servicio_id)
        
        if not servicio:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="El servicio especificado no existe."
            )
        
        if not servicio.activo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="No se puede calcular un presupuesto para un servicio inactivo."
            )
        
        # Multiplicación exacta redondeada a 2 decimales
        total_estimado = (servicio.tarifa_base * request.cantidad).quantize(Decimal('0.01'))
        
        return EstimateResponse(
            tarifa_base=servicio.tarifa_base,
            cantidad=request.cantidad,
            unidad_cobro=servicio.unidad_cobro,
            total=total_estimado
        )
    
    @staticmethod
    def create_direct_request(db: Session, request: CreateDirectRequest, current_user: dict):
        # 1. Validar que la fecha sea futura (CA-02)
        if request.fecha_hora <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La fecha y hora deben ser en el futuro."
            )

        # 2. Buscar el servicio y validar (reutilizamos la lógica)
        servicio = DirectRequestRepository.get_service_by_id(db, request.servicio_id)
        if not servicio or not servicio.activo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Servicio inválido o inactivo."
            )

        # 3. Validar técnico destino (Aquí idealmente checamos que esté activo/verificado)
        tecnico = db.scalar(select(User).where(User.firebase_uid == request.tecnico_id))
        if not tecnico:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El técnico seleccionado no existe."
            )

        # 4. Calcular el presupuesto exacto desde el backend (no confiamos en el front) (CA-04)
        total_calculado = (servicio.tarifa_base * request.cantidad).quantize(Decimal('0.01'))

        # 5. Armar el diccionario para la base de datos
        request_dict = request.model_dump()
        request_dict.update({
            "cliente_id": current_user["uid"],
            "tarifa_aplicada": servicio.tarifa_base,
            "unidad_cobro": servicio.unidad_cobro,
            "monto_base": total_calculado,
            "estado": "Pendiente"
        })

        return DirectRequestRepository.create_request(db, request_dict)