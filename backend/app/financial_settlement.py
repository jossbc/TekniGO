"""HU-36 ST-05: reparto digital/efectivo, comisión sobre total final, reembolso idempotente y alerta CA-10."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    FinancialLedgerEntry,
    OperationalSettings,
    PaymentRefund,
    RequestCancellation,
    ServiceAdjustment,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)

DEFAULT_COMMISSION_RATE = Decimal("0.10")
THIRD_FAULT_ALERT_THRESHOLD = 3
UNJUSTIFIED_FAULT_STATES = ("PENDIENTE_JUSTIFICACION", "NO_JUSTIFICADA")
CENT = Decimal("0.01")


def to_money(value) -> Decimal:
    """Política de redondeo: 2 decimales, ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def configured_commission_rate(db: Session | None = None, fallback: Decimal = DEFAULT_COMMISSION_RATE) -> Decimal:
    """Obtiene la tasa de comisión decimal (ej. 10% -> 0.10) desde OperationalSettings o usa fallback."""
    if db is not None:
        settings = db.get(OperationalSettings, 1)
        if settings is not None and settings.comision_porcentaje is not None:
            return (Decimal(str(settings.comision_porcentaje)) / Decimal("100")).quantize(Decimal("0.0001"))
    return fallback.quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class PaymentSplitInput:
    monto_base: Decimal
    monto_ajuste: Decimal
    monto_total: Decimal
    metodo_pago_base: str
    metodo_pago_ajuste: str | None
    referencia_pasarela_base: str | None
    referencia_pasarela_ajuste: str | None
    tasa_comision: Decimal


@dataclass(frozen=True)
class PaymentSplitResult:
    estrategia: str
    moneda: str
    monto_base: Decimal
    monto_ajuste: Decimal
    monto_total: Decimal
    metodo_pago_base: str
    metodo_pago_ajuste: str | None
    cobrado_digital: Decimal
    cobrado_efectivo: Decimal
    tasa_comision: Decimal
    monto_comision: Decimal
    monto_neto_tecnico: Decimal
    credito_digital_tecnico: Decimal
    deuda_comision_efectivo: Decimal

    def to_dict(self) -> dict:
        return {
            "estrategia": self.estrategia,
            "moneda": self.moneda,
            "monto_base": str(self.monto_base),
            "monto_ajuste": str(self.monto_ajuste),
            "monto_total": str(self.monto_total),
            "metodo_pago_base": self.metodo_pago_base,
            "metodo_pago_ajuste": self.metodo_pago_ajuste,
            "cobrado_digital": str(self.cobrado_digital),
            "cobrado_efectivo": str(self.cobrado_efectivo),
            "tasa_comision": str(self.tasa_comision),
            "monto_comision": str(self.monto_comision),
            "monto_neto_tecnico": str(self.monto_neto_tecnico),
            "credito_digital_tecnico": str(self.credito_digital_tecnico),
            "deuda_comision_efectivo": str(self.deuda_comision_efectivo),
        }


class PaymentSplitStrategy(ABC):
    """Contrato (OCP vía interfaz): calcula reparto digital/efectivo y comisión sobre el total final."""

    @abstractmethod
    def applies(self, data: PaymentSplitInput) -> bool: ...

    @abstractmethod
    def calculate(self, data: PaymentSplitInput) -> PaymentSplitResult: ...


class PureCashSplitStrategy(PaymentSplitStrategy):
    """Pago inicial en efectivo: todo el cobro es en efectivo y la comisión genera deuda al técnico."""

    def applies(self, data: PaymentSplitInput) -> bool:
        return data.metodo_pago_base == "efectivo"

    def calculate(self, data: PaymentSplitInput) -> PaymentSplitResult:
        comision = to_money(data.monto_total * data.tasa_comision)
        neto = to_money(data.monto_total - comision)
        return PaymentSplitResult(
            estrategia="SOLO_EFECTIVO",
            moneda="HNL",
            monto_base=data.monto_base,
            monto_ajuste=data.monto_ajuste,
            monto_total=data.monto_total,
            metodo_pago_base="efectivo",
            metodo_pago_ajuste=data.metodo_pago_ajuste,
            cobrado_digital=Decimal("0.00"),
            cobrado_efectivo=data.monto_total,
            tasa_comision=data.tasa_comision,
            monto_comision=comision,
            monto_neto_tecnico=neto,
            credito_digital_tecnico=Decimal("0.00"),
            deuda_comision_efectivo=comision,
        )


