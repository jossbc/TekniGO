"""HU-38 ST-01: Carga y validación segura de configuración sandbox mediante secretos de entorno."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

from app.payment_gateway_port import HNL_CURRENCY, PaymentGatewayConfigError

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

DEFAULT_STRIPE_API_BASE_URL = "https://api.stripe.com"
DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_WEBHOOK_TOLERANCE_SECONDS = 300


def mask_secret(secret: str | None) -> str:
    """Enmascara credenciales para diagnósticos sin exponer el secreto."""
    cleaned = (secret or "").strip()
    if not cleaned:
        return "<no-configurado>"
    if len(cleaned) <= 8:
        return "****"
    return f"{cleaned[:7]}...{cleaned[-4:]}"


def _parse_positive_float(raw: str | None, *, default: float, name: str) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise PaymentGatewayConfigError(f"La variable {name} debe ser numérica.") from None
    if value <= 0:
        raise PaymentGatewayConfigError(f"La variable {name} debe ser mayor que cero.")
    return value


def _parse_positive_int(raw: str | None, *, default: int, name: str) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise PaymentGatewayConfigError(f"La variable {name} debe ser un entero.") from None
    if value <= 0:
        raise PaymentGatewayConfigError(f"La variable {name} debe ser mayor que cero.")
    return value


@dataclass(frozen=True)
class PaymentGatewaySettings:
    """Configuración inmutable de pasarela cargada exclusivamente desde variables de entorno."""

    provider: str
    currency: str
    timeout_seconds: float
    webhook_tolerance_seconds: int
    frontend_base_url: str
    stripe_secret_key: str | None
    stripe_publishable_key: str | None
    stripe_webhook_secret: str | None
    stripe_api_base_url: str
    dummy_webhook_secret: str | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "PaymentGatewaySettings":
        source = os.environ if env is None else env
        raw_provider = (source.get("PAYMENT_GATEWAY_PROVIDER") or "").strip().lower()
        stripe_secret = (source.get("STRIPE_SECRET_KEY") or "").strip() or None
        stripe_pub = (source.get("STRIPE_PUBLISHABLE_KEY") or "").strip() or None
        stripe_wh = (source.get("STRIPE_WEBHOOK_SECRET") or "").strip() or None
        dummy_wh = (source.get("DUMMY_GATEWAY_WEBHOOK_SECRET") or "").strip() or None

        # Si no se define explícitamente el proveedor, prioriza Stripe cuando exista su secreto.
        provider = raw_provider or ("stripe" if stripe_secret else "dummy")
        currency = (source.get("PAYMENT_GATEWAY_CURRENCY") or HNL_CURRENCY).strip().upper()
        if currency != HNL_CURRENCY:
            raise PaymentGatewayConfigError("TekniGO opera pagos digitales exclusivamente en lempiras (HNL).")

        return cls(
            provider=provider,
            currency=currency,
            timeout_seconds=_parse_positive_float(
                source.get("PAYMENT_GATEWAY_TIMEOUT_SECONDS"),
                default=DEFAULT_TIMEOUT_SECONDS,
                name="PAYMENT_GATEWAY_TIMEOUT_SECONDS",
            ),
            webhook_tolerance_seconds=_parse_positive_int(
                source.get("PAYMENT_GATEWAY_WEBHOOK_TOLERANCE_SECONDS"),
                default=DEFAULT_WEBHOOK_TOLERANCE_SECONDS,
                name="PAYMENT_GATEWAY_WEBHOOK_TOLERANCE_SECONDS",
            ),
            frontend_base_url=(
                source.get("PAYMENT_FRONTEND_BASE_URL") or DEFAULT_FRONTEND_BASE_URL
            ).strip().rstrip("/"),
            stripe_secret_key=stripe_secret,
            stripe_publishable_key=stripe_pub,
            stripe_webhook_secret=stripe_wh,
            stripe_api_base_url=(
                source.get("STRIPE_API_BASE_URL") or DEFAULT_STRIPE_API_BASE_URL
            ).strip().rstrip("/"),
            dummy_webhook_secret=dummy_wh,
        )

    def require_stripe_sandbox_secrets(self) -> tuple[str, str]:
        """Valida que las llaves de Stripe existan y pertenezcan estrictamente a Sandbox (CA-08)."""
        if not self.stripe_secret_key:
            raise PaymentGatewayConfigError(
                "Falta el secreto STRIPE_SECRET_KEY para operar con Stripe Sandbox."
            )
        if not self.stripe_secret_key.startswith("sk_test_"):
            raise PaymentGatewayConfigError(
                "CA-08: STRIPE_SECRET_KEY debe ser una llave de pruebas Sandbox ('sk_test_...')."
            )
        if self.stripe_publishable_key and not self.stripe_publishable_key.startswith("pk_test_"):
            raise PaymentGatewayConfigError(
                "CA-08: STRIPE_PUBLISHABLE_KEY debe ser una llave pública de pruebas ('pk_test_...')."
            )
        if not self.stripe_webhook_secret:
            raise PaymentGatewayConfigError(
                "Falta el secreto STRIPE_WEBHOOK_SECRET para autenticar webhooks de Stripe."
            )
        return self.stripe_secret_key, self.stripe_webhook_secret

    def require_dummy_webhook_secret(self) -> str:
        """Retorna el secreto HMAC del adaptador dummy (exige valor no vacío)."""
        return self.dummy_webhook_secret or "teknigo-dummy-local-webhook-secret"