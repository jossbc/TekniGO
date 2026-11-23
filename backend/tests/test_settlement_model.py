"""HU-42 ST-01: modelo de liquidaciones, unicidad y reglas puras."""
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settlements as st
from app.database import Base
from app.models import CommissionSettlement, User


def _user(db, n):
    user = User(
        id=uuid.uuid4(), firebase_uid=f"uid{n}", nombre_completo=f"U{n}",
        correo=f"u{n}@x.com", dni=f"{n:013d}", telefono=f"+5049{n:07d}",
    )
    db.add(user)
    db.flush()
    return user


def _settlement(tecnico_id, ref="BAC-0001", sha="a" * 64, **kw):
    data = dict(
        tecnico_id=tecnico_id, importe_declarado=Decimal("100.50"), referencia=ref,
        referencia_normalizada=st.normalize_reference(ref), ruta_privada="liquidaciones/x.bin",
        content_type="image/png", tamano_bytes=10, comprobante_sha256=sha,
    )
    data.update(kw)
    return CommissionSettlement(**data)


class SettlementRulesTests(unittest.TestCase):
    def test_money_rounds_half_up_with_cents(self):
        self.assertEqual(st.money("10.005"), Decimal("10.01"))
        self.assertEqual(st.money("10.004"), Decimal("10.00"))
        self.assertEqual(st.money(Decimal("0.125")), Decimal("0.13"))

    def test_money_rejects_float_and_garbage(self):
        for bad in (10.5, "abc", "NaN", "Infinity", True):
            with self.assertRaises(st.SettlementValidationError):
                st.money(bad)

    def test_amount_must_be_positive(self):
        for bad in ("0", "0.004", "-5"):
            with self.assertRaises(st.SettlementValidationError):
                st.validate_amount(bad)
        self.assertEqual(st.validate_amount("0.005"), Decimal("0.01"))

    def test_reference_normalization(self):
        self.assertEqual(st.normalize_reference(" ab-12 34 "), "AB-1234")
        for bad in ("ab", "x" * 65, "AB#123", ""):
            with self.assertRaises(st.SettlementValidationError):
                st.normalize_reference(bad)

    def test_transitions_only_from_pending(self):
        self.assertTrue(st.can_transition(st.PENDIENTE, st.APROBADA))
        for final in st.FINAL_STATES:
            for target in st.SETTLEMENT_STATES:
                self.assertFalse(st.can_transition(final, target))

    def test_split_applied_never_creates_credit(self):
        self.assertEqual(st.split_applied_amount(Decimal("80.00"), Decimal("100.00")), (Decimal("80.00"), Decimal("0.00")))
        self.assertEqual(st.split_applied_amount(Decimal("130.25"), Decimal("100.00")), (Decimal("100.00"), Decimal("30.25")))
        self.assertEqual(st.split_applied_amount(Decimal("50.00"), Decimal("0.00")), (Decimal("0.00"), Decimal("50.00")))


class SettlementTableTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[User.__table__, CommissionSettlement.__table__])
        self.db = Session(self.engine)
        self.tech = _user(self.db, 1)
        self.admin = _user(self.db, 2)

    def tearDown(self):
        self.db.close()

    def _add(self, **kw):
        self.db.add(_settlement(self.tech.id, **kw))
        self.db.flush()

    def test_defaults_pending_and_zero_amounts(self):
        s = _settlement(self.tech.id)
        self.db.add(s)
        self.db.commit()
        self.assertEqual(s.estado, "PENDIENTE")
        self.assertEqual(s.importe_aplicado, Decimal("0.00"))
        self.assertIsNone(s.revisado_por)

    def test_duplicate_reference_or_proof_blocked_while_active(self):
        self._add()
        with self.assertRaises(IntegrityError):
            self._add(sha="b" * 64)  # misma referencia
        self.db.rollback()
        self.tech = _user(self.db, 3)
        self._add()
        with self.assertRaises(IntegrityError):
            self._add(ref="OTRA-9999")  # mismo comprobante
        self.db.rollback()

    def test_rejected_settlement_releases_reference_and_proof(self):
        self._add(
            estado="RECHAZADA", observacion="Ilegible", revisado_por=self.admin.id,
            revisado_en=datetime.now(timezone.utc),
        )
        self._add()  # reenvío con la misma referencia y comprobante
        self.assertEqual(self.db.query(CommissionSettlement).count(), 2)

    def test_approved_requires_verified_amount_and_reviewer(self):
        with self.assertRaises(IntegrityError):
            self._add(estado="APROBADA", revisado_por=self.admin.id, revisado_en=datetime.now(timezone.utc))
        self.db.rollback()
        with self.assertRaises(IntegrityError):
            self._add(estado="APROBADA", importe_verificado=Decimal("100.50"))
        self.db.rollback()
        self._add(
            estado="APROBADA", importe_verificado=Decimal("100.50"),
            revisado_por=self.admin.id, revisado_en=datetime.now(timezone.utc),
        )

    def test_rejection_requires_observation(self):
        with self.assertRaises(IntegrityError):
            self._add(estado="RECHAZADA", revisado_por=self.admin.id, revisado_en=datetime.now(timezone.utc))

    def test_invalid_state_and_amount_rejected(self):
        with self.assertRaises(IntegrityError):
            self._add(estado="OTRO")
        self.db.rollback()
        with self.assertRaises(IntegrityError):
            self._add(importe_declarado=Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