class MixedCardBaseCashAdjustmentSplitStrategy(PaymentSplitStrategy):
    """CA-07: Pago inicial con tarjeta + aumento aprobado cobrado en efectivo por la diferencia."""

    def applies(self, data: PaymentSplitInput) -> bool:
        return (
            data.metodo_pago_base == "tarjeta"
            and data.monto_ajuste > Decimal("0.00")
            and data.metodo_pago_ajuste == "efectivo"
        )

    def calculate(self, data: PaymentSplitInput) -> PaymentSplitResult:
        comision = to_money(data.monto_total * data.tasa_comision)
        neto = to_money(data.monto_total - comision)
        cobrado_digital = data.monto_base
        cobrado_efectivo = data.monto_ajuste
        if cobrado_digital >= comision:
            credito_digital = to_money(cobrado_digital - comision)
            deuda_efectivo = Decimal("0.00")
        else:
            credito_digital = Decimal("0.00")
            deuda_efectivo = to_money(comision - cobrado_digital)
        return PaymentSplitResult(
            estrategia="MIXTO_TARJETA_EFECTIVO",
            moneda="HNL",
            monto_base=data.monto_base,
            monto_ajuste=data.monto_ajuste,
            monto_total=data.monto_total,
            metodo_pago_base="tarjeta",
            metodo_pago_ajuste="efectivo",
            cobrado_digital=cobrado_digital,
            cobrado_efectivo=cobrado_efectivo,
            tasa_comision=data.tasa_comision,
            monto_comision=comision,
            monto_neto_tecnico=neto,
            credito_digital_tecnico=credito_digital,
            deuda_comision_efectivo=deuda_efectivo,
        )


class PureDigitalSplitStrategy(PaymentSplitStrategy):
    """Pago inicial con tarjeta y sin efectivo complementario (sin ajuste, reducción o diferencia con tarjeta)."""

    def applies(self, data: PaymentSplitInput) -> bool:
        return data.metodo_pago_base == "tarjeta"

    def calculate(self, data: PaymentSplitInput) -> PaymentSplitResult:
        comision = to_money(data.monto_total * data.tasa_comision)
        neto = to_money(data.monto_total - comision)
        return PaymentSplitResult(
            estrategia="SOLO_TARJETA",
            moneda="HNL",
            monto_base=data.monto_base,
            monto_ajuste=data.monto_ajuste,
            monto_total=data.monto_total,
            metodo_pago_base="tarjeta",
            metodo_pago_ajuste=data.metodo_pago_ajuste,
            cobrado_digital=data.monto_total,
            cobrado_efectivo=Decimal("0.00"),
            tasa_comision=data.tasa_comision,
            monto_comision=comision,
            monto_neto_tecnico=neto,
            credito_digital_tecnico=neto,
            deuda_comision_efectivo=Decimal("0.00"),
        )


def default_payment_split_strategies() -> tuple[PaymentSplitStrategy, ...]:
    return (
        PureCashSplitStrategy(),
        MixedCardBaseCashAdjustmentSplitStrategy(),
        PureDigitalSplitStrategy(),
    )


def calculate_payment_split(
    record,
    *,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
    strategies: Sequence[PaymentSplitStrategy] | None = None,
) -> PaymentSplitResult:
    base = to_money(getattr(record, "monto_base", None) or getattr(record, "presupuesto", 0) or 0)
    ajuste = to_money(getattr(record, "monto_ajuste", 0) or 0)
    raw_total = getattr(record, "monto_total", None)
    total = to_money(raw_total if raw_total is not None else (base + ajuste))
    metodo_base = (getattr(record, "metodo_pago", None) or "efectivo").strip().lower()
    raw_metodo_ajuste = getattr(record, "metodo_pago_ajuste", None)
    metodo_ajuste = raw_metodo_ajuste.strip().lower() if raw_metodo_ajuste else None

    data = PaymentSplitInput(
        monto_base=base,
        monto_ajuste=ajuste,
        monto_total=total,
        metodo_pago_base=metodo_base,
        metodo_pago_ajuste=metodo_ajuste,
        referencia_pasarela_base=getattr(record, "referencia_pasarela", None),
        referencia_pasarela_ajuste=getattr(record, "referencia_pasarela_ajuste", None),
        tasa_comision=commission_rate.quantize(Decimal("0.0001")),
    )
    for strategy in strategies or default_payment_split_strategies():
        if strategy.applies(data):
            return strategy.calculate(data)
    raise HTTPException(409, "No se encontró estrategia financiera para el reparto de medios.")


