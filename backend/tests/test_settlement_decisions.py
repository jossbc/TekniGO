"""HU-42 ST-04: aprobación transaccional, parcial, sobrepago, rechazo, idempotencia y auditoría."""
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import UploadedPart
from app.models import (
    CommissionSettlement,
    CommissionSettlementAudit,
    TechnicianProfile,
    TechnicianStatusAudit,
    User,
    UserRole,
)
from app.settlements_router import (
    SettlementDecisionSchema,
    decide_settlement,
    get_settlement_summary,
    list_settlement_audit,
    submit_settlement,
)
from app.storage import PrivateDocumentStorage

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
DEBT = "app.settlements.commission_debt_generated"


class SettlementDecisionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            "os.environ",
            {
                "PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes",
                "PRIVATE_STORAGE_BACKEND": "local",
                "PRIVATE_STORAGE_DIR": self.tmp.name,
                "SUPABASE_URL": "",
                "SUPABASE_SERVICE_ROLE_KEY": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.storage = PrivateDocumentStorage(base_dir=Path(self.tmp.name))

        self.tech = self._add_user("tech-1", "TECNICO", 1, estado_identidad="VERIFICADO")
        self.admin = self._add_user("adm-1", "ADMIN", 2, estado_identidad="VERIFICADO")
        self.client_user = self._add_user("cli-1", "CLIENTE", 3)
        self.db.add(
            TechnicianProfile(
                usuario_id=self.tech["id"],
                disponible=True,
                estado_verificacion="VERIFICADO",
                suspendido=False,
                restriccion_financiera=True,
                servicios_efectivo_sin_liquidar=5,
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _add_user(self, uid: str, role: str, n: int, *, estado_identidad: str = "PENDIENTE") -> dict:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=f"Usuario {uid}",
            dni=f"08012000000{n:02d}",
            telefono=f"+5049{n:07d}",
            telefono_verificado=True,
            estado_identidad=estado_identidad,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        return {"uid": uid, "id": user.id}

    def _submit(self, *, debt: str, importe: str = "200.00", ref: str = "BAC-1001", content: bytes = PNG, ctype: str = "image/png"):
        with patch(DEBT, return_value=Decimal(debt)):
            return submit_settlement(
                importe=importe,
                referencia=ref,
                comprobante=UploadedPart(content, ctype),
                claims=self.tech,
                db=self.db,
                storage=self.storage,
            )

    def _profile(self) -> TechnicianProfile:
        self.db.expire_all()
        return self.db.get(TechnicianProfile, self.tech["id"])

    # CA-03, CA-04
    def test_total_approval_zeros_debt_resets_cash_counter_removes_block_and_audits(self):
        created = self._submit(debt="250.75", importe="250.75", ref="BAC-TOTAL-1")
        sid = uuid.UUID(created["id"])
        when = datetime(2026, 11, 22, 18, 30, tzinfo=timezone.utc)

        with patch(DEBT, return_value=Decimal("250.75")):
            res = decide_settlement(
                sid,
                SettlementDecisionSchema(decision="APROBAR", importe_verificado="250.75", observacion="Conciliado en estado bancario"),
                self.admin,
                self.db,
                now=when,
            )
            summary = get_settlement_summary(self.tech, self.db)

        self.assertEqual(res["liquidacion"]["estado"], "APROBADA")
        self.assertEqual(res["liquidacion"]["importe_verificado"], "250.75")
        self.assertEqual(res["liquidacion"]["importe_aplicado"], "250.75")
        self.assertEqual(res["liquidacion"]["importe_excedente"], "0.00")
        self.assertEqual(res["liquidacion"]["revisado_por"], str(self.admin["id"]))
        self.assertEqual(res["liquidacion"]["revisado_en"], when.isoformat())
        self.assertEqual(res["efecto_financiero"]["accion"], "APROBACION_TOTAL")
        self.assertEqual(res["efecto_financiero"]["deuda_anterior"], "250.75")
        self.assertEqual(res["efecto_financiero"]["deuda_posterior"], "0.00")
        self.assertEqual(summary["deuda_vigente"], "0.00")

        profile = self._profile()
        self.assertFalse(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 0)

        audit_list = list_settlement_audit(sid, self.admin, self.db)["movimientos"]
        self.assertEqual(len(audit_list), 1)
        self.assertEqual(audit_list[0]["accion"], "APROBACION_TOTAL")
        self.assertEqual(audit_list[0]["referencia"], "BAC-TOTAL-1")
        self.assertEqual(audit_list[0]["contador_anterior"], 5)
        self.assertEqual(audit_list[0]["contador_posterior"], 0)
        self.assertTrue(audit_list[0]["restriccion_anterior"])
        self.assertFalse(audit_list[0]["restriccion_posterior"])

        tech_audits = self.db.scalars(select(TechnicianStatusAudit).where(TechnicianStatusAudit.usuario_id == self.tech["id"])).all()
        self.assertEqual(len(tech_audits), 1)
        self.assertEqual(tech_audits[0].proceso_responsable, "LIQUIDACION_COMISIONES_ADMIN")
        self.assertFalse(tech_audits[0].habilitado_anterior)
        self.assertTrue(tech_audits[0].habilitado_nuevo)

    # CA-05
    def test_partial_approval_applies_only_verified_amount_and_keeps_debt_counter_and_block(self):
        created = self._submit(debt="300.00", importe="300.00", ref="BAC-PART-1")
        sid = uuid.UUID(created["id"])

        with patch(DEBT, return_value=Decimal("300.00")):
            res = decide_settlement(
                sid,
                SettlementDecisionSchema(decision="APROBAR", importe_verificado="120.335", observacion="Solo ingresaron L 120.34"),
                self.admin,
                self.db,
            )
            summary = get_settlement_summary(self.tech, self.db)

        self.assertEqual(res["liquidacion"]["estado"], "APROBADA")
        self.assertEqual(res["liquidacion"]["importe_verificado"], "120.34")
        self.assertEqual(res["liquidacion"]["importe_aplicado"], "120.34")
        self.assertEqual(res["liquidacion"]["importe_excedente"], "0.00")
        self.assertEqual(res["efecto_financiero"]["accion"], "APROBACION_PARCIAL")
        self.assertEqual(res["efecto_financiero"]["deuda_posterior"], "179.66")
        self.assertEqual(summary["deuda_vigente"], "179.66")

        profile = self._profile()
        self.assertTrue(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 5)

    # CA-05: sobrepago
    def test_overpayment_sends_excess_to_conciliation_without_creating_credit(self):
        created = self._submit(debt="100.00", importe="150.25", ref="BAC-OVER-1")
        sid = uuid.UUID(created["id"])

        with patch(DEBT, return_value=Decimal("100.00")):
            res = decide_settlement(
                sid,
                SettlementDecisionSchema(decision="APROBAR", importe_verificado="150.25", observacion="Depósito excede deuda"),
                self.admin,
                self.db,
            )
            summary = get_settlement_summary(self.tech, self.db)

        self.assertEqual(res["liquidacion"]["estado"], "EN_CONCILIACION")
        self.assertEqual(res["liquidacion"]["importe_verificado"], "150.25")
        self.assertEqual(res["liquidacion"]["importe_aplicado"], "100.00")
        self.assertEqual(res["liquidacion"]["importe_excedente"], "50.25")
        self.assertEqual(res["efecto_financiero"]["accion"], "SOBREPAGO_CONCILIACION")
        self.assertEqual(res["efecto_financiero"]["deuda_posterior"], "0.00")
        self.assertEqual(summary["deuda_vigente"], "0.00")

        profile = self._profile()
        self.assertFalse(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 0)

    # CA-06
    def test_repeated_decision_is_rejected_and_does_not_reduce_debt_twice(self):
        created = self._submit(debt="200.00", importe="100.00", ref="BAC-IDEM-1")
        sid = uuid.UUID(created["id"])

        with patch(DEBT, return_value=Decimal("200.00")):
            decide_settlement(
                sid,
                SettlementDecisionSchema(decision="APROBAR", importe_verificado="100.00"),
                self.admin,
                self.db,
            )
            with self.assertRaises(HTTPException) as caught:
                decide_settlement(
                    sid,
                    SettlementDecisionSchema(decision="APROBAR", importe_verificado="100.00"),
                    self.admin,
                    self.db,
                )
            self.assertEqual(caught.exception.status_code, 409)
            summary = get_settlement_summary(self.tech, self.db)

        self.assertEqual(summary["deuda_vigente"], "100.00")
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(CommissionSettlementAudit)),
            1,
        )

    # CA-03, CA-07: rechazo
    def test_rejection_requires_observation_and_keeps_debt_counter_and_block(self):
        created = self._submit(debt="200.00", importe="200.00", ref="BAC-REJ-1")
        sid = uuid.UUID(created["id"])

        with patch(DEBT, return_value=Decimal("200.00")):
            with self.assertRaises(HTTPException) as caught:
                decide_settlement(
                    sid,
                    SettlementDecisionSchema(decision="RECHAZAR", observacion="   "),
                    self.admin,
                    self.db,
                )
            self.assertEqual(caught.exception.status_code, 400)

            res = decide_settlement(
                sid,
                SettlementDecisionSchema(decision="RECHAZAR", observacion="Comprobante borroso sin número de referencia"),
                self.admin,
                self.db,
            )
            summary = get_settlement_summary(self.tech, self.db)

        self.assertEqual(res["liquidacion"]["estado"], "RECHAZADA")
        self.assertIsNone(res["liquidacion"]["importe_verificado"])
        self.assertEqual(res["liquidacion"]["importe_aplicado"], "0.00")
        self.assertEqual(res["efecto_financiero"]["accion"], "RECHAZO")
        self.assertEqual(summary["deuda_vigente"], "200.00")
        profile = self._profile()
        self.assertTrue(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 5)

    # CA-07: retirar bloqueo financiero no elimina suspensiones ni verificación pendiente
    def test_removing_financial_block_preserves_suspension_and_pending_verification(self):
        profile = self._profile()
        profile.suspendido = True
        profile.estado_verificacion = "PENDIENTE"
        self.db.commit()

        created = self._submit(debt="100.00", importe="100.00", ref="BAC-PRES-1")
        sid = uuid.UUID(created["id"])

        with patch(DEBT, return_value=Decimal("100.00")):
            res = decide_settlement(
                sid,
                SettlementDecisionSchema(decision="APROBAR", importe_verificado="100.00"),
                self.admin,
                self.db,
            )

        updated = self._profile()
        self.assertFalse(updated.restriccion_financiera)
        self.assertEqual(updated.servicios_efectivo_sin_liquidar, 0)
        self.assertTrue(updated.suspendido)
        self.assertEqual(updated.estado_verificacion, "PENDIENTE")
        self.assertTrue(res["efecto_financiero"]["suspendido"])
        self.assertEqual(res["efecto_financiero"]["estado_verificacion"], "PENDIENTE")

    def test_non_admin_cannot_decide_or_read_audit_and_float_amount_rejected(self):
        created = self._submit(debt="100.00", importe="100.00", ref="BAC-PERM-1")
        sid = uuid.UUID(created["id"])

        for actor in (self.tech, self.client_user):
            with self.assertRaises(HTTPException) as c1:
                decide_settlement(sid, SettlementDecisionSchema(decision="APROBAR", importe_verificado="100.00"), actor, self.db)
            self.assertEqual(c1.exception.status_code, 403)
            with self.assertRaises(HTTPException) as c2:
                list_settlement_audit(sid, actor, self.db)
            self.assertEqual(c2.exception.status_code, 403)

        with patch(DEBT, return_value=Decimal("100.00")):
            with self.assertRaises(HTTPException) as c3:
                decide_settlement(sid, SettlementDecisionSchema(decision="APROBAR", importe_verificado=100.5), self.admin, self.db)
            self.assertEqual(c3.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
