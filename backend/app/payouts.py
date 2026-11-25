"""HU-43: reglas puras de cuenta bancaria privada, desembolsos, reservas y saldos.

Política de redondeo: todos los importes usan lempiras (HNL) con aritmética
`Decimal` a 2 decimales y redondeo ROUND_HALF_UP (0.005 -> 0.01). Nunca se usa `float`.
"""
import base64
import hashlib
import re
from decimal import Decimal

from app.settlements import SettlementValidationError, money, normalize_reference
from app.storage import decrypt_bytes, encrypt_bytes

PENDIENTE = "PENDIENTE"
PAGADA = "PAGADA"
RECHAZADA = "RECHAZADA"

LEDGER_RESERVA = "RESERVA_DESEMBOLSO"
LEDGER_DEBITO = "DEBITO_DESEMBOLSO"
LEDGER_LIBERACION = "LIBERACION_RESERVA"
LEDGER_TYPES = (LEDGER_RESERVA, LEDGER_DEBITO, LEDGER_LIBERACION)

PAYOUT_STATES = (PENDIENTE, PAGADA, RECHAZADA)
FINAL_PAYOUT_STATES = (PAGADA, RECHAZADA)
ALLOWED_PAYOUT_TRANSITIONS = {
    PENDIENTE: frozenset({PAGADA, RECHAZADA}),
    PAGADA: frozenset(),
    RECHAZADA: frozenset(),
}

ACCOUNT_TYPES = ("AHORRO", "CHEQUES")
DEFAULT_MINIMUM_WITHDRAWAL = Decimal("200.00")
MIN_ACCOUNT_DIGITS = 8
MAX_ACCOUNT_DIGITS = 24
_CENT = Decimal("0.01")


class PayoutValidationError(ValueError):
    """Dato de cuenta bancaria o desembolso inválido."""


def validate_bank_name(value: str | None) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if not (2 <= len(clean) <= 80):
        raise PayoutValidationError("Indica el nombre del banco (entre 2 y 80 caracteres)")
    return clean


def validate_account_type(value: str | None) -> str:
    clean = (value or "").strip().upper()
    if clean not in ACCOUNT_TYPES:
        raise PayoutValidationError("Tipo de cuenta inválido. Usa 'AHORRO' o 'CHEQUES'.")
    return clean


def validate_holder_name(value: str | None) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if not (3 <= len(clean) <= 150):
        raise PayoutValidationError("Indica el nombre completo del titular de la cuenta")
    return clean


def normalize_account_number(value: str | None) -> str:
    """Elimina espacios y guiones; valida que sean solo dígitos en el rango permitido."""
    clean = re.sub(r"[\s\-]+", "", value or "")
    if not clean.isdigit() or not (MIN_ACCOUNT_DIGITS <= len(clean) <= MAX_ACCOUNT_DIGITS):
        raise PayoutValidationError(
            f"El número de cuenta debe tener entre {MIN_ACCOUNT_DIGITS} y {MAX_ACCOUNT_DIGITS} dígitos"
        )
    return clean


def mask_account_number(clean_number: str) -> tuple[str, str]:
    """Devuelve (ultimos_cuatro, cuenta_enmascarada) para consultas visibles (CA-07)."""
    normalized = normalize_account_number(clean_number)
    last4 = normalized[-4:]
    return last4, f"****{last4}"


def hash_account_number(clean_number: str) -> str:
    normalized = normalize_account_number(clean_number)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def encrypt_account_number(clean_number: str, *, secret_key: str | None = None) -> str:
    """Cifra el número de cuenta en reposo con AES-GCM/autenticado de storage.py (CA-07)."""
    normalized = normalize_account_number(clean_number)
    encrypted = encrypt_bytes(normalized.encode("utf-8"), secret_key=secret_key)
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_account_number(encoded_cipher: str, *, secret_key: str | None = None) -> str:
    """Descifra el número de cuenta solo para el trámite administrativo autorizado (CA-07)."""
    try:
        payload = base64.b64decode(encoded_cipher.encode("ascii"), validate=True)
        return decrypt_bytes(payload, secret_key=secret_key).decode("utf-8")
    except Exception as exc:
        raise PayoutValidationError("No se pudo descifrar el número de cuenta bancaria") from exc


def normalize_transfer_reference(value: str | None) -> str:
    try:
        return normalize_reference(value)
    except SettlementValidationError as exc:
        raise PayoutValidationError(str(exc)) from None


def can_transition_payout(current: str, target: str) -> bool:
    return target in ALLOWED_PAYOUT_TRANSITIONS.get(current, frozenset())


def validate_payout_amount(
    value,
    *,
    minimum_withdrawal: Decimal,
    available_balance: Decimal,
) -> Decimal:
    """CA-01 / CA-02: importe positivo, >= mínimo configurado y <= saldo libre."""
    try:
        amount = money(value)
    except SettlementValidationError as exc:
        raise PayoutValidationError(str(exc)) from None

    minimum = money(minimum_withdrawal)
    available = max(money(available_balance), Decimal("0.00"))
    if amount <= Decimal("0.00"):
        raise PayoutValidationError("El importe a retirar debe ser mayor que cero")
    if amount < minimum:
        raise PayoutValidationError(f"El importe mínimo de retiro es L {minimum:.2f}")
    if amount > available:
        raise PayoutValidationError(f"El importe supera tu saldo libre disponible (L {available:.2f})")
    return amount


