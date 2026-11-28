"""HU-38 ST-02: Repositorio, servicio de intentos de pago idempotentes y consulta por servicio (HU-39)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.financial_settlement import get_refund_for_request
from app.models import (
    DigitalPaymentIntent,
    DirectRequest,
    OpenRequest,
    OpenRequestProposal,
    ServiceAdjustment,
    User,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.payment_attempt_state import (
    PaymentAttemptStateRegistry,
    PaymentAttemptStatus,
    PaymentConcept,
    PaymentRetentionStatus,
    default_payment_state_registry,
)
from app.payment_gateway_config import PaymentGatewaySettings
from app.payment_gateway_port import (
    CreatePaymentRequest,
    GatewayEnvironment,
    HNL_CURRENCY,
    PaymentGatewayPort,
    to_hnl_money,
)


class DigitalPaymentError(Exception):
    """Error base de dominio de pagos digitales."""


class DigitalPaymentValidationError(DigitalPaymentError):
    """Error de validación (HTTP 400)."""


class DigitalPaymentForbiddenError(DigitalPaymentError):
    """Acceso denegado (HTTP 403)."""


class DigitalPaymentNotFoundError(DigitalPaymentError):
    """Recurso o intento no encontrado (HTTP 404)."""


class DigitalPaymentConflictError(DigitalPaymentError):
    """Conflicto de estado o duplicidad de cobro confirmado (HTTP 409)."""


class DigitalPaymentRepository(ABC):
    """Puerto Repository (DIP/OCP) para persistencia de intentos de pago digital."""

    @abstractmethod
    def get_by_id(self, attempt_id: str, *, lock: bool = False) -> DigitalPaymentIntent | None: ...

    @abstractmethod
    def get_by_idempotency_key(self, key: str, *, lock: bool = False) -> DigitalPaymentIntent | None: ...

    @abstractmethod
    def get_by_gateway_reference(
        self,
        *,
        external_reference: str | None = None,
        payment_intent_id: str | None = None,
        lock: bool = False,
    ) -> DigitalPaymentIntent | None: ...

    @abstractmethod
    def list_by_service(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        concepto: PaymentConcept | None = None,
    ) -> list[DigitalPaymentIntent]: ...

    @abstractmethod
    def add(self, attempt: DigitalPaymentIntent) -> None: ...

    @abstractmethod
    def flush(self) -> None: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def refresh(self, attempt: DigitalPaymentIntent) -> None: ...


class SqlAlchemyDigitalPaymentRepository(DigitalPaymentRepository):
    """Implementación SQLAlchemy del repositorio de intentos de pago digital."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, attempt_id: str, *, lock: bool = False) -> DigitalPaymentIntent | None:
        query = select(DigitalPaymentIntent).where(DigitalPaymentIntent.id == attempt_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def get_by_idempotency_key(self, key: str, *, lock: bool = False) -> DigitalPaymentIntent | None:
        query = select(DigitalPaymentIntent).where(DigitalPaymentIntent.clave_idempotencia == key)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def get_by_gateway_reference(
        self,
        *,
        external_reference: str | None = None,
        payment_intent_id: str | None = None,
        lock: bool = False,
    ) -> DigitalPaymentIntent | None:
        predicates = []
        if external_reference and external_reference.strip():
            ref = external_reference.strip()
            predicates.append(DigitalPaymentIntent.referencia_pasarela == ref)
            predicates.append(DigitalPaymentIntent.payment_intent_id == ref)
        if payment_intent_id and payment_intent_id.strip():
            pi = payment_intent_id.strip()
            predicates.append(DigitalPaymentIntent.payment_intent_id == pi)
            predicates.append(DigitalPaymentIntent.referencia_pasarela == pi)
        if not predicates:
            return None
        query = select(DigitalPaymentIntent).where(or_(*predicates))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._db.scalar(query)

    def list_by_service(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        concepto: PaymentConcept | None = None,
    ) -> list[DigitalPaymentIntent]:
        query = select(DigitalPaymentIntent).where(
            DigitalPaymentIntent.modalidad == modalidad,
            DigitalPaymentIntent.solicitud_id == solicitud_id,
        )
        if concepto is not None:
            query = query.where(DigitalPaymentIntent.concepto == concepto.value)
        query = query.order_by(DigitalPaymentIntent.creado_en.desc(), DigitalPaymentIntent.id.desc())
        return list(self._db.scalars(query).all())

    def add(self, attempt: DigitalPaymentIntent) -> None:
        self._db.add(attempt)

    def flush(self) -> None:
        self._db.flush()

    def commit(self) -> None:
        self._db.commit()

    def refresh(self, attempt: DigitalPaymentIntent) -> None:
        self._db.refresh(attempt)


def normalize_modalidad(raw: str | None) -> str:
    cleaned = (raw or "").strip().upper()
    if cleaned not in {"DIRECTA", "ABIERTA"}:
        raise DigitalPaymentValidationError("La modalidad debe ser 'DIRECTA' o 'ABIERTA'.")
    return cleaned


def normalize_concepto(raw: str | None) -> PaymentConcept:
    cleaned = (raw or PaymentConcept.BASE.value).strip().upper()
    try:
        return PaymentConcept(cleaned)
    except ValueError:
        raise DigitalPaymentValidationError("El concepto debe ser 'BASE' o 'AJUSTE'.") from None


def serialize_payment_attempt(row: DigitalPaymentIntent) -> dict[str, Any]:
    return {
        "id": row.id,
        "clave_idempotencia": row.clave_idempotencia,
        "referencia_interna": row.referencia_interna,
        "modalidad": row.modalidad,
        "solicitud_id": row.solicitud_id,
        "concepto": row.concepto,
        "cliente_uid": row.cliente_uid,
        "tecnico_uid": row.tecnico_uid,
        "propuesta_id": row.propuesta_id,
        "moneda": row.moneda,
        "monto": str(to_hnl_money(row.monto)),
        "proveedor": row.proveedor,
        "entorno": row.entorno,
        "es_sandbox": row.entorno == GatewayEnvironment.SANDBOX.value,
        "es_simulacion_local": row.entorno == GatewayEnvironment.SIMULADOR_LOCAL.value,
        "referencia_pasarela": row.referencia_pasarela,
        "payment_intent_id": row.payment_intent_id,
        "checkout_url": row.checkout_url,
        "estado": row.estado,
        "estado_retencion": row.estado_retencion,
        "motivo_resultado": row.motivo_resultado,
        "creado_en": format_tegucigalpa_iso(row.creado_en),
        "confirmado_en": format_tegucigalpa_iso(row.confirmado_en),
        "liberado_en": format_tegucigalpa_iso(row.liberado_en),
        "reembolsado_en": format_tegucigalpa_iso(row.reembolsado_en),
    }


def _find_service_record(
    db: Session,
    *,
    modalidad: str | None,
    solicitud_id: str,
    lock: bool = False,
) -> tuple[str, Any]:
    clean_id = (solicitud_id or "").strip()
    if not clean_id:
        raise DigitalPaymentNotFoundError("No se encontró la solicitud.")

    candidates: list[tuple[str, Any]] = []
    if modalidad is None or modalidad == "DIRECTA":
        direct_id: str | None = clean_id
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            try:
                direct_id = str(uuid.UUID(clean_id))
            except ValueError:
                direct_id = None
        if direct_id is not None:
            dq = select(DirectRequest).where(DirectRequest.id == direct_id)
            if lock:
                dq = dq.with_for_update().execution_options(populate_existing=True)
            row = db.scalar(dq)
            if row is not None:
                candidates.append(("DIRECTA", row))

    if not candidates and (modalidad is None or modalidad == "ABIERTA"):
        oq = select(OpenRequest).where(OpenRequest.id == clean_id)
        if lock:
            oq = oq.with_for_update().execution_options(populate_existing=True)
        row = db.scalar(oq)
        if row is not None:
            candidates.append(("ABIERTA", row))

    if not candidates:
        raise DigitalPaymentNotFoundError("No se encontró la solicitud.")
    return candidates[0]


def _resolve_expected_amount_hnl(
    db: Session,
    *,
    modalidad: str,
    record: Any,
    concepto: PaymentConcept,
    propuesta_id: str | None = None,
) -> Decimal:
    """Calcula autoritativamente en backend el monto acordado en HNL (CA-01 / CA-05)."""
    if concepto == PaymentConcept.BASE:
        if modalidad == "ABIERTA" and propuesta_id:
            proposal = db.scalar(
                select(OpenRequestProposal).where(
                    OpenRequestProposal.id == propuesta_id,
                    OpenRequestProposal.solicitud_id == str(record.id),
                )
            )
            if proposal is None:
                raise DigitalPaymentNotFoundError("Propuesta no encontrada para la solicitud abierta.")
            return to_hnl_money(proposal.monto)
        raw_base = getattr(record, "monto_base", None)
        if raw_base is None or to_hnl_money(raw_base) <= Decimal("0.00"):
            raw_base = getattr(record, "presupuesto", None)
        if raw_base is None:
            raise DigitalPaymentConflictError("La solicitud no tiene un monto base acordado.")
        return to_hnl_money(raw_base)

    # Concepto AJUSTE (CA-05): solo diferencia positiva de un ajuste aprobado (nunca efectivo)
    adjustment = db.scalar(
        select(ServiceAdjustment).where(
            ServiceAdjustment.modalidad == modalidad,
            ServiceAdjustment.solicitud_id == str(record.id),
        )
    )
    if adjustment is None:
        raise DigitalPaymentNotFoundError("La solicitud no tiene un ajuste registrado.")
    if adjustment.estado not in {"PENDIENTE", "APROBADO"}:
        raise DigitalPaymentConflictError("Solo un ajuste pendiente o aprobado permite cobrar la diferencia digital.")
    diff = to_hnl_money(adjustment.diferencia)
    if diff <= Decimal("0.00"):
        raise DigitalPaymentConflictError("Solo una diferencia positiva requiere cobro complementario.")
    if (getattr(record, "metodo_pago_ajuste", None) or "").strip().lower() == "efectivo":
        raise DigitalPaymentConflictError(
            "La diferencia del ajuste ya fue registrada en efectivo; no corresponde cobro digital."
        )
    return diff


class DigitalPaymentIntentService:
    """Casos de uso de creación, idempotencia y consulta de intentos de pago digital (ST-02)."""

    def __init__(
        self,
        db: Session,
        repository: DigitalPaymentRepository,
        gateway: PaymentGatewayPort,
        *,
        settings: PaymentGatewaySettings | None = None,
        state_registry: PaymentAttemptStateRegistry | None = None,
    ) -> None:
        self._db = db
        self._repo = repository
        self._gateway = gateway
        self._settings = settings or PaymentGatewaySettings.from_env()
        self._states = state_registry or default_payment_state_registry

    def _build_internal_reference(self, modalidad: str, concepto: PaymentConcept) -> str:
        prefix = "DIR" if modalidad == "DIRECTA" else "ABI"
        tag = "BASE" if concepto == PaymentConcept.BASE else "AJU"
        suffix = uuid.uuid4().hex[:12].upper()
        return f"TKGO-{prefix}-{tag}-{suffix}"

    def _build_return_urls(
        self,
        modalidad: str,
        solicitud_id: str,
        internal_ref: str,
        concepto: PaymentConcept = PaymentConcept.BASE,
    ) -> tuple[str, str]:
        base = self._settings.frontend_base_url
        success_path = (
            f"/mis-trabajos/{solicitud_id}"
            if (concepto == PaymentConcept.AJUSTE or modalidad == "ABIERTA")
            else f"/mis-solicitudes/{solicitud_id}"
        )
        cancel_path = (
            f"/mis-trabajos/{solicitud_id}"
            if concepto == PaymentConcept.AJUSTE
            else f"/mis-solicitudes/{solicitud_id}"
        )
        success_url = f"{base}{success_path}?pago=confirmando&ref={internal_ref}&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base}{cancel_path}?pago=cancelado&ref={internal_ref}"
        return success_url, cancel_url

    def create_attempt(
        self,
        user: User,
        *,
        solicitud_id: str,
        modalidad: str | None = None,
        concepto: str = PaymentConcept.BASE.value,
        idempotency_key: str | None = None,
        propuesta_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        norm_mod = normalize_modalidad(modalidad) if modalidad else None
        norm_concept = normalize_concepto(concepto)
        resolved_mod, record = _find_service_record(
            self._db,
            modalidad=norm_mod,
            solicitud_id=solicitud_id,
            lock=True,
        )
        req_id = str(record.id)

        if record.cliente_id != user.firebase_uid:
            raise DigitalPaymentForbiddenError("Solo el cliente propietario puede iniciar el pago de esta solicitud.")

        expected_amount = _resolve_expected_amount_hnl(
            self._db,
            modalidad=resolved_mod,
            record=record,
            concepto=norm_concept,
            propuesta_id=propuesta_id,
        )
        if expected_amount <= Decimal("0.00"):
            raise DigitalPaymentValidationError("El monto a cobrar debe ser mayor a L. 0.00.")

        clean_idem = (idempotency_key or "").strip()[:160]
        if not clean_idem:
            clean_idem = f"PAY-{resolved_mod}-{req_id}-{norm_concept.value}-{uuid.uuid4().hex[:10]}"

        # 1. Idempotencia estricta por clave_idempotencia (CA-04)
        existing_idem = self._repo.get_by_idempotency_key(clean_idem, lock=True)
        if existing_idem is not None:
            if existing_idem.cliente_uid != user.firebase_uid:
                raise DigitalPaymentForbiddenError("No tienes permiso para consultar este intento de pago.")
            if existing_idem.solicitud_id != req_id or existing_idem.concepto != norm_concept.value:
                raise DigitalPaymentConflictError("La clave de idempotencia ya fue usada para otro cobro.")
            return {
                "intento": serialize_payment_attempt(existing_idem),
                "entorno_pasarela": self._gateway.runtime_info().to_dict(),
                "reintento_idempotente": True,
            }

        # 2. Nunca permitir un segundo cobro si ya existe uno CONFIRMADO para el mismo servicio y concepto (CA-04)
        prior_attempts = self._repo.list_by_service(resolved_mod, req_id, concepto=norm_concept)
        for prior in prior_attempts:
            prior_state = self._states.resolve(prior.estado)
            if prior_state.counts_as_confirmed_charge:
                raise DigitalPaymentConflictError(
                    f"Ya existe un pago digital confirmado para el concepto {norm_concept.value} de esta solicitud."
                )

        # 3. Si hay un intento PENDIENTE vigente del mismo monto/propuesta, lo reutiliza sin duplicar sesión
        for prior in prior_attempts:
            if (
                prior.estado == PaymentAttemptStatus.PENDIENTE.value
                and to_hnl_money(prior.monto) == expected_amount
                and (prior.propuesta_id or None) == (propuesta_id or None)
            ):
                return {
                    "intento": serialize_payment_attempt(prior),
                    "entorno_pasarela": self._gateway.runtime_info().to_dict(),
                    "reintento_idempotente": True,
                }

        internal_ref = self._build_internal_reference(resolved_mod, norm_concept)
        success_url, cancel_url = self._build_return_urls(resolved_mod, req_id, internal_ref, norm_concept)
        label_concept = "Pago inicial de servicio" if norm_concept == PaymentConcept.BASE else "Pago de diferencia por ajuste"

        gateway_res = self._gateway.create_payment(
            CreatePaymentRequest(
                internal_reference=internal_ref,
                amount_hnl=expected_amount,
                currency=HNL_CURRENCY,
                idempotency_key=clean_idem,
                description=f"TekniGO - {label_concept}",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "modalidad": resolved_mod,
                    "solicitud_id": req_id,
                    "concepto": norm_concept.value,
                    "propuesta_id": propuesta_id or "",
                    "cliente_uid": user.firebase_uid,
                },
            )
        )

        moment = ensure_utc_datetime(now or datetime.now(timezone.utc))
        runtime = self._gateway.runtime_info()
        attempt = DigitalPaymentIntent(
            id=str(uuid.uuid4()),
            clave_idempotencia=clean_idem,
            referencia_interna=internal_ref,
            modalidad=resolved_mod,
            solicitud_id=req_id,
            concepto=norm_concept.value,
            cliente_uid=user.firebase_uid,
            tecnico_uid=getattr(record, "tecnico_id", None),
            propuesta_id=propuesta_id,
            moneda=HNL_CURRENCY,
            monto=expected_amount,
            proveedor=runtime.provider,
            entorno=runtime.environment.value,
            referencia_pasarela=gateway_res.external_reference,
            payment_intent_id=gateway_res.payment_intent_id,
            checkout_url=gateway_res.checkout_url,
            estado=gateway_res.status.value,
            estado_retencion=PaymentRetentionStatus.NO_APLICA.value,
            motivo_resultado=gateway_res.failure_reason,
            metadatos={"raw_status": gateway_res.raw_status},
            creado_en=moment,
            actualizado_en=moment,
        )
        self._repo.add(attempt)
        self._repo.commit()
        self._repo.refresh(attempt)

        return {
            "intento": serialize_payment_attempt(attempt),
            "entorno_pasarela": runtime.to_dict(),
            "reintento_idempotente": False,
        }

    def get_attempt_detail(self, user: User, attempt_id: str) -> dict[str, Any]:
        row = self._repo.get_by_id((attempt_id or "").strip())
        if row is None:
            raise DigitalPaymentNotFoundError("Intento de pago digital no encontrado.")
        is_admin = any(role.rol == "ADMIN" for role in user.roles)
        identities = {user.firebase_uid, str(user.id)}
        if not is_admin and row.cliente_uid not in identities and row.tecnico_uid not in identities:
            raise DigitalPaymentForbiddenError("No tienes permiso para consultar este intento de pago.")
        return {
            "intento": serialize_payment_attempt(row),
            "entorno_pasarela": self._gateway.runtime_info().to_dict(),
        }

    def get_service_digital_payments_summary(
        self,
        modalidad: str,
        solicitud_id: str,
        *,
        viewer: User | None = None,
    ) -> dict[str, Any]:
        """
        Contrato de consulta por servicio para HU-38 / HU-39 / HU-40 / HU-41:
        expone cobro BASE y cobro AJUSTE por separado con monto confirmado, referencia externa y estado.
        """
        norm_mod = normalize_modalidad(modalidad)
        _, record = _find_service_record(self._db, modalidad=norm_mod, solicitud_id=solicitud_id)
        req_id = str(record.id)

        if viewer is not None:
            is_admin = any(role.rol == "ADMIN" for role in viewer.roles)
            identities = {viewer.firebase_uid, str(viewer.id)}
            if not is_admin and record.cliente_id not in identities and getattr(record, "tecnico_id", None) not in identities:
                raise DigitalPaymentForbiddenError("No tienes permiso para consultar los pagos de este servicio.")

        attempts = self._repo.list_by_service(norm_mod, req_id)

        def _pick_component(concept: PaymentConcept) -> dict[str, Any] | None:
            scoped = [row for row in attempts if row.concepto == concept.value]
            if not scoped:
                return None
            confirmed = next(
                (r for r in scoped if r.estado in {PaymentAttemptStatus.CONFIRMADO.value, PaymentAttemptStatus.REEMBOLSADO.value}),
                None,
            )
            chosen = confirmed or scoped[0]
            confirmed_amount = (
                to_hnl_money(chosen.monto)
                if chosen.estado == PaymentAttemptStatus.CONFIRMADO.value
                else Decimal("0.00")
            )
            return {
                "intento_id": chosen.id,
                "concepto": chosen.concepto,
                "monto_intento": str(to_hnl_money(chosen.monto)),
                "monto_confirmado": str(confirmed_amount),
                "moneda": chosen.moneda,
                "referencia_interna": chosen.referencia_interna,
                "referencia_externa": chosen.payment_intent_id or chosen.referencia_pasarela,
                "referencia_pasarela": chosen.referencia_pasarela,
                "payment_intent_id": chosen.payment_intent_id,
                "estado": chosen.estado,
                "estado_retencion": chosen.estado_retencion,
                "proveedor": chosen.proveedor,
                "entorno": chosen.entorno,
                "confirmado_en": format_tegucigalpa_iso(chosen.confirmado_en),
                "liberado_en": format_tegucigalpa_iso(chosen.liberado_en),
                "reembolsado_en": format_tegucigalpa_iso(chosen.reembolsado_en),
            }

        cobro_base = _pick_component(PaymentConcept.BASE)
        cobro_ajuste = _pick_component(PaymentConcept.AJUSTE)

        monto_base_conf = to_hnl_money(cobro_base["monto_confirmado"]) if cobro_base else Decimal("0.00")
        monto_ajuste_conf = to_hnl_money(cobro_ajuste["monto_confirmado"]) if cobro_ajuste else Decimal("0.00")
        total_conf = to_hnl_money(monto_base_conf + monto_ajuste_conf)

        return {
            "modalidad": norm_mod,
            "solicitud_id": req_id,
            "moneda": HNL_CURRENCY,
            "metodo_pago_base": (getattr(record, "metodo_pago", None) or "efectivo").strip().lower(),
            "metodo_pago_ajuste": (getattr(record, "metodo_pago_ajuste", None) or "").strip().lower() or None,
            "cobro_base": cobro_base,
            "cobro_ajuste": cobro_ajuste,
            "monto_base_digital_confirmado": str(monto_base_conf),
            "monto_ajuste_digital_confirmado": str(monto_ajuste_conf),
            "monto_digital_confirmado_total": str(total_conf),
            "tiene_pago_base_confirmado": monto_base_conf > Decimal("0.00"),
            "tiene_pago_ajuste_confirmado": monto_ajuste_conf > Decimal("0.00"),
            "reembolso": get_refund_for_request(self._db, norm_mod, req_id),
            "intentos": [serialize_payment_attempt(row) for row in attempts],
            "entorno_pasarela": self._gateway.runtime_info().to_dict(),
        }