"""HU-43 ST-02: cuenta bancaria privada del técnico y solicitud de desembolso con bloqueo transaccional."""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import payouts as rules
from app import settlements as settlement_rules
from app.database import get_db
from app.identity_documents import _get_user, _require_admin
from app.models import PayoutLedgerEntry, PayoutOrder, PayoutOrderAudit, TechnicianBankAccount, User
from app.security import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store, private"}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _require_technician(claims: dict, db: Session, *, lock: bool = False) -> User:
    user = _get_user(claims, db, lock=lock)
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Solo los técnicos pueden gestionar retiros de ganancias")
    return user


def _serialize_bank_account_masked(account: TechnicianBankAccount | None) -> dict | None:
    """CA-07: nunca expone el número de cuenta completo ni el cifrado en consultas visibles."""
    if account is None:
        return None
    return {
        "id": str(account.id),
        "banco": account.banco,
        "tipo_cuenta": account.tipo_cuenta,
        "titular_nombre": account.titular_nombre,
        "ultimos_cuatro": account.ultimos_cuatro,
        "cuenta_enmascarada": account.cuenta_enmascarada,
        "actualizado_en": _iso(account.actualizado_en),
    }


def _serialize_ledger_entry(entry: PayoutLedgerEntry) -> dict:
    return {
        "id": str(entry.id),
        "clave_idempotencia": entry.clave_idempotencia,
        "orden_id": str(entry.orden_id),
        "tecnico_id": str(entry.tecnico_id),
        "tipo_movimiento": entry.tipo_movimiento,
        "moneda": entry.moneda,
        "importe": str(entry.importe),
        "referencia_transferencia": entry.referencia_transferencia,
        "saldo_contable_resultante": str(entry.saldo_contable_resultante),
        "saldo_reservado_resultante": str(entry.saldo_reservado_resultante),
        "saldo_libre_resultante": str(entry.saldo_libre_resultante),
        "responsable_id": str(entry.responsable_id),
        "creado_en": _iso(entry.creado_en),
    }


def _serialize_order_for_owner(order: PayoutOrder) -> dict:
    return {
        "id": str(order.id),
        "importe": str(order.importe),
        "importe_reservado": str(order.importe_reservado),
        "minimo_aplicado": str(order.minimo_aplicado),
        "estado": order.estado,
        "banco": order.banco_snapshot,
        "tipo_cuenta": order.tipo_cuenta_snapshot,
        "cuenta_enmascarada": order.cuenta_enmascarada_snapshot,
        "referencia_transferencia": order.referencia_transferencia,
        "motivo_rechazo": order.motivo_rechazo,
        "observacion": order.observacion,
        "creado_en": _iso(order.creado_en),
        "revisado_en": _iso(order.revisado_en),
    }


class BankAccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    banco: str
    tipo_cuenta: str
    titular_nombre: str
    numero_cuenta: str


class PayoutRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    importe: Any


def save_my_bank_account(
    payload: BankAccountInput,
    claims: dict,
    db: Session,
) -> dict:
    """CA-01 / CA-07: registra o reemplaza la cuenta bancaria personal cifrada en reposo."""
    user = _require_technician(claims, db, lock=True)
    try:
        banco = rules.validate_bank_name(payload.banco)
        tipo_cuenta = rules.validate_account_type(payload.tipo_cuenta)
        titular = rules.validate_holder_name(payload.titular_nombre)
        clean_number = rules.normalize_account_number(payload.numero_cuenta)
        last4, masked = rules.mask_account_number(clean_number)
        cipher = rules.encrypt_account_number(clean_number)
        digest = rules.hash_account_number(clean_number)
    except rules.PayoutValidationError as exc:
        raise HTTPException(400, str(exc)) from None

    # Desactivar cuenta activa anterior para preservar trazabilidad de órdenes previas.
    previous_active = db.scalars(
        select(TechnicianBankAccount).where(
            TechnicianBankAccount.tecnico_id == user.id,
            TechnicianBankAccount.activa == True,  # noqa: E712
        )
    ).all()
    for prev in previous_active:
        prev.activa = False
    db.flush()

    account = TechnicianBankAccount(
        id=uuid.uuid4(),
        tecnico_id=user.id,
        banco=banco,
        tipo_cuenta=tipo_cuenta,
        titular_nombre=titular,
        numero_cuenta_cifrado=cipher,
        ultimos_cuatro=last4,
        cuenta_enmascarada=masked,
        numero_cuenta_hash=digest,
        activa=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"cuenta_bancaria": _serialize_bank_account_masked(account)}


