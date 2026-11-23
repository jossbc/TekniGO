"""HU-36: pruebas unitarias de cancelación, reapertura, ajuste único y evidencia privada."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fastapi import HTTPException

from app.financial_settlement import (
    RefundOnCancellationObserver,
    calculate_payment_split,
    confirm_gateway_refund,
)

from app.adjustment_policy import (
    AdjustmentContext,
    AdjustmentRuleViolation,
    default_adjustment_policy,
)
from app.adjustment_repository import SqlAlchemyAdjustmentRepository
from app.adjustment_service import (
    AdjustmentDecision,
    request_adjustment,
    respond_adjustment,
    to_money,
)
from app.cancellation_justification_service import (
    JustificationDecision,
    resolve_justification,
    submit_justification,
)
from app.cancellation_repository import (
    SqlAlchemyCancellationRepository,
    SqlAlchemyJustificationRepository,
)
from app.cancellation_service import cancel_request
from app.database import Base
from app.models import DirectRequest,OpenRequest,Service, Specialty, User, UserRole
from app.reopen_service import reopen_cancelled_request
from app.service_completion import has_pending_adjustment
from app.cancellation_evidence import PrivateEvidenceStore
from app.cancellation_policy import (
    CancellationCause as Cause,
    CancellationContext,
    CancellationNotAllowed,
    default_cancellation_policy,
)
from app.reopen_sources import default_reopen_sources
from app.storage import PrivateDocumentStorage

SECRET = "test-only-private-storage-secret-32-bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)


class CancellationPolicyTests(unittest.TestCase):
    """ST-01 (CA-01, CA-02, CA-03, CA-06)."""

    def setUp(self):
        self.policy = default_cancellation_policy()

    def ctx(self, state, role, cause=Cause.VOLUNTARIA, hours=48, adjustment_rejected=False):
        return CancellationContext(
            state, role, cause, NOW + timedelta(hours=hours), NOW,
            adjustment_rejected=adjustment_rejected,
        )

    def test_before_acceptance_never_generates_fault(self):
        for state in ("PENDIENTE", "PUBLICADA", "CON OFERTAS"):
            for role in ("CLIENTE", "TECNICO"):
                with self.subTest(state=state, role=role):
                    decision = self.policy.evaluate(self.ctx(state, role, hours=1))
                    self.assertFalse(decision.counts_fault)

    def test_client_with_exactly_24_hours_has_no_fault(self):
        self.assertFalse(self.policy.evaluate(self.ctx("ACEPTADA", "CLIENTE", hours=24)).counts_fault)

    def test_client_one_second_under_24_hours_registers_fault(self):
        context = CancellationContext(
            "ACEPTADA", "CLIENTE", Cause.VOLUNTARIA,
            NOW + timedelta(hours=24) - timedelta(seconds=1), NOW,
        )
        decision = self.policy.evaluate(context)
        self.assertTrue(decision.counts_fault)
        self.assertEqual(decision.responsible_role, "CLIENTE")
        self.assertFalse(decision.allows_reopen)

    def test_technician_cancelling_after_accepting_is_fault_and_allows_reopen(self):
        decision = self.policy.evaluate(self.ctx("ACEPTADA", "TECNICO", hours=100))
        self.assertTrue(decision.counts_fault)
        self.assertEqual(decision.responsible_role, "TECNICO")
        self.assertTrue(decision.allows_reopen)

    def test_technician_no_show_reported_by_client_allows_reopen(self):
        decision = self.policy.evaluate(self.ctx("EN PROCESO", "CLIENTE", Cause.INASISTENCIA_CONTRAPARTE, hours=-1))
        self.assertEqual(decision.responsible_role, "TECNICO")
        self.assertTrue(decision.allows_reopen)

    def test_client_no_show_reported_by_technician_does_not_allow_reopen(self):
        decision = self.policy.evaluate(self.ctx("ACEPTADA", "TECNICO", Cause.INASISTENCIA_CONTRAPARTE, hours=-1))
        self.assertEqual(decision.responsible_role, "CLIENTE")
        self.assertFalse(decision.allows_reopen)

    def test_rejected_adjustment_allows_penalty_free_cancellation_for_both_parties(self):
        for role in ("CLIENTE", "TECNICO"):
            with self.subTest(role=role):
                decision = self.policy.evaluate(
                    self.ctx("EN PROCESO", role, Cause.AJUSTE_RECHAZADO, hours=-2, adjustment_rejected=True)
                )
                self.assertFalse(decision.counts_fault)

    def test_adjustment_cause_without_a_rejected_adjustment_is_not_allowed(self):
        with self.assertRaises(CancellationNotAllowed):
            self.policy.evaluate(self.ctx("EN PROCESO", "CLIENTE", Cause.AJUSTE_RECHAZADO))

    def test_no_show_before_acceptance_is_not_allowed(self):
        with self.assertRaises(CancellationNotAllowed):
            self.policy.evaluate(self.ctx("PENDIENTE", "CLIENTE", Cause.INASISTENCIA_CONTRAPARTE))


class ReopenSourceTests(unittest.TestCase):
    """ST-02 (CA-03): borrador de la nueva licitación."""

    @staticmethod
    def pick(record):
        return next(source for source in default_reopen_sources() if source.supports(record))

    def test_direct_request_uses_service_specialty_and_base_amount(self):
        record = DirectRequest(
            descripcion="Reparar tubería", municipio="Tegucigalpa", sector="Kennedy",
            direccion_exacta="Calle 1", metodo_pago="tarjeta", monto_base=Decimal("350.25"),
        )
        record.servicio = SimpleNamespace(especialidad_id="esp-1")
        draft = self.pick(record).to_draft(record)
        self.assertEqual(draft.especialidad_id, "esp-1")
        self.assertEqual(draft.presupuesto, Decimal("350.25"))

    def test_open_request_falls_back_to_budget_when_base_is_missing(self):
        record = OpenRequest(
            especialidad_id="esp-2", descripcion="Instalar luz", municipio="SPS", sector="Centro",
            direccion_exacta="Av 2", metodo_pago="efectivo", presupuesto=Decimal("100.10"), monto_base=None,
        )
        self.assertEqual(self.pick(record).to_draft(record).presupuesto, Decimal("100.10"))


class AdjustmentPolicyTests(unittest.TestCase):
    """ST-03 (CA-04, CA-05) y política de redondeo."""

    def setUp(self):
        self.policy = default_adjustment_policy()

    @staticmethod
    def make(**overrides):
        values = dict(
            actor_is_selected_technician=True, request_state="EN PROCESO", already_requested=False,
            monto_base=Decimal("500.00"), nuevo_total=Decimal("650.00"), agreed_total=Decimal("500.00"),
        )
        values.update(overrides)
        return AdjustmentContext(**values)

    def violation_status(self, context):
        with self.assertRaises(AdjustmentRuleViolation) as error:
            self.policy.check(context)
        return error.exception.status_code

    def test_valid_adjustment_passes(self):
        self.policy.check(self.make())

    def test_decrease_with_positive_total_passes(self):
        self.policy.check(self.make(nuevo_total=Decimal("0.01")))

    def test_only_selected_technician_may_request(self):
        self.assertEqual(self.violation_status(self.make(actor_is_selected_technician=False)), 403)

    def test_only_in_progress_state_allowed(self):
        for state in ("ACEPTADA", "COMPLETADA", "CANCELADA"):
            with self.subTest(state=state):
                self.assertEqual(self.violation_status(self.make(request_state=state)), 409)

    def test_second_attempt_is_rejected_even_if_first_was_rejected(self):
        self.assertEqual(self.violation_status(self.make(already_requested=True)), 409)

    def test_invalid_totals_are_rejected(self):
        for total in (Decimal("0"), Decimal("-1"), Decimal("500.00")):
            with self.subTest(total=total):
                self.assertEqual(self.violation_status(self.make(nuevo_total=total)), 400)

    def test_base_must_match_current_agreed_total(self):
        self.assertEqual(self.violation_status(self.make(agreed_total=Decimal("480.00"))), 409)

    def test_rounding_policy_is_half_up_to_cents(self):
        cases = (("10.005", "10.01"), ("10.004", "10.00"), ("0.125", "0.13"))
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(to_money(Decimal(raw)), Decimal(expected))


class CancellationEvidenceTests(unittest.TestCase):
    """ST-04: evidencia privada, propiedad y rechazo de referencias arbitrarias."""

    def setUp(self):
        env = patch.dict(os.environ, {"PRIVATE_STORAGE_SECRET": SECRET})
        env.start()
        self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = PrivateEvidenceStore(PrivateDocumentStorage(base_dir=Path(directory.name)))

    def test_saved_evidence_belongs_only_to_its_author_and_is_readable(self):
        owner, other = uuid.uuid4(), uuid.uuid4()
        reference = self.store.save(
            owner_id=owner, content=PNG, content_type="image/png", label="la evidencia",
        )["referencia"]
        self.store.ensure_owned(owner, [reference])
        self.assertEqual(self.store.read(reference), PNG)
        with self.assertRaises(HTTPException):
            self.store.ensure_owned(other, [reference])

    def test_arbitrary_references_are_rejected(self):
        for reference in ("", "texto libre", "data:image/png;base64,AAAA", "private://x/y"):
            with self.subTest(reference=reference):
                with self.assertRaises(HTTPException):
                    self.store.ensure_owned(uuid.uuid4(), [reference])

    def test_more_than_ten_files_are_rejected(self):
        with self.assertRaises(HTTPException):
            self.store.ensure_owned(uuid.uuid4(), ["x"] * 11)

class HU36ServiceWorkflowTests(unittest.TestCase):
    """Pruebas de flujo en base de datos para ajuste único, aprobación/rechazo, cancelación, reapertura y justificación."""

    def setUp(self):
        env = patch.dict(os.environ, {"PRIVATE_STORAGE_SECRET": SECRET})
        env.start()
        self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.evidence = PrivateEvidenceStore(PrivateDocumentStorage(base_dir=Path(directory.name)))

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        self.client = self._add_user("client-hu36", "Ana Cliente", "CLIENTE", "1")
        self.technician = self._add_user("tech-hu36", "Carlos Técnico", "TECNICO", "2")
        self.admin = self._add_user("admin-hu36", "Admin Moderador", "ADMIN", "9")

        self.specialty = Specialty(nombre="Electricidad")
        self.db.add(self.specialty)
        self.db.flush()
        self.service = Service(
            usuario_id=self.technician.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación de tablero",
            descripcion="Revisión y cambio de breakers",
            tarifa_base=Decimal("500.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

        firebase_user = SimpleNamespace(
            email=self.client.correo,
            email_verified=True,
            disabled=False,
            phone_number=self.client.telefono,
        )
        provider = patch("app.email_verification.auth.get_user", return_value=firebase_user)
        provider.start()
        self.addCleanup(provider.stop)

        self.cancellation_policy = default_cancellation_policy()
        self.adjustment_policy = default_adjustment_policy()
        self.cancellations = SqlAlchemyCancellationRepository()
        self.adjustments = SqlAlchemyAdjustmentRepository()
        self.justifications = SqlAlchemyJustificationRepository()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080119950000{suffix}",
            telefono=f"+5049600000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _add_direct_request(self, *, estado: str = "EN PROCESO", hours_ahead: int = 48) -> DirectRequest:
        record = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.technician.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle principal, casa 10",
            fecha_hora=NOW + timedelta(hours=hours_ahead),
            descripcion="Tablero con cortocircuito en cocina",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("500.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal("500.00"),
            metodo_pago="efectivo",
            estado=estado,
            creada_en=NOW,
        )
        self.db.add(record)
        self.db.commit()
        return record

    def _add_open_request(self, *, estado: str = "ACEPTADA", hours_ahead: int = 12) -> OpenRequest:
        record = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.technician.firebase_uid,
            especialidad_id=str(self.specialty.id),
            modalidad="ABIERTA",
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle principal, casa 4",
            fecha_hora=NOW + timedelta(hours=hours_ahead),
            descripcion="Tablero con cortocircuito en cocina",
            presupuesto=Decimal("500.00"),
            monto_base=Decimal("500.00"),
            metodo_pago="efectivo",
            estado=estado,
            creada_en=NOW,
        )
        self.db.add(record)
        self.db.commit()
        return record

    def test_approve_adjustment_updates_agreed_total_and_is_idempotent(self):
        """CA-04 y CA-05: el ajuste pendiente bloquea cierre; al aprobar actualiza total y es idempotente."""
        record = self._add_direct_request(estado="EN PROCESO")
        tech_claims = {"uid": self.technician.firebase_uid}
        client_claims = {"uid": self.client.firebase_uid}

        created = request_adjustment(
            self.db, record.id,
            motivo="Se requiere reemplazar breaker principal quemado.",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("680.50"),
            claims=tech_claims,
            policy=self.adjustment_policy,
            repository=self.adjustments,
            now=NOW,
        )
        self.assertEqual(created["estado"], "PENDIENTE")
        self.assertEqual(created["diferencia"], "180.50")

        # Mientras está pendiente, HU-34 bloquea reportar finalización
        self.assertTrue(has_pending_adjustment(record, self.db)[0])

        # El técnico no puede aprobar su propio ajuste
        with self.assertRaises(HTTPException) as forbidden:
            respond_adjustment(
                self.db, record.id,
                decision=AdjustmentDecision.APROBAR,
                comentario=None,
                claims=tech_claims,
                repository=self.adjustments,
                now=NOW,
            )
        self.assertEqual(forbidden.exception.status_code, 403)

        approved = respond_adjustment(
            self.db, record.id,
            decision=AdjustmentDecision.APROBAR,
            comentario="De acuerdo con el cambio de pieza.",
            claims=client_claims,
            repository=self.adjustments,
            now=NOW,
        )
        self.assertEqual(approved["estado"], "APROBADO")
        self.db.refresh(record)
        self.assertEqual(to_money(record.monto_total), Decimal("680.50"))
        self.assertEqual(to_money(record.monto_ajuste), Decimal("180.50"))
        self.assertFalse(has_pending_adjustment(record, self.db)[0])

        # Reintento con la misma decisión es idempotente; cambiar a RECHAZAR responde 409
        retry = respond_adjustment(
            self.db, record.id,
            decision=AdjustmentDecision.APROBAR,
            comentario="De acuerdo con el cambio de pieza.",
            claims=client_claims,
            repository=self.adjustments,
            now=NOW,
        )
        self.assertEqual(retry["id"], approved["id"])
        with self.assertRaises(HTTPException) as conflict:
            respond_adjustment(
                self.db, record.id,
                decision=AdjustmentDecision.RECHAZAR,
                comentario="Cambio de opinión",
                claims=client_claims,
                repository=self.adjustments,
                now=NOW,
            )
        self.assertEqual(conflict.exception.status_code, 409)

    def test_reject_adjustment_keeps_total_and_enables_penalty_free_cancellation(self):
        """CA-04, CA-05 y CA-06: el rechazo no cambia el total, impide segundo ajuste y permite cancelar sin falta."""
        record = self._add_direct_request(estado="EN PROCESO", hours_ahead=1)
        tech_claims = {"uid": self.technician.firebase_uid}
        client_claims = {"uid": self.client.firebase_uid}

        request_adjustment(
            self.db, record.id,
            motivo="Cableado adicional oculto en pared.",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("750.00"),
            claims=tech_claims,
            policy=self.adjustment_policy,
            repository=self.adjustments,
            now=NOW,
        )
        rejected = respond_adjustment(
            self.db, record.id,
            decision=AdjustmentDecision.RECHAZAR,
            comentario="No cuento con ese presupuesto adicional.",
            claims=client_claims,
            repository=self.adjustments,
            now=NOW,
        )
        self.assertEqual(rejected["estado"], "RECHAZADO")
        self.db.refresh(record)
        self.assertIsNone(record.monto_total)
        self.assertEqual(to_money(record.monto_ajuste), Decimal("0.00"))

        # CA-04: un segundo intento se rechaza aunque el primero haya sido rechazado
        with self.assertRaises(HTTPException) as second_attempt:
            request_adjustment(
                self.db, record.id,
                motivo="Segundo intento con monto menor.",
                monto_base=Decimal("500.00"),
                nuevo_total=Decimal("580.00"),
                claims=tech_claims,
                policy=self.adjustment_policy,
                repository=self.adjustments,
                now=NOW,
            )
        self.assertEqual(second_attempt.exception.status_code, 409)

        # CA-06: cancelar por ajuste rechazado no genera falta aunque falte 1 hora
        cancelled = cancel_request(
            self.db, record.id,
            causa=Cause.AJUSTE_RECHAZADO,
            motivo="No se llegó a un acuerdo sobre el ajuste en sitio.",
            evidencias=[],
            claims=tech_claims,
            policy=self.cancellation_policy,
            repository=self.cancellations,
            adjustments=self.adjustments,
            evidence=self.evidence,
            now=NOW,
        )
        self.assertEqual(cancelled["estado"], "CANCELADA")
        self.assertFalse(cancelled["genera_incumplimiento"])
        self.assertEqual(cancelled["estado_incumplimiento"], "NO_APLICA")

    def test_cancel_after_acceptance_allows_reopen_and_justification_approval(self):
        """CA-01, CA-03 y CA-09: técnico cancela tras aceptar, cliente reabre y admin aprueba justificación."""
        record = self._add_open_request(estado="ACEPTADA", hours_ahead=12)
        tech_claims = {"uid": self.technician.firebase_uid}
        client_claims = {"uid": self.client.firebase_uid}
        admin_claims = {"uid": self.admin.firebase_uid}

        cancelled = cancel_request(
            self.db, record.id,
            causa=Cause.VOLUNTARIA,
            motivo="Avería mecánica de camino al domicilio.",
            evidencias=[],
            claims=tech_claims,
            policy=self.cancellation_policy,
            repository=self.cancellations,
            adjustments=self.adjustments,
            evidence=self.evidence,
            now=NOW,
        )
        self.assertTrue(cancelled["genera_incumplimiento"])
        self.assertEqual(cancelled["responsable_rol"], "TECNICO")
        self.assertTrue(cancelled["permite_reapertura"])
        self.assertEqual(cancelled["estado_incumplimiento"], "PENDIENTE_JUSTIFICACION")

        # Reintento idempotente: Cancelada es final
        retry_cancel = cancel_request(
            self.db, record.id,
            causa=Cause.VOLUNTARIA,
            motivo="Reintento de cancelación.",
            evidencias=[],
            claims=tech_claims,
            policy=self.cancellation_policy,
            repository=self.cancellations,
            adjustments=self.adjustments,
            evidence=self.evidence,
            now=NOW,
        )
        self.assertEqual(retry_cancel["solicitud_id"], record.id)

        # CA-03: cliente reabre como nueva licitación vinculada; la original sigue CANCELADA
        reopened = reopen_cancelled_request(
            self.db, record.id,
            fecha_hora=NOW + timedelta(days=2),
            metodo_pago=None,
            presupuesto=None,
            claims=client_claims,
            repository=self.cancellations,
            sources=default_reopen_sources(),
            now=NOW,
        )
        self.assertEqual(reopened["solicitud_origen_id"], record.id)
        self.assertFalse(reopened["reabierta_existente"])
        self.db.refresh(record)
        self.assertEqual(record.estado, "CANCELADA")

        # CA-09: técnico envía justificación con evidencia privada y admin la aprueba
        ref = self.evidence.save(
            owner_id=self.technician.id, content=PNG, content_type="image/png", label="evidencia",
        )["referencia"]
        just = submit_justification(
            self.db, record.id,
            motivo="Adjunto comprobante de grúa por falla mecánica imprevista.",
            evidencias=[ref],
            claims=tech_claims,
            cancellations=self.cancellations,
            justifications=self.justifications,
            evidence=self.evidence,
            now=NOW,
        )
        self.assertEqual(just["estado"], "PENDIENTE")

        resolved = resolve_justification(
            self.db, just["id"],
            decision=JustificationDecision.APROBAR,
            observaciones="Comprobante de fuerza mayor verificado.",
            claims=admin_claims,
            cancellations=self.cancellations,
            justifications=self.justifications,
            now=NOW,
        )
        self.assertEqual(resolved["estado"], "APROBADA")
        stored_cancellation = self.cancellations.get(self.db, "ABIERTA", record.id)
        self.assertEqual(stored_cancellation.estado_incumplimiento, "JUSTIFICADA")
    
    def test_approved_increase_on_card_payment_records_split_and_final_total_commission(self):
        """CA-07: aumento aprobado sobre tarjeta permite diferencia en efectivo o tarjeta y comisión usa total final."""
        record = self._add_direct_request(estado="EN PROCESO")
        record.metodo_pago = "tarjeta"
        record.referencia_pasarela = "TKGO-GW-BASE-001"
        self.db.commit()

        tech_claims = {"uid": self.technician.firebase_uid}
        client_claims = {"uid": self.client.firebase_uid}

        request_adjustment(
            self.db, record.id,
            motivo="Instalación de protector de voltaje adicional.",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("650.55"),
            claims=tech_claims,
            policy=self.adjustment_policy,
            repository=self.adjustments,
            now=NOW,
        )
        approved = respond_adjustment(
            self.db, record.id,
            decision=AdjustmentDecision.APROBAR,
            comentario="Pago la diferencia en efectivo en sitio.",
            metodo_pago_diferencia="efectivo",
            claims=client_claims,
            repository=self.adjustments,
            now=NOW,
        )
        split = approved["reparto_financiero"]
        self.assertEqual(split["estrategia"], "MIXTO_TARJETA_EFECTIVO")
        self.assertEqual(split["cobrado_digital"], "500.00")
        self.assertEqual(split["cobrado_efectivo"], "150.55")
        self.assertEqual(split["monto_total"], "650.55")
        # 10% sobre el total final 650.55 = 65.055 -> redondeado HALF_UP a 65.06
        self.assertEqual(split["monto_comision"], "65.06")
        self.assertEqual(split["credito_digital_tecnico"], "434.94")
        self.assertEqual(split["deuda_comision_efectivo"], "0.00")

    def test_digital_cancellation_initiates_100_percent_refund_without_commission_and_third_fault_alerts(self):
        """CA-08 y CA-10: devolución 100% digital sin comisión, confirmación idempotente y alerta a la 3.ª falta."""
        observer = RefundOnCancellationObserver()
        tech_claims = {"uid": self.technician.firebase_uid}

        last_result = None
        for idx in range(3):
            record = self._add_direct_request(estado="EN PROCESO", hours_ahead=4)
            record.metodo_pago = "tarjeta"
            record.referencia_pasarela = f"TKGO-GW-BASE-{idx}"
            record.monto_ajuste = Decimal("120.25")
            record.metodo_pago_ajuste = "tarjeta"
            record.referencia_pasarela_ajuste = f"TKGO-GW-ADJ-{idx}"
            record.monto_total = Decimal("620.25")
            self.db.commit()

            last_result = cancel_request(
                self.db, record.id,
                causa=Cause.VOLUNTARIA,
                motivo=f"Imprevisto técnico en servicio #{idx + 1}.",
                evidencias=[],
                claims=tech_claims,
                policy=self.cancellation_policy,
                repository=self.cancellations,
                observers=[observer],
                adjustments=self.adjustments,
                evidence=self.evidence,
                now=NOW,
            )

        refund = last_result["reembolso"]
        self.assertIsNotNone(refund)
        self.assertEqual(refund["monto_reembolso"], "620.25")
        self.assertEqual(refund["monto_comision"], "0.00")
        self.assertEqual(refund["estado_pasarela"], "EN_PROCESO_PASARELA")

        # Confirmación por pasarela e idempotencia en reintento
        confirmed = confirm_gateway_refund(self.db, "DIRECTA", refund["solicitud_id"], now=NOW)
        self.assertEqual(confirmed["estado_pasarela"], "CONFIRMADO_PASARELA")
        self.assertFalse(confirmed["reintento_idempotente"])
        retry = confirm_gateway_refund(self.db, "DIRECTA", refund["solicitud_id"], now=NOW)
        self.assertTrue(retry["reintento_idempotente"])

        # CA-10: a la tercera falta no justificada se activa la alerta administrativa sin suspensión automática
        alert = last_result["alerta_administrativa"]
        self.assertEqual(alert["faltas_no_justificadas_acumuladas"], 3)
        self.assertTrue(alert["alerta_administrativa_activa"])
        self.assertFalse(alert["suspension_automatica_aplicada"])


if __name__ == "__main__":
    unittest.main()