"""HU-38 ST-02/ST-03: Endpoints REST de intentos, webhooks autenticados y conciliación."""
from __future__ import annotations
import logging
from dataclasses import replace

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.digital_payments_service import (
    DigitalPaymentConflictError,
    DigitalPaymentError,
    DigitalPaymentForbiddenError,
    DigitalPaymentIntentService,
    DigitalPaymentNotFoundError,
    DigitalPaymentValidationError,
    SqlAlchemyDigitalPaymentRepository,
)
from app.digital_refund_service import DigitalRefundService
from app.models import User
from app.payment_gateway_adapters import default_payment_gateway_registry, get_payment_gateway
from app.payment_gateway_config import PaymentGatewaySettings
from app.payment_gateway_port import (
    PaymentGatewayAuthenticationError,
    PaymentGatewayError,
    PaymentGatewayConfigError,
)
from app.payment_lifecycle_bridge import ServicePaymentConfirmationObserver
from app.payment_reconciliation_service import (
    PaymentReconciliationService,
    SqlAlchemyWebhookEventRepository,
)
from app.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["Pagos Digitales (HU-38)"])
NO_STORE = {"Cache-Control": "no-store, private"}


class CreatePaymentIntentSchema(BaseModel):
    solicitud_id: str = Field(..., min_length=1, max_length=64)
    modalidad: str | None = Field(default=None, description="DIRECTA o ABIERTA (opcional si el ID es único)")
    concepto: str = Field(default="BASE", description="BASE o AJUSTE")
    propuesta_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=160)


class ReconcileSessionSchema(BaseModel):
    referencia_pasarela: str = Field(..., min_length=1, max_length=160)


def _require_user(db: Session, claims: dict) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(401, "Sesión inválida o expirada.")
    user = db.scalar(select(User).where(User.firebase_uid == uid))
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO.")
    return user


def _build_payment_service(db: Session) -> DigitalPaymentIntentService:
    gateway = get_payment_gateway()
    return DigitalPaymentIntentService(
        db,
        SqlAlchemyDigitalPaymentRepository(db),
        gateway,
    )


def _build_reconciliation_service(db: Session, *, provider: str | None = None) -> PaymentReconciliationService:
    if provider:
        settings = replace(PaymentGatewaySettings.from_env(), provider=provider.strip().lower())
        gateway = default_payment_gateway_registry.resolve(settings)
    else:
        gateway = get_payment_gateway()
    return PaymentReconciliationService(
        db,
        SqlAlchemyDigitalPaymentRepository(db),
        SqlAlchemyWebhookEventRepository(db),
        gateway,
        observers=(ServicePaymentConfirmationObserver(),),
    )

def _build_refund_service(db: Session) -> DigitalRefundService:
    return DigitalRefundService(
        db,
        SqlAlchemyDigitalPaymentRepository(db),
        get_payment_gateway(),
    )


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, PaymentGatewayAuthenticationError):
        logger.warning("Fallo de autenticación en webhook/pasarela: %s", exc)
        raise HTTPException(401, "No se pudo verificar la autenticidad de la notificación de pago.") from None
    if isinstance(exc, PaymentGatewayConfigError):
        logger.error("Error de configuración de pasarela digital: %s", exc)
        raise HTTPException(
            503,
            "El pago digital no está disponible en este momento. Intenta más tarde o selecciona pago en efectivo.",
        ) from None
    if isinstance(exc, DigitalPaymentValidationError):
        raise HTTPException(400, str(exc)) from None
    if isinstance(exc, DigitalPaymentForbiddenError):
        raise HTTPException(403, str(exc)) from None
    if isinstance(exc, DigitalPaymentNotFoundError):
        raise HTTPException(404, str(exc)) from None
    if isinstance(exc, DigitalPaymentConflictError):
        raise HTTPException(409, str(exc)) from None
    if isinstance(exc, PaymentGatewayError):
        raise HTTPException(502, str(exc)) from None
    logger.exception("Error inesperado en pagos digitales: %s", exc)
    raise HTTPException(500, "No pudimos procesar el pago digital en este momento. Intenta nuevamente.") from None
    
@router.get("/gateway-info")
def get_gateway_runtime_info_endpoint(response: Response):
    """Informa a la UI si opera en Sandbox real o Simulador local sin exponer secretos (CA-08)."""
    response.headers.update(NO_STORE)
    try:
        return get_payment_gateway().runtime_info().to_dict()
    except PaymentGatewayError as exc:
        _raise_http(exc)


@router.post("/intents")
def create_payment_intent_endpoint(
    payload: CreatePaymentIntentSchema,
    response: Response,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    effective_idem = payload.idempotency_key or (idempotency_header if isinstance(idempotency_header, str) else None)
    try:
        return _build_payment_service(db).create_attempt(
            user,
            solicitud_id=payload.solicitud_id,
            modalidad=payload.modalidad,
            concepto=payload.concepto,
            idempotency_key=effective_idem,
            propuesta_id=payload.propuesta_id,
        )
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)


@router.get("/intents/{attempt_id}")
def get_payment_intent_detail_endpoint(
    attempt_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_payment_service(db).get_attempt_detail(user, attempt_id)
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)


@router.get("/services/{modalidad}/{solicitud_id}")
def get_service_digital_payments_endpoint(
    modalidad: str,
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consulta por servicio el cobro digital BASE y AJUSTE (contrato de integración con HU-39)."""
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_payment_service(db).get_service_digital_payments_summary(
            modalidad,
            solicitud_id,
            viewer=user,
        )
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)



@router.post("/intents/{attempt_id}/reconcile")
def reconcile_payment_intent_endpoint(
    attempt_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consulta autoritativamente a la pasarela y sincroniza el estado del intento (ST-03)."""
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_reconciliation_service(db).reconcile_attempt(user, attempt_id)
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)


@router.post("/reconcile-session")
def reconcile_payment_session_endpoint(
    payload: ReconcileSessionSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Concilia un cobro usando el session_id / referencia externa devuelta por el checkout."""
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_reconciliation_service(db).reconcile_by_gateway_reference(
            user,
            payload.referencia_pasarela,
        )
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)


@router.post("/webhooks/{provider}")
async def receive_payment_webhook_endpoint(
    provider: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Recepción autenticada e idempotente de eventos de pasarela (CA-04)."""
    response.headers.update(NO_STORE)
    raw_body = await request.body()
    try:
        return _build_reconciliation_service(db, provider=provider).process_webhook(
            raw_body,
            dict(request.headers),
        )
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)




@router.get("/services/{modalidad}/{solicitud_id}/refund")
def get_service_refund_status_endpoint(
    modalidad: str,
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consulta el estado real de la devolución digital (EN_PROCESO / PENDIENTE_CONCILIACION / CONFIRMADO)."""
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_refund_service(db).get_refund_status(user, modalidad, solicitud_id)
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)


@router.post("/services/{modalidad}/{solicitud_id}/refund/retry")
def retry_service_refund_endpoint(
    modalidad: str,
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CA-06 / CA-07: reintenta una devolución fallida o concilia una en proceso contra la pasarela."""
    response.headers.update(NO_STORE)
    user = _require_user(db, claims)
    try:
        return _build_refund_service(db).retry_or_reconcile_refund(user, modalidad, solicitud_id)
    except (DigitalPaymentError, PaymentGatewayError) as exc:
        _raise_http(exc)