from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.direct_requests_repository import DirectRequestRepository
from app.direct_requests_schemas import CreateDirectRequest, EstimateRequest, EstimateResponse
from app.models import CoverageZone, User
from app.technician_activation import require_operational_technician

MAX_ESTIMATE_TOTAL = Decimal("99999999.99")


class DirectRequestService:
    @staticmethod
    def _compute_total(tarifa_base: Decimal | float, cantidad: Decimal) -> Decimal:
        total = (Decimal(str(tarifa_base)) * cantidad).quantize(Decimal("0.01"))
        if total > MAX_ESTIMATE_TOTAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El total estimado excede el monto máximo permitido.",
            )
        return total

    @staticmethod
    def calculate_estimate(db: Session, request: EstimateRequest) -> EstimateResponse:
        servicio = DirectRequestRepository.get_service_by_id(db, request.servicio_id)

        if not servicio:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El servicio especificado no existe.",
            )

        if not servicio.activo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede calcular un presupuesto para un servicio inactivo.",
            )

        total_estimado = DirectRequestService._compute_total(servicio.tarifa_base, request.cantidad)

        return EstimateResponse(
            tarifa_base=servicio.tarifa_base,
            cantidad=request.cantidad,
            unidad_cobro=servicio.unidad_cobro,
            total=total_estimado,
        )

    @staticmethod
    def create_direct_request(db: Session, request: CreateDirectRequest, current_user: dict):
        client_uid = current_user.get("uid") if isinstance(current_user, dict) else None
        if not client_uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión inválida o expirada.",
            )

        cliente = db.scalar(select(User).where(User.firebase_uid == client_uid))
        if cliente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Completa tu registro en TekniGO",
            )
        if not any(role.rol == "CLIENTE" for role in cliente.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo las cuentas con rol de cliente pueden enviar solicitudes directas.",
            )

        # 1. Validar que la fecha sea futura (CA-02)
        fecha_hora = (
            request.fecha_hora
            if request.fecha_hora.tzinfo is not None
            else request.fecha_hora.replace(tzinfo=timezone.utc)
        )
        if fecha_hora <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La fecha y hora deben ser en el futuro.",
            )

        # 2. Buscar el servicio y validar (reutilizamos la lógica)
        servicio = DirectRequestRepository.get_service_by_id(db, request.servicio_id)
        if not servicio or not servicio.activo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Servicio inválido o inactivo.",
            )

        # El perfil público usa el UUID; las solicitudes se guardan con el UID de Firebase.
        tecnico = db.get(User, servicio.usuario_id)
        if not tecnico or request.tecnico_id not in (str(tecnico.id), tecnico.firebase_uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El técnico seleccionado no existe.",
            )

        require_operational_technician(tecnico, db)
        covered_municipality = db.scalar(select(CoverageZone.id).where(
            CoverageZone.usuario_id == tecnico.id,
            CoverageZone.municipio == request.municipio,
        ).limit(1))
        if covered_municipality is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El municipio seleccionado no está en la cobertura del técnico.",
            )

        # 4. Calcular el presupuesto exacto desde el backend (no confiamos en el front) (CA-04)
        total_calculado = DirectRequestService._compute_total(servicio.tarifa_base, request.cantidad)

        # 5. Armar el diccionario para la base de datos
        request_dict = {
            "cliente_id": cliente.firebase_uid,
            "tecnico_id": tecnico.firebase_uid,
            "servicio_id": str(servicio.id),
            "municipio": request.municipio,
            "sector": request.sector,
            "direccion_exacta": request.direccion_exacta,
            "fecha_hora": fecha_hora,
            "descripcion": request.descripcion,
            "cantidad": request.cantidad,
            "tarifa_aplicada": Decimal(str(servicio.tarifa_base)).quantize(Decimal("0.01")),
            "unidad_cobro": servicio.unidad_cobro,
            "monto_base": total_calculado,
            "metodo_pago": request.metodo_pago,
            "estado": "PENDIENTE",
        }

        try:
            return DirectRequestRepository.create_request(db, request_dict)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo registrar la solicitud con los datos proporcionados.",
            ) from None