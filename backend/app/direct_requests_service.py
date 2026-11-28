from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.account_suspension_service import assert_client_can_operate
from app.adjustment_repository import SqlAlchemyAdjustmentRepository
from app.adjustment_service import to_money
from app.direct_requests_repository import DirectRequestRepository
from app.direct_requests_schemas import CreateDirectRequest, EstimateRequest, EstimateResponse, RegisterAdjustmentRequest, UpdatePaymentMethodRequest
from app.models import CoverageZone, DirectRequest, User
from app.request_states import state_key
from app.specialties import require_active_specialty
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

        require_active_specialty(db, servicio.especialidad_id)
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
        assert_client_can_operate(db, cliente, action_label="crear contrataciones directas")

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

        require_active_specialty(db, servicio.especialidad_id)
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
            "referencia_pasarela": request.referencia_pasarela,
            "monto_ajuste": Decimal("0.00"),
            "monto_total": total_calculado,
            "estado": "PENDIENTE",
        }

        try:
            return DirectRequestRepository.create_request(db, request_dict)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo registrar la solicitud con los datos proporcionados.",
            ) from None

    @staticmethod
    def update_payment_method(
        db: Session,
        solicitud_id: str,
        request: UpdatePaymentMethodRequest,
        current_user: dict,
    ) -> DirectRequest:
        """HU-37 CA-04: Permite actualizar método antes de ser aceptada; bloquea una vez aceptada."""
        client_uid = current_user.get("uid") if isinstance(current_user, dict) else None
        if not client_uid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada.")
        record = db.scalar(
            select(DirectRequest).where(DirectRequest.id == solicitud_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada.")
        if record.cliente_id != client_uid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permiso para modificar esta solicitud.")
        if state_key(record.estado or "PENDIENTE") in {"ACEPTADA", "ASIGNADA", "EN PROCESO", "COMPLETADA"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Una contratación ya aceptada conserva el método inicial; no se permite modificarlo (CA-04).",
            )
        nuevo_metodo = request.metodo_pago.lower()
        record.metodo_pago = nuevo_metodo
        if nuevo_metodo == "tarjeta":
            if request.referencia_pasarela:
                record.referencia_pasarela = request.referencia_pasarela
        else:
            record.referencia_pasarela = None
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def register_adjustment(
        db: Session,
        solicitud_id: str,
        request: RegisterAdjustmentRequest,
        current_user: dict,
    ) -> DirectRequest:
        """HU-37 CA-04 + HU-36 CA-05: solo registra el medio de pago de la diferencia de un ajuste YA aprobado."""
        user_uid = current_user.get("uid") if isinstance(current_user, dict) else None
        if not user_uid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada.")
        record = db.scalar(
            select(DirectRequest).where(DirectRequest.id == solicitud_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada.")
        if user_uid != record.cliente_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Solo el cliente propietario define el medio de pago de la diferencia.")
        adjustment = SqlAlchemyAdjustmentRepository().get(db, "DIRECTA", str(record.id))
        if adjustment is not None:
            if adjustment.estado != "APROBADO":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="El ajuste debe ser aprobado por el cliente antes de registrar su medio de pago.")
            diferencia = to_money(adjustment.diferencia)
            if diferencia <= 0 or to_money(request.monto_ajuste) != diferencia:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail=f"El monto debe ser la diferencia aprobada (L. {diferencia}).")
        monto_ajuste_dec = to_money(request.monto_ajuste)
        base_dec = to_money(record.monto_base or 0)
        record.monto_ajuste = monto_ajuste_dec
        record.metodo_pago_ajuste = request.metodo_pago_ajuste.lower()
        record.referencia_pasarela_ajuste = request.referencia_pasarela_ajuste
        record.monto_total = to_money(base_dec + monto_ajuste_dec)
        db.commit()
        db.refresh(record)
        return record