def get_my_payout_summary(claims: dict, db: Session) -> dict:
    user = _require_technician(claims, db)
    account = db.scalar(
        select(TechnicianBankAccount).where(
            TechnicianBankAccount.tecnico_id == user.id,
            TechnicianBankAccount.activa == True,  # noqa: E712
        )
    )
    orders = db.scalars(
        select(PayoutOrder)
        .where(PayoutOrder.tecnico_id == user.id)
        .order_by(PayoutOrder.creado_en.desc())
    ).all()
    accounting = rules.accounting_balance(db, user)
    reserved = rules.reserved_balance(db, user)
    available = max(accounting - reserved, Decimal("0.00"))
    debt = settlement_rules.outstanding_debt(db, user)
    minimum = rules.get_minimum_withdrawal(db)

    return {
        "moneda": "HNL",
        "saldo_contable": str(accounting),
        "saldo_reservado": str(reserved),
        "saldo_libre": str(available),
        "deuda_vigente": str(debt),
        "minimo_retiro": str(minimum),
        "cuenta_bancaria": _serialize_bank_account_masked(account),
        "ordenes": [_serialize_order_for_owner(o) for o in orders],
    }


def request_payout(
    payload: PayoutRequestInput,
    claims: dict,
    db: Session,
) -> dict:
    """HU-43 ST-02 (CA-01, CA-02, CA-03, CA-06): valida deuda=0, mínimo y saldo libre bajo FOR UPDATE."""
    # Bloqueo pesimista sobre el técnico para serializar solicitudes concurrentes (CA-06).
    user = _require_technician(claims, db, lock=True)

    account = db.scalar(
        select(TechnicianBankAccount)
        .where(
            TechnicianBankAccount.tecnico_id == user.id,
            TechnicianBankAccount.activa == True,  # noqa: E712
        )
        .with_for_update()
    )
    if account is None:
        raise HTTPException(400, "Debes registrar una cuenta bancaria personal antes de solicitar un desembolso")

    debt = settlement_rules.outstanding_debt(db, user)
    if debt > Decimal("0.00"):
        raise HTTPException(409, f"Tienes comisiones pendientes por L {debt:.2f}. Liquida tu deuda a cero antes de retirar.")

    # Bloquea también órdenes pendientes existentes del técnico para lectura consistente de reservas.
    db.scalars(
        select(PayoutOrder.id)
        .where(PayoutOrder.tecnico_id == user.id, PayoutOrder.estado == rules.PENDIENTE)
        .with_for_update()
    ).all()

    minimum = rules.get_minimum_withdrawal(db)
    accounting = rules.accounting_balance(db, user)
    reserved = rules.reserved_balance(db, user)
    available = max(accounting - reserved, Decimal("0.00"))

    try:
        amount = rules.validate_payout_amount(
            payload.importe,
            minimum_withdrawal=minimum,
            available_balance=available,
        )
    except rules.PayoutValidationError as exc:
        raise HTTPException(400, str(exc)) from None

    # CA-03: genera orden PENDIENTE y reserva el importe; el saldo contable no se debita todavía.
    order = PayoutOrder(
        id=uuid.uuid4(),
        tecnico_id=user.id,
        cuenta_bancaria_id=account.id,
        importe=amount,
        importe_reservado=amount,
        minimo_aplicado=minimum,
        estado=rules.PENDIENTE,
        banco_snapshot=account.banco,
        tipo_cuenta_snapshot=account.tipo_cuenta,
        cuenta_enmascarada_snapshot=account.cuenta_enmascarada,
    )
    new_reserved = (reserved + amount).quantize(Decimal("0.01"))
    new_available = max(accounting - new_reserved, Decimal("0.00"))
    db.add(order)
    db.flush()
    ledger_entry = rules.record_payout_ledger_entry(
        db,
        order=order,
        movement_type=rules.LEDGER_RESERVA,
        saldo_contable=accounting,
        saldo_reservado=new_reserved,
        saldo_libre=new_available,
        responsable_id=user.id,
    )
    db.commit()
    db.refresh(order)
    db.refresh(ledger_entry)

    logger.info("Orden de desembolso creada: tecnico=%s orden=%s importe=%s", user.id, order.id, order.importe)
    return {
        "orden": _serialize_order_for_owner(order),
        "saldos": {
            "moneda": "HNL",
            "saldo_contable": str(accounting),
            "saldo_reservado": str(new_reserved),
            "saldo_libre": str(new_available),
        },
        "movimiento_libro": _serialize_ledger_entry(ledger_entry),
    }


