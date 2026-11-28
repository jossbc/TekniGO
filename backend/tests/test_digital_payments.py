"""HU-38 ST-06: Pruebas completas de pago digital (CA-01 a CA-08, duplicados, fallos, concurrencia y HU-39)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import time
import unittest
import uuid

from fastapi import HTTPException,Response
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adjustment_repository import SqlAlchemyAdjustmentRepository
from app.adjustment_service import AdjustmentDecision, respond_adjustment
from app.cancellation_policy import default_cancellation_policy
from app.cancellation_repository import SqlAlchemyCancellationRepository
from app.cancellation_service import CancellationCause, cancel_request
from app.database import Base
from app.digital_payments_service import (
    DigitalPaymentConflictError,
    DigitalPaymentIntentService,
    SqlAlchemyDigitalPaymentRepository,
)
from app.digital_refund_service import DigitalRefundOnCancellationObserver, DigitalRefundService
from app.models import (
    DirectRequest,
    FinancialLedgerEntry,
    PaymentRefund,
    Service,
    ServiceAdjustment,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.payment_gateway_adapters import (
    DummyLocalGatewayAdapter,
    PaymentGatewayFactory,
    PaymentGatewayRegistry,
    StripeSandboxGatewayAdapter,
    build_default_payment_gateway_registry,
)
from app.payment_gateway_config import PaymentGatewaySettings
from app.payment_gateway_port import (
    CreatePaymentRequest,
    CreateRefundRequest,
    GatewayPaymentStatus,
    GatewayRefundResult,
    GatewayRefundStatus,
    PaymentGatewayAuthenticationError,
    PaymentGatewayCommunicationError,
    PaymentGatewayConfigError,
    centavos_to_hnl,
    hnl_to_centavos,
)
from app.payment_lifecycle_bridge import ServicePaymentConfirmationObserver
from app.payment_reconciliation_service import (
    PaymentReconciliationService,
    SqlAlchemyWebhookEventRepository,
)
from app.service_reception import confirm_service_reception_by_client
from app.technician_direct_requests import respond_to_direct_request
from app.wallet import register_cash_collection, service_receipt, wallet_overview


class ControllableRefundGateway(DummyLocalGatewayAdapter):
    """Adaptador de prueba que permite inyectar fallos y recuperación en devoluciones (CA-07)."""

    def __init__(self, webhook_secret: str = "hu38-test-secret") -> None:
        super().__init__(webhook_secret=webhook_secret)
        self.fail_refund = False
        self.confirm_refund_immediately = True
        self.create_payment_calls = 0
        self.create_refund_calls = 0

    def create_payment(self, request: CreatePaymentRequest):
        self.create_payment_calls += 1
        return super().create_payment(request)

    def create_refund(self, request: CreateRefundRequest) -> GatewayRefundResult:
        self.create_refund_calls += 1
        if self.fail_refund:
            raise PaymentGatewayCommunicationError("Error temporal en pasarela al solicitar devolución")
        status = GatewayRefundStatus.CONFIRMADO if self.confirm_refund_immediately else GatewayRefundStatus.EN_PROCESO
        return GatewayRefundResult(
            provider="dummy",
            refund_reference=f"RFND-OK-{self.create_refund_calls}-{request.payment_reference[-6:]}",
            payment_reference=request.payment_reference,
            status=status,
            amount_hnl=request.amount_hnl,
            currency=request.currency,
            raw_status="succeeded" if self.confirm_refund_immediately else "pending",
        )


class DigitalPaymentsHu38Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _seed_direct_request(
        self,
        *,
        suffix: str,
        monto_base: Decimal = Decimal("500.00"),
        estado: str = "PENDIENTE",
    ) -> tuple[User, User, DirectRequest]:
        idx = abs(hash(suffix)) % 90000
        cli = User(
            id=uuid.uuid4(),
            firebase_uid=f"cli-{suffix}",
            nombre_completo=f"Cliente {suffix}",
            correo=f"cli-{suffix}@teknigo.test",
            dni=f"08011999{idx:05d}",
            telefono=f"+5049{idx:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tec = User(
            id=uuid.uuid4(),
            firebase_uid=f"tec-{suffix}",
            nombre_completo=f"Tecnico {suffix}",
            correo=f"tec-{suffix}@teknigo.test",
            dni=f"08011998{idx:05d}",
            telefono=f"+5048{idx:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        spec = Specialty(id=uuid.uuid4(), nombre=f"Oficio {suffix}", activa=True)
        self.db.add_all([cli, tec, spec])
        self.db.flush()
        self.db.add_all([
            UserRole(usuario_id=cli.id, rol="CLIENTE"),
            UserRole(usuario_id=tec.id, rol="TECNICO"),
            TechnicianProfile(usuario_id=tec.id, disponible=True, estado_verificacion="VERIFICADO"),
        ])
        srv = Service(
            id=uuid.uuid4(),
            usuario_id=tec.id,
            especialidad_id=spec.id,
            nombre=f"Servicio {suffix}",
            descripcion="Descripción",
            tarifa_base=monto_base,
            unidad_cobro="SERVICIO",
            activo=True,
        )
        self.db.add(srv)
        self.db.flush()
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tec.firebase_uid,
            servicio_id=srv.id,
            municipio="Tegucigalpa",
            sector="Centro",
            direccion_exacta="Colonia Prueba",
            fecha_hora=datetime.now(timezone.utc) + timedelta(days=2),
            descripcion="Trabajo de prueba",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=monto_base,
            unidad_cobro="SERVICIO",
            metodo_pago="tarjeta",
            monto_base=monto_base,
            monto_total=monto_base,
            estado=estado,
        )
        self.db.add(req)
        self.db.commit()
        return cli, tec, req

    def _confirm_webhook(
        self,
        gw: DummyLocalGatewayAdapter,
        rec: PaymentReconciliationService,
        *,
        event_id: str,
        external_ref: str,
        status: str = "CONFIRMADO",
        amount_hnl: str = "500.00",
        failure_reason: str | None = None,
    ) -> dict:
        payload = {
            "event_id": event_id,
            "event_type": f"dummy.{status.lower()}",
            "external_reference": external_ref,
            "payment_status": status,
            "amount_hnl": amount_hnl,
            "currency": "HNL",
            "failure_reason": failure_reason,
        }
        raw = json.dumps(payload).encode("utf-8")
        return rec.process_webhook(raw, {"X-TekniGO-Signature": gw.sign_webhook_payload(raw)})

    def test_st01_ca08_ocp_registry_sandbox_secrets_and_decimal_rounding(self):
        # Aritmética decimal HNL <-> centavos (ROUND_HALF_UP)
        self.assertEqual(hnl_to_centavos(Decimal("125.555")), 12556)
        self.assertEqual(hnl_to_centavos(Decimal("125.554")), 12555)
        self.assertEqual(centavos_to_hnl(12556), Decimal("125.56"))

        # CA-08: rechaza llaves live en entorno de pruebas
        registry = build_default_payment_gateway_registry()
        live_settings = PaymentGatewaySettings.from_env({
            "PAYMENT_GATEWAY_PROVIDER": "stripe",
            "STRIPE_SECRET_KEY": "sk_live_prohibida",
            "STRIPE_WEBHOOK_SECRET": "whsec_ok",
        })
        with self.assertRaises(PaymentGatewayConfigError):
            registry.resolve(live_settings)

        # CA-08: Dummy declara explícitamente es_simulacion_local=True
        dummy = registry.resolve(PaymentGatewaySettings.from_env({"PAYMENT_GATEWAY_PROVIDER": "dummy"}))
        self.assertTrue(dummy.runtime_info().is_local_simulator)
        self.assertFalse(dummy.runtime_info().is_sandbox)

        # OCP por interfaz: se registra una nueva fábrica sin modificar PaymentGatewayRegistry
        class CustomFactory(PaymentGatewayFactory):
            @property
            def provider_name(self) -> str:
                return "custom_test"

            def create(self, settings: PaymentGatewaySettings):
                return DummyLocalGatewayAdapter(webhook_secret="custom")

        custom_reg = PaymentGatewayRegistry()
        custom_reg.register(CustomFactory())
        resolved = custom_reg.resolve(PaymentGatewaySettings.from_env({"PAYMENT_GATEWAY_PROVIDER": "custom_test"}))
        self.assertIsInstance(resolved, DummyLocalGatewayAdapter)

    def test_ca01_ca02_ca03_failure_recovery_retention_and_release_at_completion(self):
        cli, tec, req = self._seed_direct_request(suffix="ca123", monto_base=Decimal("1000.00"))
        gw = ControllableRefundGateway()
        repo = SqlAlchemyDigitalPaymentRepository(self.db)
        svc = DigitalPaymentIntentService(self.db, repo, gw)
        rec = PaymentReconciliationService(
            self.db,
            repo,
            SqlAlchemyWebhookEventRepository(self.db),
            gw,
            observers=(ServicePaymentConfirmationObserver(),),
        )

        # Intento 1: rechazado en pasarela -> no acepta, no acredita billetera y permite reintento (CA-02)
        first = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-fail-1")
        self._confirm_webhook(
            gw,
            rec,
            event_id="evt-rej-1",
            external_ref=first["intento"]["referencia_pasarela"],
            status="RECHAZADO",
            amount_hnl="1000.00",
            failure_reason="Fondos insuficientes",
        )
        with self.assertRaises(HTTPException) as blocked:
            respond_to_direct_request(req.id, "ACEPTADA", {"uid": tec.firebase_uid}, self.db)
        self.assertEqual(blocked.exception.status_code, 402)
        self.assertEqual(wallet_overview(self.db, tec)["saldo_disponible"], "0.00")

        # Intento 2: nueva referencia única -> confirmado -> retenido lógicamente (CA-01, CA-03)
        second = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-ok-2")
        self.assertNotEqual(first["intento"]["referencia_interna"], second["intento"]["referencia_interna"])
        self._confirm_webhook(
            gw,
            rec,
            event_id="evt-ok-2",
            external_ref=second["intento"]["referencia_pasarela"],
            status="CONFIRMADO",
            amount_hnl="1000.00",
        )

        # Retención lógica: todavía NO disponible en billetera antes de COMPLETADA (CA-03)
        w_retained = wallet_overview(self.db, tec)
        self.assertEqual(w_retained["saldo_disponible"], "0.00")
        self.assertEqual(w_retained["saldo_retenido_servicios"], "1000.00")

        # Ahora el técnico sí puede aceptar (CA-01)
        respond_to_direct_request(req.id, "ACEPTADA", {"uid": tec.firebase_uid}, self.db)
        req.estado = "EN_PROCESO"
        req.pendiente_recepcion = True
        req.finalizacion_reportada_en = datetime.now(timezone.utc)
        self.db.commit()

        # Al completar, libera retención y acredita neto en billetera (CA-03)
        confirm_service_reception_by_client(req.id, {"uid": cli.firebase_uid}, self.db)
        w_closed = wallet_overview(self.db, tec)
        self.assertEqual(w_closed["saldo_disponible"], "900.00")
        self.assertEqual(w_closed["saldo_retenido_servicios"], "0.00")

        summary = svc.get_service_digital_payments_summary("DIRECTA", req.id, viewer=cli)
        self.assertEqual(summary["cobro_base"]["estado"], "CONFIRMADO")
        self.assertEqual(summary["cobro_base"]["estado_retencion"], "LIBERADO")

    def test_ca04_duplicate_webhooks_retries_and_unauthenticated_rejection(self):
        cli, _, req = self._seed_direct_request(suffix="ca04", monto_base=Decimal("450.00"))
        gw = ControllableRefundGateway()
        repo = SqlAlchemyDigitalPaymentRepository(self.db)
        svc = DigitalPaymentIntentService(self.db, repo, gw)
        rec = PaymentReconciliationService(
            self.db,
            repo,
            SqlAlchemyWebhookEventRepository(self.db),
            gw,
            observers=(ServicePaymentConfirmationObserver(),),
        )

        # Reintento del usuario con misma clave de idempotencia no crea segundo cobro
        a1 = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-dup")
        a2 = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-dup")
        self.assertFalse(a1["reintento_idempotente"])
        self.assertTrue(a2["reintento_idempotente"])
        self.assertEqual(a1["intento"]["id"], a2["intento"]["id"])
        self.assertEqual(gw.create_payment_calls, 1)

        # Webhook sin firma válida es rechazado
        raw = json.dumps({
            "event_id": "evt-dup-1",
            "event_type": "dummy.succeeded",
            "external_reference": a1["intento"]["referencia_pasarela"],
            "payment_status": "CONFIRMADO",
            "amount_hnl": "450.00",
            "currency": "HNL",
        }).encode("utf-8")
        with self.assertRaises(PaymentGatewayAuthenticationError):
            rec.process_webhook(raw, {"X-TekniGO-Signature": "firma-falsa"})

        # Webhook válido 2 veces -> procesado 1 sola vez sin duplicar asiento
        sig = gw.sign_webhook_payload(raw)
        w1 = rec.process_webhook(raw, {"X-TekniGO-Signature": sig})
        w2 = rec.process_webhook(raw, {"X-TekniGO-Signature": sig})
        self.assertTrue(w1["procesado"])
        self.assertFalse(w2["procesado"])
        self.assertTrue(w2["reintento_idempotente"])

        # Intentar crear otro cobro BASE ya confirmado se rechaza con 409
        with self.assertRaises(DigitalPaymentConflictError):
            svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-otro")

        ledger_rows = list(
            self.db.scalars(
                select(FinancialLedgerEntry).where(
                    FinancialLedgerEntry.solicitud_id == req.id,
                    FinancialLedgerEntry.tipo_movimiento == "COBRO_DIGITAL_CONFIRMADO",
                )
            ).all()
        )
        self.assertEqual(len(ledger_rows), 1)

    def test_ca05_and_hu39_mixed_card_base_cash_adjustment_from_charge_to_closure(self):
        """Integración HU-38 + HU-39 + HU-40 + HU-41: base tarjeta (500) + ajuste efectivo (200) -> comisión sobre 700."""
        cli, tec, req = self._seed_direct_request(suffix="hu39mix", monto_base=Decimal("500.00"))
        gw = ControllableRefundGateway()
        repo = SqlAlchemyDigitalPaymentRepository(self.db)
        svc = DigitalPaymentIntentService(self.db, repo, gw)
        rec = PaymentReconciliationService(
            self.db,
            repo,
            SqlAlchemyWebhookEventRepository(self.db),
            gw,
            observers=(ServicePaymentConfirmationObserver(),),
        )

        # 1. Pago inicial BASE con tarjeta (L. 500.00)
        base_att = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-mix-base")
        self._confirm_webhook(
            gw,
            rec,
            event_id="evt-mix-base",
            external_ref=base_att["intento"]["referencia_pasarela"],
            status="CONFIRMADO",
            amount_hnl="500.00",
        )
        respond_to_direct_request(req.id, "ACEPTADA", {"uid": tec.firebase_uid}, self.db)
        req.estado = "EN_PROCESO"
        self.db.commit()

        # 2. Ajuste positivo (+L. 200.00 -> total L. 700.00) aprobado en EFECTIVO (CA-05 / HU-39)
        adj = ServiceAdjustment(
            modalidad="DIRECTA",
            solicitud_id=req.id,
            tecnico_uid=tec.firebase_uid,
            cliente_uid=cli.firebase_uid,
            motivo="Material adicional",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("700.00"),
            diferencia=Decimal("200.00"),
            estado="PENDIENTE",
            solicitado_en=datetime.now(timezone.utc),
        )
        self.db.add(adj)
        self.db.commit()

        respond_adjustment(
            self.db,
            req.id,
            decision=AdjustmentDecision.APROBAR,
            comentario="Pago diferencia en efectivo",
            metodo_pago_diferencia="efectivo",
            claims={"uid": cli.firebase_uid},
            repository=SqlAlchemyAdjustmentRepository(),
        )

        # HU-38 NO debe permitir cobro digital para una diferencia ya pactada en efectivo (CA-05)
        with self.assertRaises(DigitalPaymentConflictError):
            svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="AJUSTE", idempotency_key="idem-mix-bad")

        # Consulta HU-38 para HU-39: solo figura BASE digital (500.00); AJUSTE digital es None
        summary = svc.get_service_digital_payments_summary("DIRECTA", req.id, viewer=tec)
        self.assertEqual(summary["monto_base_digital_confirmado"], "500.00")
        self.assertEqual(summary["monto_ajuste_digital_confirmado"], "0.00")
        self.assertEqual(summary["monto_digital_confirmado_total"], "500.00")
        self.assertIsNone(summary["cobro_ajuste"])

        # 3. Cierre del servicio (COMPLETADA) + registro de efectivo HU-39
        req.pendiente_recepcion = True
        req.finalizacion_reportada_en = datetime.now(timezone.utc)
        self.db.commit()
        confirm_service_reception_by_client(req.id, {"uid": cli.firebase_uid}, self.db)
        cash_res = register_cash_collection(self.db, tec.firebase_uid, req.id)
        self.assertEqual(cash_res["efectivo"], "200.00")
        self.assertEqual(cash_res["digital"], "500.00")
        self.assertEqual(cash_res["comision"], "70.00")  # 10% de 700.00

        # Cada componente figura exactamente una vez y billetera refleja 500 - 70 = 430 disponible + 200 efectivo
        wallet = wallet_overview(self.db, tec)
        self.assertEqual(wallet["saldo_disponible"], "430.00")
        self.assertEqual(wallet["efectivo_recibido"], "200.00")
        self.assertEqual(wallet["comisiones_pendientes"], "0.00")

        receipt = service_receipt(req.id, Response(), claims={"uid": cli.firebase_uid}, db=self.db)
        self.assertEqual(receipt["monto_total"], "700.00")
        self.assertEqual(receipt["monto_comision"], "70.00")
        self.assertEqual(receipt["cobrado_digital"], "500.00")
        self.assertEqual(receipt["cobrado_efectivo"], "200.00")
        self.assertEqual(receipt["referencia_pasarela_base"], base_att["intento"]["referencia_pasarela"])
        self.assertIsNone(receipt["referencia_pasarela_ajuste"])

    def test_ca05_ca06_ca07_card_adjustment_refund_failure_retry_and_concurrent_cancellation(self):
        cli, tec, req = self._seed_direct_request(suffix="ca567", monto_base=Decimal("500.00"), estado="EN_PROCESO")
        gw = ControllableRefundGateway()
        repo = SqlAlchemyDigitalPaymentRepository(self.db)
        svc = DigitalPaymentIntentService(self.db, repo, gw)
        rec = PaymentReconciliationService(
            self.db,
            repo,
            SqlAlchemyWebhookEventRepository(self.db),
            gw,
            observers=(ServicePaymentConfirmationObserver(),),
        )

        # Base (500.00) + ajuste positivo con tarjeta (+125.55)
        b = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="BASE", idempotency_key="idem-b567")
        self._confirm_webhook(gw, rec, event_id="evt-b567", external_ref=b["intento"]["referencia_pasarela"], amount_hnl="500.00")

        adj = ServiceAdjustment(
            modalidad="DIRECTA",
            solicitud_id=req.id,
            tecnico_uid=tec.firebase_uid,
            cliente_uid=cli.firebase_uid,
            motivo="Repuesto extra",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("625.55"),
            diferencia=Decimal("125.55"),
            estado="APROBADO",
            solicitado_en=datetime.now(timezone.utc),
        )
        self.db.add(adj)
        req.monto_ajuste = Decimal("125.55")
        req.monto_total = Decimal("625.55")
        self.db.commit()

        a = svc.create_attempt(cli, solicitud_id=req.id, modalidad="DIRECTA", concepto="AJUSTE", idempotency_key="idem-a567")
        self.assertEqual(a["intento"]["monto"], "125.55")  # CA-05: solo diferencia positiva
        self._confirm_webhook(gw, rec, event_id="evt-a567", external_ref=a["intento"]["referencia_pasarela"], amount_hnl="125.55")

        # Cancelación con fallo de pasarela -> PENDIENTE_CONCILIACION sin declarar devuelto (CA-06/CA-07)
        gw.fail_refund = True
        cancel_res = cancel_request(
            self.db,
            req.id,
            causa=CancellationCause.VOLUNTARIA,
            motivo="Cancelación antes de completar con fallo temporal en pasarela",
            evidencias=[],
            claims={"uid": cli.firebase_uid},
            policy=default_cancellation_policy(),
            repository=SqlAlchemyCancellationRepository(),
            observers=[DigitalRefundOnCancellationObserver(gateway=gw)],
        )
        self.assertEqual(cancel_res["reembolso"]["monto_reembolso"], "625.55")
        self.assertEqual(cancel_res["reembolso"]["monto_comision"], "0.00")
        self.assertEqual(cancel_res["reembolso"]["estado_pasarela"], "PENDIENTE_CONCILIACION")
        self.assertFalse(cancel_res["reembolso"]["reembolsado"])

        # Cancelación concurrente/duplicada es idempotente: no crea segundo reembolso ni vuelve a invocar la pasarela
        refund_calls_before = gw.create_refund_calls
        dup_cancel = cancel_request(
            self.db,
            req.id,
            causa=CancellationCause.VOLUNTARIA,
            motivo="Segundo intento concurrente de cancelación",
            evidencias=[],
            claims={"uid": cli.firebase_uid},
            policy=default_cancellation_policy(),
            repository=SqlAlchemyCancellationRepository(),
            observers=[DigitalRefundOnCancellationObserver(gateway=gw)],
        )
        self.assertEqual(dup_cancel["reembolso"]["id"], cancel_res["reembolso"]["id"])
        self.assertEqual(gw.create_refund_calls, refund_calls_before)
        self.assertEqual(len(list(self.db.scalars(select(PaymentRefund)).all())), 1)

        # Reintento exitoso -> CONFIRMADO_PASARELA y ambos intentos marcados REEMBOLSADO
        gw.fail_refund = False
        refund_svc = DigitalRefundService(self.db, repo, gw)
        retried = refund_svc.retry_or_reconcile_refund(cli, "DIRECTA", req.id)
        self.assertEqual(retried["estado_pasarela"], "CONFIRMADO_PASARELA")
        self.assertTrue(retried["reembolsado"])
        self.assertEqual(retried["intentos"], 2)

        # Reintento posterior ya confirmado es idempotente
        again = refund_svc.retry_or_reconcile_refund(cli, "DIRECTA", req.id)
        self.assertTrue(again["reintento_idempotente"])
        summary = svc.get_service_digital_payments_summary("DIRECTA", req.id, viewer=cli)
        self.assertEqual(summary["cobro_base"]["estado"], "REEMBOLSADO")
        self.assertEqual(summary["cobro_ajuste"]["estado"], "REEMBOLSADO")

    def test_stripe_sandbox_adapter_http_and_hmac_signature(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/checkout/sessions" and request.method == "POST":
                self.assertEqual(request.headers.get("Idempotency-Key"), "idem-stripe-1")
                return httpx.Response(200, json={
                    "id": "cs_test_abc123",
                    "url": "https://checkout.stripe.com/c/pay/cs_test_abc123",
                    "status": "open",
                    "payment_status": "unpaid",
                })
            if request.url.path == "/v1/checkout/sessions/cs_test_abc123" and request.method == "GET":
                return httpx.Response(200, json={
                    "id": "cs_test_abc123",
                    "status": "complete",
                    "payment_status": "paid",
                    "amount_total": 35075,
                    "currency": "hnl",
                    "payment_intent": {"id": "pi_test_xyz", "status": "succeeded", "amount": 35075},
                })
            if request.url.path == "/v1/refunds" and request.method == "POST":
                return httpx.Response(200, json={"id": "re_test_1", "status": "succeeded", "amount": 35075, "currency": "hnl"})
            return httpx.Response(404, json={"error": {"message": "not found"}})

        adapter = StripeSandboxGatewayAdapter(
            secret_key="sk_test_mock",
            webhook_secret="whsec_mock",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        created = adapter.create_payment(CreatePaymentRequest(
            internal_reference="TKGO-DIR-BASE-1",
            amount_hnl=Decimal("350.75"),
            idempotency_key="idem-stripe-1",
            description="Servicio prueba",
            success_url="http://127.0.0.1:5173/ok",
            cancel_url="http://127.0.0.1:5173/cancel",
        ))
        self.assertEqual(created.external_reference, "cs_test_abc123")
        self.assertEqual(created.status, GatewayPaymentStatus.PENDIENTE)

        reconciled = adapter.get_payment_status("cs_test_abc123")
        self.assertEqual(reconciled.status, GatewayPaymentStatus.CONFIRMADO)
        self.assertEqual(reconciled.payment_intent_id, "pi_test_xyz")
        self.assertEqual(reconciled.amount_hnl, Decimal("350.75"))

        raw = json.dumps({
            "id": "evt_stripe_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_abc123", "payment_intent": "pi_test_xyz", "payment_status": "paid", "amount_total": 35075, "currency": "hnl"}},
        }).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(b"whsec_mock", f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
        evt = adapter.verify_and_parse_webhook(raw, {"Stripe-Signature": f"t={ts},v1={sig}"})
        self.assertEqual(evt.payment_status, GatewayPaymentStatus.CONFIRMADO)
        self.assertEqual(evt.amount_hnl, Decimal("350.75"))

        with self.assertRaises(PaymentGatewayAuthenticationError):
            adapter.verify_and_parse_webhook(raw, {"Stripe-Signature": f"t={ts},v1=invalida"})


if __name__ == "__main__":
    unittest.main()