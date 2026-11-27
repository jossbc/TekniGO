"""Consultas financieras y registro del efectivo recibido fuera de la plataforma."""
from decimal import Decimal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.financial_settlement import calculate_payment_split, record_ledger_entry_once, to_money
from app.models import (
    DirectRequest, FinancialLedgerEntry, OpenRequest, User, format_tegucigalpa_iso,
)
from app.security import get_current_user
from app.service_reception import _canonical_state, _find_locked_request

router = APIRouter(tags=["Billetera y cobros en efectivo"])
REQUEST_MODELS = {"DIRECTA": DirectRequest, "ABIERTA": OpenRequest}


def _account(db: Session, uid: str) -> User:
    account = db.scalar(select(User).where(User.firebase_uid == uid))
    if account is None:
        raise HTTPException(403, "Se requiere una cuenta de TekniGO.")
    return account


def _closure_split(record) -> dict | None:
    tracking = record.seguimiento_recepcion or {}
    event = tracking.get("evento_cierre") or {}
    effect = tracking.get("efecto_financiero") or {}
    if effect.get("reparto_medios"):
        return effect["reparto_medios"]
    if event.get("tasa_comision") is not None:
        return calculate_payment_split(record, commission_rate=Decimal(str(event["tasa_comision"]))).to_dict()
    return None


def _cash_key(modalidad: str, request_id: str) -> str:
    return f"COBRO-EFECTIVO:{modalidad}:{request_id}"


def _cash_view(record, modalidad: str, split: dict, movement: FinancialLedgerEntry | None) -> dict:
    cash_total = to_money(split["cobrado_efectivo"])
    cash_adjustment = min(cash_total, max(to_money(split["monto_ajuste"]), Decimal("0.00")))
    return {
        "solicitud_id": str(record.id),
        "modalidad": modalidad,
        "efectivo": split["cobrado_efectivo"],
        "digital": split["cobrado_digital"],
        "comision": split["monto_comision"],
        "efectivo_base": str(cash_total - cash_adjustment),
        "efectivo_ajuste": str(cash_adjustment),
        "registrado_en": format_tegucigalpa_iso(movement.creado_en) if movement else None,
    }


def register_cash_collection(db: Session, uid: str, request_id: str) -> dict:
    modalidad, record = _find_locked_request(db, _request_uuid(request_id))
    user = _account(db, uid)
    if not any(role.rol == "TECNICO" for role in user.roles) or record.tecnico_id not in {uid, str(user.id)}:
        raise HTTPException(403, "Solo el técnico seleccionado puede registrar este cobro.")
    if _canonical_state(record.estado) != "COMPLETADA":
        raise HTTPException(409, "Solo puedes registrar el cobro de un servicio completado.")
    split = _closure_split(record)
    if split is None or to_money(split["cobrado_efectivo"]) <= 0:
        raise HTTPException(409, "El servicio no tiene un componente en efectivo registrado al cierre.")

    movement, repeated = record_ledger_entry_once(
        db,
        clave_idempotencia=_cash_key(modalidad, str(record.id)),
        modalidad=modalidad,
        solicitud_id=str(record.id),
        tipo_movimiento="COBRO_EFECTIVO",
        monto_base=Decimal(split["monto_base"]),
        monto_ajuste=Decimal(split["monto_ajuste"]),
        monto_total=Decimal(split["cobrado_efectivo"]),
        tasa_comision=Decimal(split["tasa_comision"]),
        monto_comision=Decimal(split["monto_comision"]),
        metodo_pago="efectivo",
        referencia_pasarela=None,
        detalle={**split, "tecnico_id": uid, "custodiado_por_plataforma": False},
    )
    result = _cash_view(record, modalidad, split, movement)
    db.commit()
    return {**result, "reintento_idempotente": repeated}


def cash_history(db: Session, user: User) -> list[dict]:
    services = []
    identities = (user.firebase_uid, str(user.id))
    for modalidad, model in REQUEST_MODELS.items():
        records = db.scalars(select(model).where(
            model.tecnico_id.in_(identities), func.upper(func.trim(model.estado)) == "COMPLETADA",
            model.seguimiento_recepcion.is_not(None),
        ))
        for record in records:
            split = _closure_split(record)
            if split and to_money(split["cobrado_efectivo"]) > 0:
                services.append((modalidad, record, split))
    keys = [_cash_key(mode, str(record.id)) for mode, record, _ in services]
    movements = {movement.clave_idempotencia: movement for movement in db.scalars(
        select(FinancialLedgerEntry).where(FinancialLedgerEntry.clave_idempotencia.in_(keys))
    )} if keys else {}
    return sorted(
        [_cash_view(record, mode, split, movements.get(_cash_key(mode, str(record.id))))
         for mode, record, split in services],
        key=lambda item: item["registrado_en"] or "",
        reverse=True,
    )


@router.get("/users/me/cash-collections")
def my_cash_collections(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, private"
    user = _account(db, claims["uid"])
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "Se requiere el rol técnico.")
    return {"cobros": cash_history(db, user)}


@router.get("/admin/technicians/{technician_id}/cash-collections")
def admin_cash_collections(technician_id: str, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, private"
    admin = _account(db, claims["uid"])
    if not any(role.rol == "ADMIN" for role in admin.roles):
        raise HTTPException(403, "Se requiere el rol administrador.")
    return {"cobros": cash_history(db, _account(db, technician_id))}


@router.get("/admin/cash-collections")
def recent_admin_cash_collections(response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, private"
    admin = _account(db, claims["uid"])
    if not any(role.rol == "ADMIN" for role in admin.roles):
        raise HTTPException(403, "Se requiere el rol administrador.")
    movements = db.scalars(select(FinancialLedgerEntry).where(
        FinancialLedgerEntry.tipo_movimiento == "COBRO_EFECTIVO",
    ).order_by(FinancialLedgerEntry.creado_en.desc(), FinancialLedgerEntry.id.desc()).limit(100))
    return {"cobros": [{
        "solicitud_id": movement.solicitud_id, "modalidad": movement.modalidad,
        "efectivo": str(movement.monto_total), "digital": movement.detalle["cobrado_digital"],
        "comision": str(movement.monto_comision),
        "tecnico_uid": movement.detalle["tecnico_id"],
        "registrado_en": format_tegucigalpa_iso(movement.creado_en),
    } for movement in movements]}


@router.post("/users/me/cash-collections/{request_id}")
def confirm_cash_collection(request_id: str, response: Response, claims=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, private"
    return register_cash_collection(db, claims["uid"], request_id)


def _request_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "No se encontró la solicitud.") from None
