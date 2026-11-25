"""HU-43 ST-04: bandeja administrativa, cuenta autorizada, confirmación/rechazo e idempotencia."""
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import OperationalSettings, PayoutOrderAudit, User, UserRole
from app.payouts_router import (
    BankAccountInput,
    PayoutDecisionInput,
    PayoutRequestInput,
    decide_payout,
    get_admin_payout_bank_details,
    get_my_payout_summary,
    list_admin_payouts,
    list_payout_audit,
    request_payout,
    save_my_bank_account,
)

CREDITS = "app.payouts.digital_credits_generated"
DEBT = "app.settlements.commission_debt_generated"


class PayoutAdminTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes"})
        self.env.start()
        self.addCleanup(self.env.stop)

        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.tech = self._add_user("tech-1", "TECNICO", 1)
        self.admin = self._add_user("adm-1", "ADMIN", 2)
        self.client_user = self._add_user("cli-1", "CLIENTE", 3)
        self.db.add(
            OperationalSettings(
                id=1,
                comision_porcentaje=Decimal("10.00"),
                limite_deuda=Decimal("500.00"),
                minimo_retiro=Decimal("200.00"),
            )
        )
        self.db.commit()

        save_my_bank_account(
            BankAccountInput(
                banco="Banco Atlántida",
                tipo_cuenta="AHORRO",
                titular_nombre="Carlos Técnico",
                numero_cuenta="1100-2200-3344",
            ),
            self.tech,
            self.db,
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, role: str, n: int) -> dict:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=f"Usuario {uid}",
            dni=f"08012000000{n:02d}",
            telefono=f"+5049{n:07d}",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        return {"uid": uid, "id": user.id}

    def _create_order(self, *, credits: str = "600.00", amount: str = "250.00") -> uuid.UUID:
        with patch(CREDITS, return_value=Decimal(credits)), patch(DEBT, return_value=Decimal("0.00")):
            res = request_payout(PayoutRequestInput(importe=amount), self.tech, self.db)
        return uuid.UUID(res["orden"]["id"])

    # CA-04 y CA-07
    def test_inbox_masks_account_and_only_admin_can_reveal_bank_details_with_log(self):
        oid = self._create_order()

        inbox = list_admin_payouts(self.admin, self.db, "PENDIENTE")
        self.assertEqual(len(inbox["ordenes"]), 1)
        self.assertEqual(inbox["ordenes"][0]["cuenta_enmascarada"], "****3344")
        self.assertNotIn("110022003344", str(inbox))

        for non_admin in (self.tech, self.client_user):
            with self.assertRaises(HTTPException) as c1:
                list_admin_payouts(non_admin, self.db)
            self.assertEqual(c1.exception.status_code, 403)
            with self.assertRaises(HTTPException) as c2:
                get_admin_payout_bank_details(oid, non_admin, self.db)
            self.assertEqual(c2.exception.status_code, 403)
            with self.assertRaises(HTTPException) as c3:
                decide_payout(oid, PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF-1001"), non_admin, self.db)
            self.assertEqual(c3.exception.status_code, 403)

        with self.assertLogs("app.payouts_router", level="INFO") as logs:
            details = get_admin_payout_bank_details(oid, self.admin, self.db)
        self.assertEqual(details["numero_cuenta"], "110022003344")
        self.assertEqual(details["cuenta_enmascarada"], "****3344")
        joined = "\n".join(logs.output)
        self.assertIn(str(self.admin["id"]), joined)
        self.assertIn(str(oid), joined)
        self.assertNotIn("110022003344", joined)

    # CA-04, CA-05, CA-06
    def test_confirm_deducts_once_releases_reserve_audits_and_blocks_repeat_or_duplicate_ref(self):
        oid1 = self._create_order(credits="600.00", amount="250.00")
        oid2 = self._create_order(credits="600.00", amount="200.00")
        when = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)

        with patch(CREDITS, return_value=Decimal("600.00")), patch(DEBT, return_value=Decimal("0.00")):
            # Falta referencia -> 400
            with self.assertRaises(HTTPException) as c_missing:
                decide_payout(oid1, PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="  "), self.admin, self.db)
            self.assertEqual(c_missing.exception.status_code, 400)

            res = decide_payout(
                oid1,
                PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF-BAC-9001", observacion="Transferencia realizada"),
                self.admin,
                self.db,
                now=when,
            )
            summary = get_my_payout_summary(self.tech, self.db)

            # CA-06: orden pagada no puede pagarse de nuevo
            with self.assertRaises(HTTPException) as c_repeat:
                decide_payout(
                    oid1,
                    PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF-OTRA-999"),
                    self.admin,
                    self.db,
                )
            self.assertEqual(c_repeat.exception.status_code, 409)

            # Referencia de transferencia duplicada en otra orden -> 409
            with self.assertRaises(HTTPException) as c_dup:
                decide_payout(
                    oid2,
                    PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia=" trf-bac-9001 "),
                    self.admin,
                    self.db,
                )
            self.assertEqual(c_dup.exception.status_code, 409)

        self.assertEqual(res["orden"]["estado"], "PAGADA")
        self.assertEqual(res["orden"]["importe_reservado"], "0.00")
        self.assertEqual(res["orden"]["referencia_transferencia"], "TRF-BAC-9001")
        self.assertEqual(res["orden"]["revisado_por"], str(self.admin["id"]))
        self.assertEqual(res["orden"]["revisado_en"], when.isoformat())
        # Contable bajó de 600 a 350; sigue reservada oid2 (200); libre queda 150
        self.assertEqual(summary["saldo_contable"], "350.00")
        self.assertEqual(summary["saldo_reservado"], "200.00")
        self.assertEqual(summary["saldo_libre"], "150.00")

        audit = list_payout_audit(oid1, self.admin, self.db)["movimientos"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["accion"], "CONFIRMACION_PAGO")
        self.assertEqual(audit[0]["saldo_contable_anterior"], "600.00")
        self.assertEqual(audit[0]["saldo_contable_posterior"], "350.00")
        self.assertEqual(audit[0]["saldo_reservado_anterior"], "450.00")
        self.assertEqual(audit[0]["saldo_reservado_posterior"], "200.00")

    # CA-04, CA-05
    def test_rejection_requires_reason_and_releases_reserve_without_debiting_funds(self):
        oid = self._create_order(credits="500.00", amount="300.00")

        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.00")):
            with self.assertRaises(HTTPException) as c_no_reason:
                decide_payout(oid, PayoutDecisionInput(decision="RECHAZAR", motivo_rechazo="   "), self.admin, self.db)
            self.assertEqual(c_no_reason.exception.status_code, 400)

            res = decide_payout(
                oid,
                PayoutDecisionInput(decision="RECHAZAR", motivo_rechazo="Cuenta inactiva en el banco"),
                self.admin,
                self.db,
            )
            summary = get_my_payout_summary(self.tech, self.db)

        self.assertEqual(res["orden"]["estado"], "RECHAZADA")
        self.assertEqual(res["orden"]["importe_reservado"], "0.00")
        self.assertEqual(res["orden"]["motivo_rechazo"], "Cuenta inactiva en el banco")
        self.assertEqual(summary["saldo_contable"], "500.00")
        self.assertEqual(summary["saldo_reservado"], "0.00")
        self.assertEqual(summary["saldo_libre"], "500.00")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(PayoutOrderAudit)), 1)


if __name__ == "__main__":
    unittest.main()
