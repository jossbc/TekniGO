"""HU-38 ST-01: Adaptadores de pasarela (Stripe Sandbox + Dummy local) y registro OCP por interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Mapping
import uuid

import httpx

from app.payment_gateway_config import PaymentGatewaySettings
from app.payment_gateway_port import (
    CreatePaymentRequest,
    CreateRefundRequest,
    GatewayEnvironment,
    GatewayPaymentResult,
    GatewayPaymentStatus,
    GatewayRefundResult,
    GatewayRefundStatus,
    GatewayRuntimeInfo,
    GatewayWebhookEvent,
    HNL_CURRENCY,
    PaymentGatewayAuthenticationError,
    PaymentGatewayCommunicationError,
    PaymentGatewayConfigError,
    PaymentGatewayPort,
    centavos_to_hnl,
    hnl_to_centavos,
    to_hnl_money,
)

logger = logging.getLogger(__name__)

STRIPE_USER_ERROR_MESSAGES: dict[str, str] = {
    "card_declined": "Tu tarjeta fue rechazada por el banco emisor. No se realizó ningún cargo; intenta con otra tarjeta.",
    "insufficient_funds": "La tarjeta no cuenta con fondos suficientes. No se realizó ningún cargo; intenta con otro método de pago.",
    "expired_card": "La tarjeta ingresada está vencida. Verifica la fecha o intenta con otra tarjeta.",
    "incorrect_cvc": "El código de seguridad (CVC) de la tarjeta es incorrecto. Verifícalo e intenta nuevamente.",
    "incorrect_number": "El número de tarjeta ingresado no es válido. Verifícalo e intenta nuevamente.",
    "processing_error": "Ocurrió un inconveniente temporal al procesar la tarjeta. No se realizó ningún cargo; intenta nuevamente.",
    "rate_limit": "La pasarela de pagos está recibiendo demasiadas solicitudes en este momento. Intenta nuevamente en unos segundos.",
    "charge_already_refunded": "Este cobro ya fue reembolsado previamente en la pasarela.",
}


def _translate_stripe_error(status_code: int, err: Any) -> str:
    err_dict = err if isinstance(err, dict) else {}
    raw_msg = str(err_dict.get("message") or "Error en Stripe Sandbox.")
    err_code = str(err_dict.get("code") or "").strip().lower()
    decline_code = str(err_dict.get("decline_code") or "").strip().lower()
    err_type = str(err_dict.get("type") or "").strip().lower()

    # El detalle técnico completo queda solo en los logs del servidor para depuración
    logger.warning(
        "Stripe Sandbox respondió %s (type=%s, code=%s, decline_code=%s): %s",
        status_code,
        err_type or "n/a",
        err_code or "n/a",
        decline_code or "n/a",
        raw_msg,
    )

    if decline_code in STRIPE_USER_ERROR_MESSAGES:
        return STRIPE_USER_ERROR_MESSAGES[decline_code]
    if err_code in STRIPE_USER_ERROR_MESSAGES:
        return STRIPE_USER_ERROR_MESSAGES[err_code]
    if status_code in (401, 403):
        return "La pasarela de pagos no está disponible temporalmente por un problema de autenticación. Intenta más tarde o elige efectivo."
    if status_code == 429:
        return STRIPE_USER_ERROR_MESSAGES["rate_limit"]
    if 400 <= status_code < 500:
        return "No pudimos iniciar el proceso de pago con la pasarela en este momento. No se realizó ningún cargo; intenta nuevamente más tarde."
    return "La pasarela de pagos presenta una interrupción temporal. No se realizó ningún cargo; intenta nuevamente en unos minutos."

def _header_ci(headers: Mapping[str, str], target: str) -> str | None:
    target_lower = target.lower()
    for key, value in headers.items():
        if str(key).lower() == target_lower:
            return str(value)
    return None


def _normalize_stripe_payment_status(
    *,
    session_status: str | None = None,
    payment_status: str | None = None,
    intent_status: str | None = None,
    has_last_error: bool = False,
) -> GatewayPaymentStatus:
    pay = (payment_status or "").strip().lower()
    sess = (session_status or "").strip().lower()
    pi = (intent_status or "").strip().lower()

    if pay == "paid" or pi == "succeeded":
        return GatewayPaymentStatus.CONFIRMADO
    if sess == "expired":
        return GatewayPaymentStatus.ABANDONADO
    if pi == "canceled":
        return GatewayPaymentStatus.ABANDONADO
    if has_last_error or pi in {"requires_payment_method"} and sess == "complete":
        return GatewayPaymentStatus.RECHAZADO
    if has_last_error:
        return GatewayPaymentStatus.RECHAZADO
    return GatewayPaymentStatus.PENDIENTE


def _normalize_stripe_refund_status(raw_status: str | None) -> GatewayRefundStatus:
    normalized = (raw_status or "").strip().lower()
    if normalized == "succeeded":
        return GatewayRefundStatus.CONFIRMADO
    if normalized in {"failed", "canceled"}:
        return GatewayRefundStatus.FALLIDO
    return GatewayRefundStatus.EN_PROCESO


class StripeSandboxGatewayAdapter(PaymentGatewayPort):
    """Adaptador real contra Stripe API en modo Sandbox/Test usando httpx (Adapter + DIP)."""

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        api_base_url: str = "https://api.stripe.com",
        timeout_seconds: float = 15.0,
        webhook_tolerance_seconds: int = 300,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not secret_key.startswith("sk_test_"):
            raise PaymentGatewayConfigError(
                "CA-08: StripeSandboxGatewayAdapter solo acepta llaves Sandbox ('sk_test_...')."
            )
        if not webhook_secret.strip():
            raise PaymentGatewayConfigError("STRIPE_WEBHOOK_SECRET es obligatorio.")
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._webhook_tolerance_seconds = webhook_tolerance_seconds
        self._http_client = http_client

    def runtime_info(self) -> GatewayRuntimeInfo:
        return GatewayRuntimeInfo(
            provider="stripe",
            environment=GatewayEnvironment.SANDBOX,
            is_sandbox=True,
            is_local_simulator=False,
            display_label="Stripe Sandbox (Modo pruebas)",
            currency=HNL_CURRENCY,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:255]

        url = f"{self._api_base_url}{path}"
        try:
            if self._http_client is not None:
                resp = self._http_client.request(method, url, data=data, params=params, headers=headers)
            else:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    resp = client.request(method, url, data=data, params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Error de red al comunicar con Stripe Sandbox: %s: %s", type(exc).__name__, exc)
            raise PaymentGatewayCommunicationError(
                "No pudimos conectar con la pasarela de pagos en este momento. No se realizó ningún cargo; intenta nuevamente en unos minutos."
            ) from None

        try:
            payload = resp.json()
        except ValueError:
            logger.warning("Respuesta no JSON desde Stripe Sandbox (HTTP %s).", resp.status_code)
            raise PaymentGatewayCommunicationError(
                "Recibimos una respuesta inválida de la pasarela de pagos. Intenta nuevamente en unos minutos."
            ) from None

        if resp.status_code >= 400:
            err = payload.get("error") if isinstance(payload, dict) else {}
            raise PaymentGatewayCommunicationError(_translate_stripe_error(resp.status_code, err))

        if not isinstance(payload, dict):
            logger.warning("Estructura inesperada desde Stripe Sandbox: %s", type(payload).__name__)
            raise PaymentGatewayCommunicationError(
                "No pudimos interpretar la respuesta de la pasarela de pagos. Intenta nuevamente."
            )
        return payload

    def create_payment(self, request: CreatePaymentRequest) -> GatewayPaymentResult:
        amount_hnl = to_hnl_money(request.amount_hnl)
        if amount_hnl <= Decimal("0.00"):
            raise PaymentGatewayConfigError("El monto del cobro digital debe ser mayor a L. 0.00.")
        centavos = hnl_to_centavos(amount_hnl)

        form_data: dict[str, Any] = {
            "mode": "payment",
            "allowed_payment_method_types[0]": "card",
            "client_reference_id": request.internal_reference,
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": request.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(centavos),
            "line_items[0][price_data][product_data][name]": request.description[:120],
            "metadata[referencia_interna]": request.internal_reference,
            "payment_intent_data[metadata][referencia_interna]": request.internal_reference,
        }
        for key, value in request.metadata.items():
            clean_key = str(key).strip()
            if not clean_key:
                continue
            clean_val = str(value)[:200]
            form_data[f"metadata[{clean_key}]"] = clean_val
            form_data[f"payment_intent_data[metadata][{clean_key}]"] = clean_val

        payload = self._request(
            "POST",
            "/v1/checkout/sessions",
            data=form_data,
            idempotency_key=request.idempotency_key,
        )
        pi_raw = payload.get("payment_intent")
        pi_id = pi_raw.get("id") if isinstance(pi_raw, dict) else (str(pi_raw) if pi_raw else None)

        return GatewayPaymentResult(
            provider="stripe",
            external_reference=str(payload["id"]),
            payment_intent_id=pi_id,
            checkout_url=payload.get("url"),
            status=_normalize_stripe_payment_status(
                session_status=payload.get("status"),
                payment_status=payload.get("payment_status"),
            ),
            amount_hnl=amount_hnl,
            currency=request.currency.upper(),
            raw_status=str(payload.get("status") or "open"),
            is_sandbox=True,
            is_local_simulator=False,
        )

    def get_payment_status(self, external_reference: str) -> GatewayPaymentResult:
        clean_ref = (external_reference or "").strip()
        if not clean_ref:
            raise PaymentGatewayCommunicationError("Referencia externa de pago vacía.")

        if clean_ref.startswith("pi_"):
            payload = self._request("GET", f"/v1/payment_intents/{clean_ref}")
            last_error = payload.get("last_payment_error") if isinstance(payload.get("last_payment_error"), dict) else None
            amount = centavos_to_hnl(int(payload.get("amount") or 0))
            return GatewayPaymentResult(
                provider="stripe",
                external_reference=str(payload["id"]),
                payment_intent_id=str(payload["id"]),
                checkout_url=None,
                status=_normalize_stripe_payment_status(
                    intent_status=payload.get("status"),
                    has_last_error=bool(last_error),
                ),
                amount_hnl=amount,
                currency=str(payload.get("currency") or HNL_CURRENCY).upper(),
                raw_status=str(payload.get("status") or ""),
                failure_reason=last_error.get("message") if last_error else None,
                is_sandbox=True,
                is_local_simulator=False,
            )

        payload = self._request(
            "GET",
            f"/v1/checkout/sessions/{clean_ref}",
            params={"expand[]": "payment_intent"},
        )
        pi_obj = payload.get("payment_intent") if isinstance(payload.get("payment_intent"), dict) else None
        pi_id = pi_obj.get("id") if pi_obj else (str(payload.get("payment_intent")) if payload.get("payment_intent") else None)
        last_error = (
            pi_obj.get("last_payment_error")
            if pi_obj and isinstance(pi_obj.get("last_payment_error"), dict)
            else None
        )
        amount = centavos_to_hnl(int(payload.get("amount_total") or (pi_obj or {}).get("amount") or 0))
        return GatewayPaymentResult(
            provider="stripe",
            external_reference=str(payload["id"]),
            payment_intent_id=pi_id,
            checkout_url=payload.get("url"),
            status=_normalize_stripe_payment_status(
                session_status=payload.get("status"),
                payment_status=payload.get("payment_status"),
                intent_status=pi_obj.get("status") if pi_obj else None,
                has_last_error=bool(last_error),
            ),
            amount_hnl=amount,
            currency=str(payload.get("currency") or HNL_CURRENCY).upper(),
            raw_status=f"{payload.get('status')}:{payload.get('payment_status')}",
            failure_reason=last_error.get("message") if last_error else None,
            is_sandbox=True,
            is_local_simulator=False,
        )

    def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayWebhookEvent:
        sig_header = _header_ci(headers, "Stripe-Signature")
        if not sig_header:
            raise PaymentGatewayAuthenticationError("Falta el encabezado Stripe-Signature.")

        timestamp_str: str | None = None
        signatures: list[str] = []
        for part in sig_header.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "t":
                timestamp_str = v
            elif k == "v1":
                signatures.append(v)

        if not timestamp_str or not signatures:
            raise PaymentGatewayAuthenticationError("Encabezado Stripe-Signature malformado.")

        try:
            timestamp_int = int(timestamp_str)
        except ValueError:
            raise PaymentGatewayAuthenticationError("Timestamp de firma Stripe inválido.") from None

        if abs(int(time.time()) - timestamp_int) > self._webhook_tolerance_seconds:
            raise PaymentGatewayAuthenticationError("Notificación de Stripe fuera de la ventana de tolerancia.")

        signed_payload = f"{timestamp_str}.".encode("utf-8") + raw_body
        expected_sig = hmac.new(
            self._webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(expected_sig, candidate) for candidate in signatures):
            raise PaymentGatewayAuthenticationError("Firma criptográfica de Stripe inválida.")

        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PaymentGatewayAuthenticationError("Payload JSON de webhook inválido.") from None

        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        obj = (event.get("data") or {}).get("object") or {}
        if not event_id or not event_type or not isinstance(obj, dict):
            raise PaymentGatewayAuthenticationError("Evento Stripe incompleto.")

        metadata = {str(k): str(v) for k, v in (obj.get("metadata") or {}).items()}
        currency = str(obj.get("currency") or HNL_CURRENCY).upper()

        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            return GatewayWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                provider="stripe",
                external_reference=str(obj.get("id") or ""),
                payment_intent_id=str(obj.get("payment_intent") or "") or None,
                payment_status=GatewayPaymentStatus.CONFIRMADO
                if str(obj.get("payment_status") or "").lower() == "paid"
                else GatewayPaymentStatus.PENDIENTE,
                amount_hnl=centavos_to_hnl(int(obj.get("amount_total") or 0)),
                currency=currency,
                metadata=metadata,
            )

        if event_type in {"checkout.session.expired", "checkout.session.async_payment_failed"}:
            status = (
                GatewayPaymentStatus.ABANDONADO
                if event_type == "checkout.session.expired"
                else GatewayPaymentStatus.RECHAZADO
            )
            return GatewayWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                provider="stripe",
                external_reference=str(obj.get("id") or ""),
                payment_intent_id=str(obj.get("payment_intent") or "") or None,
                payment_status=status,
                amount_hnl=centavos_to_hnl(int(obj.get("amount_total") or 0)),
                currency=currency,
                failure_reason="Sesión expirada o cobro rechazado en Stripe Sandbox.",
                metadata=metadata,
            )

        if event_type == "payment_intent.succeeded":
            return GatewayWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                provider="stripe",
                external_reference=str(obj.get("id") or ""),
                payment_intent_id=str(obj.get("id") or ""),
                payment_status=GatewayPaymentStatus.CONFIRMADO,
                amount_hnl=centavos_to_hnl(int(obj.get("amount_received") or obj.get("amount") or 0)),
                currency=currency,
                metadata=metadata,
            )

        if event_type in {"payment_intent.payment_failed", "payment_intent.canceled"}:
            last_error = obj.get("last_payment_error") if isinstance(obj.get("last_payment_error"), dict) else {}
            status = (
                GatewayPaymentStatus.RECHAZADO
                if event_type == "payment_intent.payment_failed"
                else GatewayPaymentStatus.ABANDONADO
            )
            return GatewayWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                provider="stripe",
                external_reference=str(obj.get("id") or ""),
                payment_intent_id=str(obj.get("id") or ""),
                payment_status=status,
                amount_hnl=centavos_to_hnl(int(obj.get("amount") or 0)),
                currency=currency,
                failure_reason=str(last_error.get("message") or "Pago no completado en Stripe Sandbox."),
                metadata=metadata,
            )

        if event_type in {"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"}:
            raw_refund_status = "failed" if event_type == "refund.failed" else obj.get("status")
            return GatewayWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                provider="stripe",
                payment_intent_id=str(obj.get("payment_intent") or "") or None,
                refund_reference=str(obj.get("id") or "") or None,
                refund_status=_normalize_stripe_refund_status(raw_refund_status),
                amount_hnl=centavos_to_hnl(int(obj.get("amount") or 0)),
                currency=currency,
                failure_reason=str(obj.get("failure_reason") or "") or None,
                metadata=metadata,
            )

        return GatewayWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            provider="stripe",
            external_reference=str(obj.get("id") or "") or None,
            metadata=metadata,
        )

    def create_refund(self, request: CreateRefundRequest) -> GatewayRefundResult:
        amount_hnl = to_hnl_money(request.amount_hnl)
        if amount_hnl <= Decimal("0.00"):
            raise PaymentGatewayConfigError("El monto a reembolsar debe ser mayor a L. 0.00.")

        form_data: dict[str, Any] = {
            "payment_intent": request.payment_reference,
            "amount": str(hnl_to_centavos(amount_hnl)),
            "reason": request.reason,
        }
        for key, value in request.metadata.items():
            clean_key = str(key).strip()
            if clean_key:
                form_data[f"metadata[{clean_key}]"] = str(value)[:200]

        payload = self._request(
            "POST",
            "/v1/refunds",
            data=form_data,
            idempotency_key=request.idempotency_key,
        )
        return GatewayRefundResult(
            provider="stripe",
            refund_reference=str(payload.get("id") or "") or None,
            payment_reference=request.payment_reference,
            status=_normalize_stripe_refund_status(payload.get("status")),
            amount_hnl=centavos_to_hnl(int(payload.get("amount") or hnl_to_centavos(amount_hnl))),
            currency=str(payload.get("currency") or request.currency).upper(),
            raw_status=str(payload.get("status") or ""),
            failure_reason=str(payload.get("failure_reason") or "") or None,
        )

    def get_refund_status(self, refund_reference: str) -> GatewayRefundResult:
        clean_ref = (refund_reference or "").strip()
        if not clean_ref:
            raise PaymentGatewayCommunicationError("Referencia de reembolso vacía.")
        payload = self._request("GET", f"/v1/refunds/{clean_ref}")
        return GatewayRefundResult(
            provider="stripe",
            refund_reference=str(payload.get("id") or clean_ref),
            payment_reference=str(payload.get("payment_intent") or ""),
            status=_normalize_stripe_refund_status(payload.get("status")),
            amount_hnl=centavos_to_hnl(int(payload.get("amount") or 0)),
            currency=str(payload.get("currency") or HNL_CURRENCY).upper(),
            raw_status=str(payload.get("status") or ""),
            failure_reason=str(payload.get("failure_reason") or "") or None,
        )


class DummyLocalGatewayAdapter(PaymentGatewayPort):
    """
    Adaptador local determinista para pruebas automatizadas y respaldo de desarrollo.
    CA-08: siempre informa es_simulacion_local=True y nunca se presenta como pasarela real.
    """

    def __init__(self, *, webhook_secret: str) -> None:
        self._webhook_secret = webhook_secret
        self._payments: dict[str, GatewayPaymentResult] = {}
        self._refunds: dict[str, GatewayRefundResult] = {}

    def runtime_info(self) -> GatewayRuntimeInfo:
        return GatewayRuntimeInfo(
            provider="dummy",
            environment=GatewayEnvironment.SIMULADOR_LOCAL,
            is_sandbox=False,
            is_local_simulator=True,
            display_label="Simulador local (Dummy — no es integración real)",
            currency=HNL_CURRENCY,
        )

    def sign_webhook_payload(self, raw_body: bytes) -> str:
        """Firma auxiliar HMAC-SHA256 para pruebas unitarias e integración controlada."""
        return hmac.new(self._webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def create_payment(self, request: CreatePaymentRequest) -> GatewayPaymentResult:
        amount_hnl = to_hnl_money(request.amount_hnl)
        if amount_hnl <= Decimal("0.00"):
            raise PaymentGatewayConfigError("El monto del cobro digital debe ser mayor a L. 0.00.")
        token = uuid.uuid5(uuid.NAMESPACE_OID, f"dummy-pay:{request.idempotency_key}").hex[:14].upper()
        external_ref = f"DUMMY-PAY-{token}"
        result = GatewayPaymentResult(
            provider="dummy",
            external_reference=external_ref,
            payment_intent_id=external_ref,
            checkout_url=None,
            status=GatewayPaymentStatus.PENDIENTE,
            amount_hnl=amount_hnl,
            currency=request.currency.upper(),
            raw_status="pending",
            is_sandbox=False,
            is_local_simulator=True,
        )
        self._payments[external_ref] = result
        return result

    def get_payment_status(self, external_reference: str) -> GatewayPaymentResult:
        existing = self._payments.get(external_reference)
        if existing is None:
            raise PaymentGatewayCommunicationError(f"Intento dummy no encontrado: {external_reference}")
        return existing

    def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayWebhookEvent:
        signature = _header_ci(headers, "X-TekniGO-Signature")
        if not signature:
            raise PaymentGatewayAuthenticationError("Falta la firma X-TekniGO-Signature.")
        expected = self.sign_webhook_payload(raw_body)
        if not hmac.compare_digest(expected, signature.strip()):
            raise PaymentGatewayAuthenticationError("Firma de webhook dummy inválida.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PaymentGatewayAuthenticationError("Payload JSON de webhook dummy inválido.") from None

        raw_pay_status = payload.get("payment_status")
        raw_ref_status = payload.get("refund_status")
        raw_amount = payload.get("amount_hnl")
        return GatewayWebhookEvent(
            event_id=str(payload.get("event_id") or uuid.uuid4()),
            event_type=str(payload.get("event_type") or "dummy.event"),
            provider="dummy",
            external_reference=payload.get("external_reference"),
            payment_intent_id=payload.get("payment_intent_id") or payload.get("external_reference"),
            refund_reference=payload.get("refund_reference"),
            payment_status=GatewayPaymentStatus(raw_pay_status) if raw_pay_status else None,
            refund_status=GatewayRefundStatus(raw_ref_status) if raw_ref_status else None,
            amount_hnl=to_hnl_money(raw_amount) if raw_amount is not None else None,
            currency=str(payload.get("currency") or HNL_CURRENCY).upper(),
            failure_reason=payload.get("failure_reason"),
            metadata=payload.get("metadata") or {},
        )

    def create_refund(self, request: CreateRefundRequest) -> GatewayRefundResult:
        amount_hnl = to_hnl_money(request.amount_hnl)
        token = uuid.uuid5(uuid.NAMESPACE_OID, f"dummy-rfnd:{request.idempotency_key}").hex[:12].upper()
        refund_ref = f"DUMMY-RFND-{token}"
        result = GatewayRefundResult(
            provider="dummy",
            refund_reference=refund_ref,
            payment_reference=request.payment_reference,
            status=GatewayRefundStatus.EN_PROCESO,
            amount_hnl=amount_hnl,
            currency=request.currency.upper(),
            raw_status="pending",
        )
        self._refunds[refund_ref] = result
        return result

    def get_refund_status(self, refund_reference: str) -> GatewayRefundResult:
        existing = self._refunds.get(refund_reference)
        if existing is None:
            raise PaymentGatewayCommunicationError(f"Reembolso dummy no encontrado: {refund_reference}")
        return existing


class PaymentGatewayFactory(ABC):
    """Interfaz Abstract Factory (OCP): permite incorporar nuevas pasarelas sin modificar el registro."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identificador único en minúsculas del proveedor."""

    @abstractmethod
    def create(self, settings: PaymentGatewaySettings) -> PaymentGatewayPort:
        """Construye el adaptador validando su configuración."""