def record_ledger_entry_once(
    db: Session,
    *,
    clave_idempotencia: str,
    modalidad: str,
    solicitud_id: str,
    tipo_movimiento: str,
    monto_base: Decimal,
    monto_ajuste: Decimal,
    monto_total: Decimal,
    tasa_comision: Decimal,
    monto_comision: Decimal,
    metodo_pago: str,
    referencia_pasarela: str | None,
    detalle: dict,
    now: datetime | None = None,
) -> tuple[FinancialLedgerEntry, bool]:
    """Inserta un movimiento único en el libro financiero; si ya existe, lo devuelve sin duplicar."""
    existing = db.scalar(
        select(FinancialLedgerEntry).where(FinancialLedgerEntry.clave_idempotencia == clave_idempotencia)
    )
    if existing is not None:
        return existing, True

    entry = FinancialLedgerEntry(
        clave_idempotencia=clave_idempotencia,
        modalidad=modalidad,
        solicitud_id=solicitud_id,
        tipo_movimiento=tipo_movimiento,
        moneda="HNL",
        monto_base=to_money(monto_base),
        monto_ajuste=to_money(monto_ajuste),
        monto_total=to_money(monto_total),
        tasa_comision=tasa_comision.quantize(Decimal("0.0001")),
        monto_comision=to_money(monto_comision),
        metodo_pago=metodo_pago,
        referencia_pasarela=referencia_pasarela,
        detalle=dict(detalle),
        creado_en=ensure_utc_datetime(now or datetime.now(timezone.utc)),
    )
    db.add(entry)
    db.flush()
    return entry, False


def register_complementary_payment(
    db: Session,
    record,
    adjustment: ServiceAdjustment,
    *,
    metodo_pago_diferencia: str,
    referencia_pasarela_diferencia: str | None = None,
    now: datetime | None = None,
) -> dict:
    """
    CA-07: Registra de forma idempotente el cobro complementario (tarjeta o efectivo) por la diferencia
    de un aumento aprobado y asienta el movimiento en el libro financiero con la comisión sobre el total final.
    """
    if adjustment.estado != "APROBADO":
        raise HTTPException(409, "Solo un ajuste aprobado admite registro de cobro complementario.")
    diferencia = to_money(adjustment.diferencia)
    if diferencia <= Decimal("0.00"):
        raise HTTPException(409, "Solo un aumento aprobado genera diferencia a cobrar.")

    metodo = (metodo_pago_diferencia or "").strip().lower()
    if metodo not in {"tarjeta", "efectivo"}:
        raise HTTPException(400, "El método de pago de la diferencia debe ser 'tarjeta' o 'efectivo'.")

    metodo_base = (getattr(record, "metodo_pago", None) or "efectivo").strip().lower()
    if metodo_base == "efectivo" and metodo != "efectivo":
        raise HTTPException(409, "Un servicio con pago inicial en efectivo mantiene efectivo para la diferencia.")

    clean_ref = (referencia_pasarela_diferencia or "").strip() or None
    if metodo == "tarjeta" and not clean_ref:
        raise HTTPException(400, "El cobro complementario con tarjeta requiere referencia de pasarela.")
    if metodo == "efectivo":
        clean_ref = None

    existing_method = (getattr(record, "metodo_pago_ajuste", None) or "").strip().lower() or None
    if existing_method is not None and existing_method != metodo:
        raise HTTPException(409, f"El medio de pago de la diferencia ya fue registrado como '{existing_method}'.")

    record.metodo_pago_ajuste = metodo
    if clean_ref:
        record.referencia_pasarela_ajuste = clean_ref

    rate = configured_commission_rate(db)
    split = calculate_payment_split(record, commission_rate=rate)
    key = f"COBRO-DIFERENCIA-{adjustment.modalidad}-{adjustment.solicitud_id}"
    entry, reused = record_ledger_entry_once(
        db,
        clave_idempotencia=key,
        modalidad=adjustment.modalidad,
        solicitud_id=adjustment.solicitud_id,
        tipo_movimiento="COBRO_DIFERENCIA_AJUSTE",
        monto_base=split.monto_base,
        monto_ajuste=split.monto_ajuste,
        monto_total=split.monto_total,
        tasa_comision=split.tasa_comision,
        monto_comision=split.monto_comision,
        metodo_pago=metodo,
        referencia_pasarela=record.referencia_pasarela_ajuste,
        detalle=split.to_dict(),
        now=now,
    )
    return {
        "reparto": split.to_dict(),
        "movimiento_libro_id": entry.id,
        "clave_idempotencia": entry.clave_idempotencia,
        "reintento_idempotente": reused,
    }