def get_minimum_withdrawal(db) -> Decimal:
    """Lee el mínimo de retiro configurado en `configuracion_operativa` (fallback L 200.00)."""
    from app.models import OperationalSettings

    row = db.get(OperationalSettings, 1)
    if row is not None and row.minimo_retiro is not None and row.minimo_retiro > 0:
        return money(row.minimo_retiro)
    return DEFAULT_MINIMUM_WITHDRAWAL


def digital_credits_generated(db, user) -> Decimal:
    """Fondos digitales acreditados por servicios cerrados con pago digital.

    PROVISIONAL (HU-43): hoy vive en `seguimiento_recepcion.efecto_financiero.credito_digital_acreditado`
    de `service_reception.py`. Cuando HU-40/41 integren el libro financiero, reemplazar solo esta función.
    """
    from sqlalchemy import select

    from app.models import DirectRequest, OpenRequest

    identities = {str(user.id), user.firebase_uid}
    total = Decimal("0.00")
    for model in (DirectRequest, OpenRequest):
        rows = db.scalars(
            select(model).where(model.tecnico_id.in_(identities), model.seguimiento_recepcion.is_not(None))
        ).all()
        for row in rows:
            effect = (row.seguimiento_recepcion or {}).get("efecto_financiero") or {}
            raw_credit = effect.get("credito_digital_acreditado")
            if raw_credit not in (None, ""):
                total += money(str(raw_credit))
    return total


def payout_idempotency_key(order_id, movement_type: str) -> str:
    """Clave determinista por orden y tipo de movimiento para garantizar débito/liberación únicos."""
    if movement_type not in LEDGER_TYPES:
        raise PayoutValidationError("Tipo de movimiento de libro inválido")
    return f"{movement_type}:{order_id}"


def record_payout_ledger_entry(
    db,
    *,
    order,
    movement_type: str,
    saldo_contable: Decimal,
    saldo_reservado: Decimal,
    saldo_libre: Decimal,
    responsable_id,
    referencia_transferencia: str | None = None,
    created_at=None,
):
    """HU-43 ST-05: registra un movimiento único e inmutable en `movimientos_libro_desembolsos`."""
    from datetime import datetime, timezone
    import uuid

    from app.models import PayoutLedgerEntry

    now = created_at if created_at is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    entry = PayoutLedgerEntry(
        id=uuid.uuid4(),
        clave_idempotencia=payout_idempotency_key(order.id, movement_type),
        orden_id=order.id,
        tecnico_id=order.tecnico_id,
        tipo_movimiento=movement_type,
        moneda="HNL",
        importe=money(order.importe),
        referencia_transferencia=referencia_transferencia,
        saldo_contable_resultante=max(money(saldo_contable), Decimal("0.00")),
        saldo_reservado_resultante=max(money(saldo_reservado), Decimal("0.00")),
        saldo_libre_resultante=max(money(saldo_libre), Decimal("0.00")),
        responsable_id=responsable_id,
        creado_en=now,
    )
    db.add(entry)
    return entry


def paid_payouts_total(db, user) -> Decimal:
    """Suma los débitos únicos registrados en el libro financiero (con respaldo a órdenes PAGADA)."""
    from sqlalchemy import func, select

    from app.models import PayoutLedgerEntry, PayoutOrder

    debited = db.scalar(
        select(func.coalesce(func.sum(PayoutLedgerEntry.importe), 0)).where(
            PayoutLedgerEntry.tecnico_id == user.id,
            PayoutLedgerEntry.tipo_movimiento == LEDGER_DEBITO,
        )
    )
    debited_dec = money(debited or 0)
    if debited_dec > Decimal("0.00"):
        return debited_dec

    paid = db.scalar(
        select(func.coalesce(func.sum(PayoutOrder.importe), 0)).where(
            PayoutOrder.tecnico_id == user.id,
            PayoutOrder.estado == PAGADA,
        )
    )
    return money(paid or 0)


def reserved_balance(db, user) -> Decimal:
    """Suma de importes reservados en órdenes PENDIENTES (CA-03)."""
    from sqlalchemy import func, select

    from app.models import PayoutOrder

    reserved = db.scalar(
        select(func.coalesce(func.sum(PayoutOrder.importe_reservado), 0)).where(
            PayoutOrder.tecnico_id == user.id,
            PayoutOrder.estado == PENDIENTE,
        )
    )
    return money(reserved or 0)


def accounting_balance(db, user) -> Decimal:
    """Saldo contable = créditos digitales generados - desembolsos ya pagados (CA-03)."""
    return max((digital_credits_generated(db, user) - paid_payouts_total(db, user)).quantize(_CENT), Decimal("0.00"))


def available_balance(db, user) -> Decimal:
    """Saldo libre = saldo contable - saldo reservado en órdenes pendientes (CA-01, CA-03, CA-06)."""
    return max((accounting_balance(db, user) - reserved_balance(db, user)).quantize(_CENT), Decimal("0.00"))