class StripeSandboxGatewayFactory(PaymentGatewayFactory):
    @property
    def provider_name(self) -> str:
        return "stripe"

    def create(self, settings: PaymentGatewaySettings) -> PaymentGatewayPort:
        secret_key, webhook_secret = settings.require_stripe_sandbox_secrets()
        return StripeSandboxGatewayAdapter(
            secret_key=secret_key,
            webhook_secret=webhook_secret,
            api_base_url=settings.stripe_api_base_url,
            timeout_seconds=settings.timeout_seconds,
            webhook_tolerance_seconds=settings.webhook_tolerance_seconds,
        )


class DummyLocalGatewayFactory(PaymentGatewayFactory):
    @property
    def provider_name(self) -> str:
        return "dummy"

    def create(self, settings: PaymentGatewaySettings) -> PaymentGatewayPort:
        return DummyLocalGatewayAdapter(
            webhook_secret=settings.require_dummy_webhook_secret(),
        )


class PaymentGatewayRegistry:
    """Registro OCP por interfaz: resuelve el adaptador sin condicionales por proveedor."""

    def __init__(self) -> None:
        self._factories: dict[str, PaymentGatewayFactory] = {}

    def register(self, factory: PaymentGatewayFactory) -> None:
        key = factory.provider_name.strip().lower()
        if not key:
            raise PaymentGatewayConfigError("El nombre del proveedor no puede estar vacío.")
        self._factories[key] = factory

    def registered_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories.keys()))

    def resolve(self, settings: PaymentGatewaySettings | None = None) -> PaymentGatewayPort:
        effective = settings or PaymentGatewaySettings.from_env()
        factory = self._factories.get(effective.provider.strip().lower())
        if factory is None:
            available = ", ".join(self.registered_providers())
            raise PaymentGatewayConfigError(
                f"Proveedor de pasarela '{effective.provider}' no registrado. Disponibles: {available}."
            )
        return factory.create(effective)


def build_default_payment_gateway_registry() -> PaymentGatewayRegistry:
    registry = PaymentGatewayRegistry()
    registry.register(StripeSandboxGatewayFactory())
    registry.register(DummyLocalGatewayFactory())
    return registry


default_payment_gateway_registry = build_default_payment_gateway_registry()


def get_payment_gateway(settings: PaymentGatewaySettings | None = None) -> PaymentGatewayPort:
    """Punto de entrada único (DIP) para obtener el adaptador de pasarela configurado."""
    return default_payment_gateway_registry.resolve(settings)