class RefundGatewayPort(ABC):
    """Puerto (OCP/DIP vía interfaz) para iniciar y confirmar devoluciones con la pasarela."""

    @abstractmethod
    def initiate_refund(self, *, modalidad: str, solicitud_id: str, amount: Decimal, origin_ref: str) -> str: ...


class DeterministicRefundGatewayAdapter(RefundGatewayPort):
    """Adaptador determinista e idempotente para la pasarela de pagos."""

    def initiate_refund(self, *, modalidad: str, solicitud_id: str, amount: Decimal, origin_ref: str) -> str:
        token = uuid.uuid5(uuid.NAMESPACE_OID, f"refund-{modalidad}-{solicitud_id}-{amount}-{origin_ref}").hex[:12].upper()
        return f"TKGO-RFND-{token}"


def serialize_refund(refund: PaymentRefund) -> dict:
    return {
        "id": refund.id,
        "solicitud_id": refund.solicitud_id,
        "modalidad": refund.modalidad,
        "moneda": "HNL",
        "monto_base_reembolsado": str(to_money(refund.monto_base_reembolsado)),
        "monto_ajuste_reembolsado": str(to_money(refund.monto_ajuste_reembolsado)),
        "monto_reembolso": str(to_money(refund.monto_reembolso)),
        "porcentaje_reembolsado": 100,
        "monto_comision": "0.00",
        "referencia_pasarela_origen": refund.referencia_pasarela_origen,
        "referencia_reembolso_pasarela": refund.referencia_reembolso_pasarela,
        "estado_pasarela": refund.estado_pasarela,
        "iniciado_en": format_tegucigalpa_iso(refund.iniciado_en),
        "confirmado_en": format_tegucigalpa_iso(refund.confirmado_en),
    }


def collect_digital_payments(record) -> tuple[Decimal, Decimal, list[str]]:
    """Devuelve (base_digital, ajuste_digital, referencias_pasarela) efectivamente cobrados."""
    base_digital = Decimal("0.00")
    ajuste_digital = Decimal("0.00")
    refs: list[str] = []

    metodo_base = (getattr(record, "metodo_pago", None) or "").strip().lower()
    ref_base = (getattr(record, "referencia_pasarela", None) or "").strip()
    if metodo_base == "tarjeta" and ref_base:
        base_digital = to_money(getattr(record, "monto_base", None) or getattr(record, "presupuesto", 0) or 0)
        refs.append(ref_base)

    metodo_ajuste = (getattr(record, "metodo_pago_ajuste", None) or "").strip().lower()
    ref_ajuste = (getattr(record, "referencia_pasarela_ajuste", None) or "").strip()
    monto_ajuste = to_money(getattr(record, "monto_ajuste", 0) or 0)
    if metodo_ajuste == "tarjeta" and ref_ajuste and monto_ajuste > Decimal("0.00"):
        ajuste_digital = monto_ajuste
        refs.append(ref_ajuste)

    return base_digital, ajuste_digital, refs


def count_unjustified_faults(db: Session, user_uid: str | None) -> int:
    """CA-09 / CA-10: cuenta las faltas no justificadas vigentes de un usuario (excluye JUSTIFICADA)."""
    if not user_uid:
        return 0
    total = db.scalar(
        select(func.count())
        .select_from(RequestCancellation)
        .where(
            RequestCancellation.responsable_uid == user_uid,
            RequestCancellation.genera_incumplimiento.is_(True),
            RequestCancellation.estado_incumplimiento.in_(UNJUSTIFIED_FAULT_STATES),
        )
    )
    return int(total or 0)


def check_third_unjustified_fault_alert(db: Session, user_uid: str | None) -> dict:
    """CA-10: la tercera falta no justificada genera alerta administrativa; la suspensión requiere HU-52."""
    faltas = count_unjustified_faults(db, user_uid)
    return {
        "responsable_uid": user_uid,
        "faltas_no_justificadas_acumuladas": faltas,
        "alerta_administrativa_activa": faltas >= THIRD_FAULT_ALERT_THRESHOLD,
        "requiere_decision_hu52": faltas >= THIRD_FAULT_ALERT_THRESHOLD,
        "suspension_automatica_aplicada": False,
    }