@router.get("/users/me/payouts")
def my_payouts_endpoint(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers.update(NO_STORE)
    return get_my_payout_summary(claims, db)


@router.put("/users/me/bank-account")
def save_my_bank_account_endpoint(
    payload: BankAccountInput,
    response: Response,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    return save_my_bank_account(payload, claims, db)


@router.post("/users/me/payouts", status_code=201)
def request_payout_endpoint(
    payload: PayoutRequestInput,
    response: Response,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    return request_payout(payload, claims, db)


def _serialize_order_for_admin(order: PayoutOrder, tech: User | None, reviewer: User | None) -> dict:
    # CA-07: en listados visibles la cuenta siempre aparece enmascarada.
    return {
        **_serialize_order_for_owner(order),
        "tecnico_id": str(order.tecnico_id),
        "tecnico_nombre": tech.nombre_completo if tech else None,
        "tecnico_telefono": tech.telefono if tech else None,
        "revisado_por": str(order.revisado_por) if order.revisado_por else None,
        "revisado_por_nombre": reviewer.nombre_completo if reviewer else None,
    }


def _serialize_payout_audit(entry: PayoutOrderAudit, reviewer: User | None) -> dict:
    return {
        "id": str(entry.id),
        "orden_id": str(entry.orden_id),
        "tecnico_id": str(entry.tecnico_id),
        "accion": entry.accion,
        "estado_anterior": entry.estado_anterior,
        "estado_nuevo": entry.estado_nuevo,
        "importe": str(entry.importe),
        "referencia_transferencia": entry.referencia_transferencia,
        "motivo_rechazo": entry.motivo_rechazo,
        "observacion": entry.observacion,
        "saldo_contable_anterior": str(entry.saldo_contable_anterior),
        "saldo_contable_posterior": str(entry.saldo_contable_posterior),
        "saldo_reservado_anterior": str(entry.saldo_reservado_anterior),
        "saldo_reservado_posterior": str(entry.saldo_reservado_posterior),
        "saldo_libre_anterior": str(entry.saldo_libre_anterior),
        "saldo_libre_posterior": str(entry.saldo_libre_posterior),
        "responsable_id": str(entry.responsable_id),
        "responsable_nombre": reviewer.nombre_completo if reviewer else None,
        "creado_en": _iso(entry.creado_en),
    }


class PayoutDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    referencia_transferencia: str | None = None
    motivo_rechazo: str | None = None
    observacion: str | None = None


def list_admin_payouts(claims: dict, db: Session, estado: str | None = None) -> dict:
    _require_admin(claims, db)
    query = select(PayoutOrder).order_by(PayoutOrder.creado_en.asc())
    if estado:
        clean = estado.strip().upper()
        if clean not in rules.PAYOUT_STATES:
            raise HTTPException(400, "Estado de desembolso inválido")
        query = query.where(PayoutOrder.estado == clean)
    rows = db.scalars(query).all()
    ids = {r.tecnico_id for r in rows} | {r.revisado_por for r in rows if r.revisado_por}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {
        "moneda": "HNL",
        "ordenes": [_serialize_order_for_admin(r, users.get(r.tecnico_id), users.get(r.revisado_por)) for r in rows],
    }


def get_admin_payout_bank_details(order_id: uuid.UUID, claims: dict, db: Session) -> dict:
    """CA-07: revela la cuenta bancaria descifrada únicamente a administración dentro del trámite autorizado."""
    admin = _require_admin(claims, db)
    order = db.get(PayoutOrder, order_id)
    if order is None:
        raise HTTPException(404, "No se encontró la orden de desembolso")
    account = db.get(TechnicianBankAccount, order.cuenta_bancaria_id)
    if account is None:
        raise HTTPException(404, "No se encontró la cuenta bancaria asociada")
    try:
        plain_number = rules.decrypt_account_number(account.numero_cuenta_cifrado)
    except rules.PayoutValidationError:
        raise HTTPException(502, "No se pudo descifrar la cuenta bancaria") from None

    logger.info("Acceso administrativo a cuenta bancaria para desembolso: admin=%s orden=%s", admin.id, order.id)
    return {
        "orden_id": str(order.id),
        "banco": account.banco,
        "tipo_cuenta": account.tipo_cuenta,
        "titular_nombre": account.titular_nombre,
        "numero_cuenta": plain_number,
        "cuenta_enmascarada": account.cuenta_enmascarada,
    }


def decide_payout(
    order_id: uuid.UUID,
    payload: PayoutDecisionInput,
    claims: dict,
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    """HU-43 ST-04 (CA-04, CA-05, CA-06): confirmación con referencia única o rechazo con liberación de reserva."""
    admin = _require_admin(claims, db)
    decision = (payload.decision or "").strip().upper()
    if decision not in ("CONFIRMAR", "RECHAZAR"):
        raise HTTPException(400, "Decisión inválida. Usa 'CONFIRMAR' o 'RECHAZAR'.")

    # Bloqueo pesimista sobre la orden para evitar doble pago o carreras (CA-06).
    order = db.scalar(
        select(PayoutOrder)
        .where(PayoutOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise HTTPException(404, "No se encontró la orden de desembolso")
    if order.estado != rules.PENDIENTE:
        raise HTTPException(409, "Esta orden de desembolso ya fue decidida y no puede modificarse")

    tech = db.scalar(
        select(User).where(User.id == order.tecnico_id).with_for_update().execution_options(populate_existing=True)
    )
    if tech is None:
        raise HTTPException(404, "No se encontró el técnico asociado a la orden")

    eval_now = now if now is not None else datetime.now(timezone.utc)
    if eval_now.tzinfo is None:
        eval_now = eval_now.replace(tzinfo=timezone.utc)

    estado_anterior = order.estado
    contable_anterior = rules.accounting_balance(db, tech)
    reservado_anterior = rules.reserved_balance(db, tech)
    libre_anterior = max(contable_anterior - reservado_anterior, Decimal("0.00"))
    obs = (payload.observacion or "").strip() or None

    if decision == "RECHAZAR":
        motivo = (payload.motivo_rechazo or "").strip()
        if not motivo:
            raise HTTPException(400, "Debes indicar el motivo para rechazar la orden de desembolso")
        if not rules.can_transition_payout(estado_anterior, rules.RECHAZADA):
            raise HTTPException(409, "Esta orden de desembolso ya fue decidida y no puede modificarse")

        order.estado = rules.RECHAZADA
        order.importe_reservado = Decimal("0.00")
        order.motivo_rechazo = motivo
        order.observacion = obs
        order.revisado_por = admin.id
        order.revisado_en = eval_now

        accion = "RECHAZO"
        contable_posterior = contable_anterior
        reservado_posterior = max(reservado_anterior - order.importe, Decimal("0.00"))
        libre_posterior = max(contable_posterior - reservado_posterior, Decimal("0.00"))
    else:
        raw_ref = (payload.referencia_transferencia or "").strip()
        if not raw_ref:
            raise HTTPException(400, "Debes indicar la referencia bancaria de la transferencia")
        try:
            ref_norm = rules.normalize_transfer_reference(raw_ref)
        except rules.PayoutValidationError as exc:
            raise HTTPException(400, str(exc)) from None

        duplicate = db.scalar(
            select(PayoutOrder.id).where(
                PayoutOrder.estado == rules.PAGADA,
                PayoutOrder.referencia_normalizada == ref_norm,
            )
        )
        if duplicate is not None:
            raise HTTPException(409, "Esta referencia de transferencia ya fue aplicada a otra orden pagada")

        if not rules.can_transition_payout(estado_anterior, rules.PAGADA):
            raise HTTPException(409, "Esta orden de desembolso ya fue decidida y no puede modificarse")

        order.estado = rules.PAGADA
        order.importe_reservado = Decimal("0.00")
        order.referencia_transferencia = raw_ref
        order.referencia_normalizada = ref_norm
        order.observacion = obs
        order.revisado_por = admin.id
        order.revisado_en = eval_now

        accion = "CONFIRMACION_PAGO"
        contable_posterior = max(contable_anterior - order.importe, Decimal("0.00"))
        reservado_posterior = max(reservado_anterior - order.importe, Decimal("0.00"))
        libre_posterior = max(contable_posterior - reservado_posterior, Decimal("0.00"))

    audit = PayoutOrderAudit(
        id=uuid.uuid4(),
        orden_id=order.id,
        tecnico_id=tech.id,
        accion=accion,
        estado_anterior=estado_anterior,
        estado_nuevo=order.estado,
        importe=order.importe,
        referencia_transferencia=order.referencia_transferencia,
        motivo_rechazo=order.motivo_rechazo,
        observacion=order.observacion,
        saldo_contable_anterior=contable_anterior,
        saldo_contable_posterior=contable_posterior,
        saldo_reservado_anterior=reservado_anterior,
        saldo_reservado_posterior=reservado_posterior,
        saldo_libre_anterior=libre_anterior,
        saldo_libre_posterior=libre_posterior,
        responsable_id=admin.id,
        creado_en=eval_now,
    )
    ledger_type = rules.LEDGER_DEBITO if decision == "CONFIRMAR" else rules.LEDGER_LIBERACION
    ledger_entry = rules.record_payout_ledger_entry(
        db,
        order=order,
        movement_type=ledger_type,
        saldo_contable=contable_posterior,
        saldo_reservado=reservado_posterior,
        saldo_libre=libre_posterior,
        responsable_id=admin.id,
        referencia_transferencia=order.referencia_transferencia,
        created_at=eval_now,
    )
    try:
        db.add(audit)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esta referencia de transferencia ya fue aplicada a otra orden pagada") from None

    db.refresh(order)
    db.refresh(audit)
    db.refresh(ledger_entry)
    logger.info(
        "Decisión administrativa de desembolso: admin=%s orden=%s accion=%s importe=%s",
        admin.id, order.id, accion, order.importe,
    )
    return {
        "orden": _serialize_order_for_admin(order, tech, admin),
        "saldos": {
            "moneda": "HNL",
            "saldo_contable": str(contable_posterior),
            "saldo_reservado": str(reservado_posterior),
            "saldo_libre": str(libre_posterior),
        },
        "auditoria": _serialize_payout_audit(audit, admin),
        "movimiento_libro": _serialize_ledger_entry(ledger_entry),
    }


def list_payout_audit(order_id: uuid.UUID, claims: dict, db: Session) -> dict:
    _require_admin(claims, db)
    order = db.get(PayoutOrder, order_id)
    if order is None:
        raise HTTPException(404, "No se encontró la orden de desembolso")
    entries = db.scalars(
        select(PayoutOrderAudit)
        .where(PayoutOrderAudit.orden_id == order_id)
        .order_by(PayoutOrderAudit.creado_en.asc())
    ).all()
    ledger_entries = db.scalars(
        select(PayoutLedgerEntry)
        .where(PayoutLedgerEntry.orden_id == order_id)
        .order_by(PayoutLedgerEntry.creado_en.asc())
    ).all()
    reviewer_ids = {e.responsable_id for e in entries}
    reviewers = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(reviewer_ids))).all()}
        if reviewer_ids
        else {}
    )
    return {
        "orden_id": str(order_id),
        "movimientos": [_serialize_payout_audit(e, reviewers.get(e.responsable_id)) for e in entries],
        "movimientos_libro": [_serialize_ledger_entry(e) for e in ledger_entries],
    }


@router.get("/admin/payouts")
def admin_payouts_endpoint(
    response: Response, estado: str | None = None, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    response.headers.update(NO_STORE)
    return list_admin_payouts(claims, db, estado)


@router.get("/admin/payouts/{order_id}/bank-details")
def admin_payout_bank_details_endpoint(
    order_id: uuid.UUID, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    response.headers.update(NO_STORE)
    return get_admin_payout_bank_details(order_id, claims, db)


@router.post("/admin/payouts/{order_id}/decision")
def admin_decide_payout_endpoint(
    order_id: uuid.UUID,
    payload: PayoutDecisionInput,
    response: Response,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    return decide_payout(order_id, payload, claims, db)


@router.get("/admin/payouts/{order_id}/audit")
def admin_payout_audit_endpoint(
    order_id: uuid.UUID, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)
):
    response.headers.update(NO_STORE)
    return list_payout_audit(order_id, claims, db)
