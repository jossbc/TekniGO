"""HU-43 ST-02: cuenta bancaria privada, validación de deuda/mínimo/saldo y reserva transaccional."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import OperationalSettings, PayoutOrder, TechnicianBankAccount, User, UserRole
from app.payouts_router import (
    BankAccountInput,
    PayoutRequestInput,
    get_my_payout_summary,
    request_payout,
    router as payouts_router,
    save_my_bank_account,
)
from app.security import get_current_user

CREDITS = "app.payouts.digital_credits_generated"
DEBT = "app.settlements.commission_debt_generated"


class PayoutRequestsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes"})
        self.env.start()
        self.addCleanup(self.env.stop)

        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.tech = self._add_user("tech-1", "TECNICO", 1)
        self.client_user = self._add_user("cli-1", "CLIENTE", 2)
        self.db.add(
            OperationalSettings(
                id=1,
                comision_porcentaje=Decimal("10.00"),
                limite_deuda=Decimal("500.00"),
                minimo_retiro=Decimal("200.00"),
            )
        )
        self.db.commit()

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

    def _register_account(self, number: str = "7412-8890-1234", claims=None):
        return save_my_bank_account(
            BankAccountInput(
                banco="BAC Credomatic",
                tipo_cuenta="AHORRO",
                titular_nombre="Juan Pérez",
                numero_cuenta=number,
            ),
            claims or self.tech,
            self.db,
        )

    def _order_count(self) -> int:
        return self.db.scalar(select(func.count()).select_from(PayoutOrder))

    # CA-07
    def test_bank_account_saved_encrypted_and_only_masked_data_returned(self):
        saved = self._register_account("7412-8890-1234")
        self.assertEqual(saved["cuenta_bancaria"]["cuenta_enmascarada"], "****1234")
        self.assertNotIn("741288901234", str(saved))
        self.assertNotIn("numero_cuenta_cifrado", str(saved))

        # Reemplazar cuenta desactiva la anterior y deja una sola activa
        replaced = self._register_account("9988-7766-5544")
        self.assertEqual(replaced["cuenta_bancaria"]["cuenta_enmascarada"], "****5544")
        accounts = self.db.scalars(select(TechnicianBankAccount).where(TechnicianBankAccount.tecnico_id == self.tech["id"])).all()
        self.assertEqual(len(accounts), 2)
        self.assertEqual(sum(1 for a in accounts if a.activa), 1)
        for acc in accounts:
            self.assertNotIn("741288901234", acc.numero_cuenta_cifrado)
            self.assertNotIn("998877665544", acc.numero_cuenta_cifrado)

        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.00")):
            summary = get_my_payout_summary(self.tech, self.db)
        self.assertEqual(summary["cuenta_bancaria"]["cuenta_enmascarada"], "****5544")
        self.assertNotIn("998877665544", str(summary))
        self.assertNotIn("numero_cuenta_cifrado", str(summary))

    # CA-01
    def test_requires_technician_role_bank_account_and_zero_debt(self):
        with self.assertRaises(HTTPException) as c_role:
            self._register_account(claims=self.client_user)
        self.assertEqual(c_role.exception.status_code, 403)

        # Sin cuenta bancaria registrada
        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.00")):
            with self.assertRaises(HTTPException) as c_acc:
                request_payout(PayoutRequestInput(importe="250.00"), self.tech, self.db)
            self.assertEqual(c_acc.exception.status_code, 400)

        self._register_account()

        # Con deuda pendiente (> 0)
        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.01")):
            with self.assertRaises(HTTPException) as c_debt:
                request_payout(PayoutRequestInput(importe="250.00"), self.tech, self.db)
            self.assertEqual(c_debt.exception.status_code, 409)

        self.assertEqual(self._order_count(), 0)

    # CA-02 y CA-03
    def test_minimum_200_rejects_199_99_admits_200_00_and_reserves_without_debiting_accounting_balance(self):
        self._register_account()

        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.00")):
            # L 199.99 rechazado
            with self.assertRaises(HTTPException) as c_low:
                request_payout(PayoutRequestInput(importe="199.99"), self.tech, self.db)
            self.assertEqual(c_low.exception.status_code, 400)
            self.assertEqual(self._order_count(), 0)

            # L 200.00 admitido
            res = request_payout(PayoutRequestInput(importe="200.00"), self.tech, self.db)
            summary = get_my_payout_summary(self.tech, self.db)

        self.assertEqual(res["orden"]["estado"], "PENDIENTE")
        self.assertEqual(res["orden"]["importe"], "200.00")
        self.assertEqual(res["orden"]["importe_reservado"], "200.00")
        self.assertEqual(res["orden"]["minimo_aplicado"], "200.00")
        self.assertEqual(res["orden"]["cuenta_enmascarada"], "****1234")
        # CA-03: saldo contable no se debita todavía; solo se reserva
        self.assertEqual(res["saldos"]["saldo_contable"], "500.00")
        self.assertEqual(res["saldos"]["saldo_reservado"], "200.00")
        self.assertEqual(res["saldos"]["saldo_libre"], "300.00")
        self.assertEqual(summary["saldo_contable"], "500.00")
        self.assertEqual(summary["saldo_reservado"], "200.00")
        self.assertEqual(summary["saldo_libre"], "300.00")

    # CA-06
    def test_multiple_requests_cannot_exceed_available_balance(self):
        self._register_account()

        with patch(CREDITS, return_value=Decimal("500.00")), patch(DEBT, return_value=Decimal("0.00")):
            first = request_payout(PayoutRequestInput(importe="300.00"), self.tech, self.db)
            self.assertEqual(first["saldos"]["saldo_libre"], "200.00")

            # Supera el saldo libre restante (200.00) aunque el saldo contable siga en 500.00
            with self.assertRaises(HTTPException) as c_over:
                request_payout(PayoutRequestInput(importe="200.01"), self.tech, self.db)
            self.assertEqual(c_over.exception.status_code, 400)

            # Exactamente el saldo libre restante (200.00) sí se admite
            second = request_payout(PayoutRequestInput(importe="200.00"), self.tech, self.db)
            self.assertEqual(second["saldos"]["saldo_contable"], "500.00")
            self.assertEqual(second["saldos"]["saldo_reservado"], "500.00")
            self.assertEqual(second["saldos"]["saldo_libre"], "0.00")

            # Ya sin saldo libre, cualquier nuevo retiro falla
            with self.assertRaises(HTTPException) as c_zero:
                request_payout(PayoutRequestInput(importe="200.00"), self.tech, self.db)
            self.assertEqual(c_zero.exception.status_code, 400)

        self.assertEqual(self._order_count(), 2)

    def test_all_payout_routes_require_authentication(self):
        expected = {
            "/users/me/payouts",
            "/users/me/bank-account",
            "/admin/payouts",
            "/admin/payouts/{order_id}/bank-details",
            "/admin/payouts/{order_id}/decision",
            "/admin/payouts/{order_id}/audit",
        }
        seen = set()
        for route in payouts_router.routes:
            seen.add(route.path)
            deps = [dep.call for dep in route.dependant.dependencies]
            self.assertIn(get_current_user, deps)
        self.assertEqual(seen, expected)


if __name__ == "__main__":
    unittest.main()