class RefundOnCancellationObserver:
    """
    Observador de cancelación (ST-05):
    - CA-08: inicia devolución idempotente del 100% digital efectivamente cobrado, sin comisión.
    - CA-10: garantiza que una cancelación nunca habilite reseñas.
    """

    def __init__(self, gateway: RefundGatewayPort | None = None):
        self._gateway = gateway or DeterministicRefundGatewayAdapter()

    def on_cancelled(self, db: Session, record, cancellation: RequestCancellation) -> None:
        # CA-10: una cancelación nunca habilita reseñas
        tracking = dict(getattr(record, "seguimiento_recepcion", None) or {})
        if tracking:
            tracking["resenas_habilitadas"] = False
            record.seguimiento_recepcion = tracking

        existing = db.scalar(
            select(PaymentRefund).where(
                PaymentRefund.modalidad == cancellation.modalidad,
                PaymentRefund.solicitud_id == cancellation.solicitud_id,
            )
        )
        if existing is not None:
            return

        base_digital, ajuste_digital, refs = collect_digital_payments(record)
        total_digital = to_money(base_digital + ajuste_digital)
        if total_digital <= Decimal("0.00"):
            return

        origin_ref = ",".join(refs)
        refund_ref = self._gateway.initiate_refund(
            modalidad=cancellation.modalidad,
            solicitud_id=cancellation.solicitud_id,
            amount=total_digital,
            origin_ref=origin_ref,
        )
        moment = ensure_utc_datetime(cancellation.cancelada_en)
        refund = PaymentRefund(
            modalidad=cancellation.modalidad,
            solicitud_id=cancellation.solicitud_id,
            cliente_uid=record.cliente_id,
            monto_base_reembolsado=base_digital,
            monto_ajuste_reembolsado=ajuste_digital,
            monto_reembolso=total_digital,
            monto_comision=Decimal("0.00"),
            referencia_pasarela_origen=origin_ref,
            referencia_reembolso_pasarela=refund_ref,
            estado_pasarela="EN_PROCESO_PASARELA",
            iniciado_en=moment,
        )
        db.add(refund)
        db.flush()

        record_ledger_entry_once(
            db,
            clave_idempotencia=f"REEMBOLSO-{cancellation.modalidad}-{cancellation.solicitud_id}",
            modalidad=cancellation.modalidad,
            solicitud_id=cancellation.solicitud_id,
            tipo_movimiento="DEVOLUCION_CANCELACION",
            monto_base=base_digital,
            monto_ajuste=ajuste_digital,
            monto_total=total_digital,
            tasa_comision=Decimal("0.0000"),
            monto_comision=Decimal("0.00"),
            metodo_pago="tarjeta",
            referencia_pasarela=refund_ref,
            detalle=serialize_refund(refund),
            now=moment,
        )


def get_refund_for_request(db: Session, modalidad: str, solicitud_id: str) -> dict | None:
    refund = db.scalar(
        select(PaymentRefund).where(
            PaymentRefund.modalidad == modalidad,
            PaymentRefund.solicitud_id == solicitud_id,
        )
    )
    return serialize_refund(refund) if refund is not None else None


def confirm_gateway_refund(
    db: Session,
    modalidad: str,
    solicitud_id: str,
    *,
    referencia_confirmacion: str | None = None,
    now: datetime | None = None,
) -> dict:
    """CA-08: confirma de forma idempotente la devolución emitida por la pasarela."""
    refund = db.scalar(
        select(PaymentRefund)
        .where(
            PaymentRefund.modalidad == modalidad,
            PaymentRefund.solicitud_id == solicitud_id,
        )
        .with_for_update()
    )
    if refund is None:
        raise HTTPException(404, "La solicitud no tiene una devolución digital iniciada.")

    if refund.estado_pasarela == "CONFIRMADO_PASARELA":
        data = serialize_refund(refund)
        data["reintento_idempotente"] = True
        return data

    moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
    if referencia_confirmacion and referencia_confirmacion.strip():
        refund.referencia_reembolso_pasarela = referencia_confirmacion.strip()
    refund.estado_pasarela = "CONFIRMADO_PASARELA"
    refund.confirmado_en = moment

    record_ledger_entry_once(
        db,
        clave_idempotencia=f"REEMBOLSO-CONFIRMADO-{modalidad}-{solicitud_id}",
        modalidad=modalidad,
        solicitud_id=solicitud_id,
        tipo_movimiento="DEVOLUCION_CONFIRMADA_PASARELA",
        monto_base=to_money(refund.monto_base_reembolsado),
        monto_ajuste=to_money(refund.monto_ajuste_reembolsado),
        monto_total=to_money(refund.monto_reembolso),
        tasa_comision=Decimal("0.0000"),
        monto_comision=Decimal("0.00"),
        metodo_pago="tarjeta",
        referencia_pasarela=refund.referencia_reembolso_pasarela,
        detalle=serialize_refund(refund),
        now=moment,
    )
    db.commit()
    data = serialize_refund(refund)
    data["reintento_idempotente"] = False
    return data