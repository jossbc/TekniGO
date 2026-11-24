"""HU-42: reglas puras de liquidación de comisiones (estados, referencias, dinero).

Política de redondeo: todos los importes se expresan en lempiras (HNL) con
aritmética `Decimal` y 2 decimales, redondeo ROUND_HALF_UP (0.005 -> 0.01).
Nunca se usa `float`.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PENDIENTE = "PENDIENTE"
APROBADA = "APROBADA"
RECHAZADA = "RECHAZADA"
EN_CONCILIACION = "EN_CONCILIACION"

SETTLEMENT_STATES = (PENDIENTE, APROBADA, RECHAZADA, EN_CONCILIACION)
# Estados que "consumen" la referencia y el comprobante (CA-06). Una liquidación
# rechazada los libera para que el técnico pueda corregir y reenviar.
ACTIVE_STATES = (PENDIENTE, APROBADA, EN_CONCILIACION)
FINAL_STATES = (APROBADA, RECHAZADA, EN_CONCILIACION)
# Solo una liquidación pendiente puede decidirse; las decisiones repetidas no
# repiten efectos (CA-06).
ALLOWED_TRANSITIONS = {
    PENDIENTE: frozenset({APROBADA, RECHAZADA, EN_CONCILIACION}),
    APROBADA: frozenset(),
    RECHAZADA: frozenset(),
    EN_CONCILIACION: frozenset(),
}

MAX_AMOUNT = Decimal("99999999.99")  # límite de Numeric(10, 2)
MIN_REFERENCE_LENGTH = 4
MAX_REFERENCE_LENGTH = 64
_REFERENCE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/.]*$")
_CENT = Decimal("0.01")


class SettlementValidationError(ValueError):
    """Dato de liquidación inválido."""


def money(value) -> Decimal:
    """Convierte a lempiras con 2 decimales (ROUND_HALF_UP). Rechaza float, NaN e infinitos."""
    if isinstance(value, float) or isinstance(value, bool):
        raise SettlementValidationError("El importe debe enviarse como decimal, no como número flotante")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise SettlementValidationError("Importe inválido") from None
    if not amount.is_finite():
        raise SettlementValidationError("Importe inválido")
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def validate_amount(value) -> Decimal:
    amount = money(value)
    if amount <= 0:
        raise SettlementValidationError("El importe debe ser mayor que cero")
    if amount > MAX_AMOUNT:
        raise SettlementValidationError("El importe supera el máximo permitido")
    return amount


def normalize_reference(value: str | None) -> str:
    """Recorta espacios y pasa a mayúsculas: 'ab-1 ' y 'AB-1' son la misma referencia."""
    clean = re.sub(r"\s+", "", value or "").upper()
    if not (MIN_REFERENCE_LENGTH <= len(clean) <= MAX_REFERENCE_LENGTH):
        raise SettlementValidationError(
            f"La referencia debe tener entre {MIN_REFERENCE_LENGTH} y {MAX_REFERENCE_LENGTH} caracteres"
        )
    if not _REFERENCE_RE.match(clean):
        raise SettlementValidationError("La referencia solo admite letras, números y - _ / .")
    return clean


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def split_applied_amount(verified: Decimal, outstanding_debt: Decimal) -> tuple[Decimal, Decimal]:
    """Devuelve (aplicado, excedente). Aplica solo hasta la deuda; el sobrante no es crédito (CA-05)."""
    verified = money(verified)
    debt = max(money(outstanding_debt), Decimal("0.00"))
    applied = min(verified, debt)
    return applied, (verified - applied).quantize(_CENT)


def commission_debt_generated(db, user) -> Decimal:
    """Deuda de comisión en efectivo generada por los servicios cerrados del técnico.

    PROVISIONAL (HU-42): hoy esta deuda solo vive en el efecto financiero que
    `service_reception` guarda en cada solicitud. Cuando HU-40/41 expongan el
    libro financiero, reemplazar este cálculo (es el único punto de acoplamiento).
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
            raw_debt = effect.get("deuda_comision_efectivo")
            if raw_debt not in (None, ""):
                # service_reception.py (Sprint 2) persiste JSON numérico; str() evita float binario en money().
                total += money(str(raw_debt))
    return total


def outstanding_debt(db, user) -> Decimal:
    """Deuda vigente = comisiones generadas - importes ya aplicados por liquidaciones aprobadas."""
    from sqlalchemy import func, select

    from app.models import CommissionSettlement

    applied = db.scalar(
        select(func.coalesce(func.sum(CommissionSettlement.importe_aplicado), 0)).where(
            CommissionSettlement.tecnico_id == user.id,
            CommissionSettlement.estado.in_((APROBADA, EN_CONCILIACION)),
        )
    )
    return max(commission_debt_generated(db, user) - money(applied or 0), Decimal("0.00"))


def apply_regularization_if_settled(
    db,
    user,
    profile,
    *,
    remaining_debt: Decimal,
    admin_id,
    reference: str,
) -> tuple[int, bool]:
    """HU-42 ST-05 (CA-04, CA-05, CA-07): desbloqueo tras regularización total.

    Solo cuando la deuda vigente llega exactamente a 0.00 (en un pago o en varios
    parciales acumulados) se reinicia el contador de efectivo y se retira
    `restriccion_financiera`. Nunca modifica `suspendido` ni `estado_verificacion`.
    Devuelve `(contador_posterior, restriccion_posterior)`.
    """
    if profile is None:
        return 0, False

    counter = int(profile.servicios_efectivo_sin_liquidar or 0)
    restricted = bool(profile.restriccion_financiera)
    if money(remaining_debt) > Decimal("0.00"):
        return counter, restricted

    from app.models import TechnicianStatusAudit
    from app.technician_activation import is_operationally_enabled

    previous_enabled = is_operationally_enabled(user, profile)
    profile.servicios_efectivo_sin_liquidar = 0
    profile.restriccion_financiera = False

    if restricted:
        new_enabled = is_operationally_enabled(user, profile)
        db.add(
            TechnicianStatusAudit(
                usuario_id=profile.usuario_id,
                estado_anterior=profile.estado_verificacion,
                estado_nuevo=profile.estado_verificacion,
                habilitado_anterior=previous_enabled,
                habilitado_nuevo=new_enabled,
                causa=f"Restricción financiera retirada tras regularización total ({reference}).",
                actor_usuario_id=admin_id,
                proceso_responsable="LIQUIDACION_COMISIONES_ADMIN",
            )
        )

    return 0, False
