"""HU-43 ST-01: cuenta bancaria privada, órdenes de desembolso, reservas y reglas puras."""
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import payouts as po
from app.database import Base
from app.models import OperationalSettings, PayoutOrder, TechnicianBankAccount, User


def _user(db: Session, n: int) -> User:
    user = User(
        id=uuid.uuid4(),
        firebase_uid=f"uid-{n}",
        nombre_completo=f"Técnico {n}",
        correo=f"t{n}@example.com",
        dni=f"08012000000{n:02d}",
        telefono=f"+5049{n:07d}",
    )
    db.add(user)
    db.flush()
    return user


class PayoutRulesTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes"})
        self.env.start()
        self.addCleanup(self.env.stop)

    # CA-07
    def test_account_number_is_encrypted_and_masked_properly(self):
        clean = po.normalize_account_number("7412-8890-1234")
        self.assertEqual(clean, "741288901234")
        last4, masked = po.mask_account_number(clean)
        self.assertEqual((last4, masked), ("1234", "****1234"))

        cipher = po.encrypt_account_number(clean)
        self.assertNotIn("741288901234", cipher)
        self.assertNotIn("1234", cipher)
        self.assertEqual(po.decrypt_account_number(cipher), clean)

    def test_invalid_bank_account_fields_are_rejected(self):
        for bad in ("1234567", "1" * 25, "ABCD12345678", ""):
            with self.assertRaises(po.PayoutValidationError):
                po.normalize_account_number(bad)
        with self.assertRaises(po.PayoutValidationError):
            po.validate_account_type("CORRIENTE")
        with self.assertRaises(po.PayoutValidationError):
            po.validate_bank_name("A")
        with self.assertRaises(po.PayoutValidationError):
            po.validate_holder_name("Jo")

    # CA-01 / CA-02
    def test_minimum_withdrawal_threshold_with_cents(self):
        with self.assertRaises(po.PayoutValidationError):
            po.validate_payout_amount("199.99", minimum_withdrawal=Decimal("200.00"), available_balance=Decimal("500.00"))
        self.assertEqual(
            po.validate_payout_amount("200.00", minimum_withdrawal=Decimal("200.00"), available_balance=Decimal("500.00")),
            Decimal("200.00"),
        )
        # Redondeo ROUND_HALF_UP con centavos
        self.assertEqual(
            po.validate_payout_amount("200.005", minimum_withdrawal=Decimal("200.00"), available_balance=Decimal("500.00")),
            Decimal("200.01"),
        )
        # No puede superar saldo libre ni ser float
        with self.assertRaises(po.PayoutValidationError):
            po.validate_payout_amount("500.01", minimum_withdrawal=Decimal("200.00"), available_balance=Decimal("500.00"))
        with self.assertRaises(po.PayoutValidationError):
            po.validate_payout_amount(200.0, minimum_withdrawal=Decimal("200.00"), available_balance=Decimal("500.00"))

    # CA-06
    def test_payout_transitions_only_from_pending(self):
        self.assertTrue(po.can_transition_payout(po.PENDIENTE, po.PAGADA))
        self.assertTrue(po.can_transition_payout(po.PENDIENTE, po.RECHAZADA))
        for final in po.FINAL_PAYOUT_STATES:
            for target in po.PAYOUT_STATES:
                self.assertFalse(po.can_transition_payout(final, target))


class PayoutTableTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tech = _user(self.db, 1)
        self.admin = _user(self.db, 2)
        self.account = self._add_account(self.tech.id)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_account(self, tech_id, *, activa=True, number="741288901234"):
        last4, masked = po.mask_account_number(number)
        acc = TechnicianBankAccount(
            tecnico_id=tech_id,
            banco="BAC Credomatic",
            tipo_cuenta="AHORRO",
            titular_nombre="Técnico Uno",
            numero_cuenta_cifrado=po.encrypt_account_number(number),
            ultimos_cuatro=last4,
            cuenta_enmascarada=masked,
            numero_cuenta_hash=po.hash_account_number(number),
            activa=activa,
        )
        self.db.add(acc)
        self.db.flush()
        return acc

    def _order(self, **kw):
        data = dict(
            tecnico_id=self.tech.id,
            cuenta_bancaria_id=self.account.id,
            importe=Decimal("250.00"),
            importe_reservado=Decimal("250.00"),
            minimo_aplicado=Decimal("200.00"),
            estado=po.PENDIENTE,
            banco_snapshot="BAC Credomatic",
            tipo_cuenta_snapshot="AHORRO",
            cuenta_enmascarada_snapshot="****1234",
        )
        data.update(kw)
        return PayoutOrder(**data)

    def test_only_one_active_bank_account_per_technician(self):
        with self.assertRaises(IntegrityError):
            self._add_account(self.tech.id, activa=True, number="999988887777")
        self.db.rollback()
        # Una inactiva histórica no choca con la activa
        self._add_account(self.tech.id, activa=False, number="999988887777")
        self.db.commit()

    def test_pending_order_requires_exact_reserve_and_final_states_release_reserve(self):
        # PENDIENTE con reserva distinta del importe falla
        with self.assertRaises(IntegrityError):
            self.db.add(self._order(importe_reservado=Decimal("0.00")))
            self.db.flush()
        self.db.rollback()

        # PAGADA debe liberar reserva a 0.00 y tener referencia + responsable
        with self.assertRaises(IntegrityError):
            self.db.add(
                self._order(
                    estado=po.PAGADA,
                    importe_reservado=Decimal("250.00"),
                    referencia_transferencia="TRF-1001",
                    referencia_normalizada="TRF-1001",
                    revisado_por=self.admin.id,
                    revisado_en=datetime.now(timezone.utc),
                )
            )
            self.db.flush()
        self.db.rollback()

        paid = self._order(
            estado=po.PAGADA,
            importe_reservado=Decimal("0.00"),
            referencia_transferencia="TRF-1001",
            referencia_normalizada="TRF-1001",
            revisado_por=self.admin.id,
            revisado_en=datetime.now(timezone.utc),
        )
        self.db.add(paid)
        self.db.commit()
        self.assertEqual(paid.importe_reservado, Decimal("0.00"))

    def test_paid_transfer_reference_is_unique_and_rejection_requires_reason(self):
        now = datetime.now(timezone.utc)
        self.db.add(
            self._order(
                estado=po.PAGADA,
                importe_reservado=Decimal("0.00"),
                referencia_transferencia="TRF-UNICA",
                referencia_normalizada="TRF-UNICA",
                revisado_por=self.admin.id,
                revisado_en=now,
            )
        )
        self.db.flush()
        with self.assertRaises(IntegrityError):
            self.db.add(
                self._order(
                    estado=po.PAGADA,
                    importe_reservado=Decimal("0.00"),
                    referencia_transferencia="TRF-UNICA",
                    referencia_normalizada="TRF-UNICA",
                    revisado_por=self.admin.id,
                    revisado_en=now,
                )
            )
            self.db.flush()
        self.db.rollback()

        with self.assertRaises(IntegrityError):
            self.db.add(
                self._order(
                    estado=po.RECHAZADA,
                    importe_reservado=Decimal("0.00"),
                    motivo_rechazo="   ",
                    revisado_por=self.admin.id,
                    revisado_en=now,
                )
            )
            self.db.flush()
        self.db.rollback()

    def test_minimum_withdrawal_reads_operational_settings_or_defaults_to_200(self):
        self.assertEqual(po.get_minimum_withdrawal(self.db), Decimal("200.00"))
        self.db.add(OperationalSettings(id=1, comision_porcentaje=Decimal("10.00"), limite_deuda=Decimal("500.00"), minimo_retiro=Decimal("350.50")))
        self.db.commit()
        self.assertEqual(po.get_minimum_withdrawal(self.db), Decimal("350.50"))


if __name__ == "__main__":
    unittest.main()
