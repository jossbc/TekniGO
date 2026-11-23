"""HU-42 ST-02: carga del técnico y bandeja administrativa de liquidaciones."""
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import UploadedPart
from app.models import CommissionSettlement, TechnicianProfile, User, UserRole
from app.settlements_router import (
    get_proof_for_admin,
    get_settlement_summary,
    list_admin_settlements,
    submit_settlement,
)
from app.storage import MAX_DOCUMENT_BYTES, PrivateDocumentStorage

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
PDF = b"%PDF-1.4 fake"
DEBT = "app.settlements.commission_debt_generated"


class SettlementSubmissionTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes"})
        env.start()
        self.addCleanup(env.stop)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = PrivateDocumentStorage(base_dir=Path(self.tmp.name))
        self.tech = self._user("tech-1", "TECNICO", 1)
        self.other_tech = self._user("tech-2", "TECNICO", 2)
        self.client = self._user("cli-1", "CLIENTE", 3)
        self.admin = self._user("adm-1", "ADMIN", 4)
        self.db.add(TechnicianProfile(usuario_id=self.tech["id"], suspendido=True, restriccion_financiera=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _user(self, uid, role, n):
        user = User(firebase_uid=uid, correo=f"{uid}@x.com", nombre_completo=uid, dni=f"08012000000{n:02d}", telefono=f"+5049{n:07d}")
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        return {"uid": uid, "id": user.id}

    def _submit(self, importe="100.00", ref="BAC-1001", content=PNG, ctype="image/png", claims=None):
        return submit_settlement(
            importe=importe, referencia=ref, comprobante=UploadedPart(content, ctype),
            claims=claims or self.tech, db=self.db, storage=self.storage,
        )

    def _count(self):
        return self.db.scalar(select(func.count()).select_from(CommissionSettlement))

    # CA-01 / CA-02
    def test_technician_with_debt_submits_pending_without_changing_debt_or_block(self):
        with patch(DEBT, return_value=Decimal("250.00")):
            before = get_settlement_summary(self.tech, self.db)["deuda_vigente"]
            result = self._submit("100.005", "bac-1001")
            after = get_settlement_summary(self.tech, self.db)
        self.assertEqual(result["estado"], "PENDIENTE")
        self.assertEqual(result["importe_declarado"], "100.01")
        self.assertEqual((before, after["deuda_vigente"]), ("250.00", "250.00"))
        self.assertEqual(after["importe_en_revision"], "100.01")
        profile = self.db.get(TechnicianProfile, self.tech["id"])
        self.assertTrue(profile.restriccion_financiera)
        self.assertTrue(profile.suspendido)

    def test_requires_debt_and_technician_role(self):
        with patch(DEBT, return_value=Decimal("0.00")):
            with self.assertRaises(HTTPException) as c:
                self._submit()
            self.assertEqual(c.exception.status_code, 409)
        with patch(DEBT, return_value=Decimal("50.00")):
            with self.assertRaises(HTTPException) as c:
                self._submit(claims=self.client)
            self.assertEqual(c.exception.status_code, 403)
        self.assertEqual(self._count(), 0)

    def test_validates_amount_reference_and_real_file_content(self):
        with patch(DEBT, return_value=Decimal("250.00")):
            for kwargs in (
                {"importe": "0"}, {"importe": "abc"}, {"ref": "x"},
                {"content": b"MZ not an image", "ctype": "image/png"},
                {"content": JPEG, "ctype": "image/png"},  # declarado distinto al real
                {"content": PDF, "ctype": "application/pdf"},  # solo foto o captura
                {"content": b"", "ctype": "image/png"},
                {"content": PNG + b"0" * MAX_DOCUMENT_BYTES, "ctype": "image/png"},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as c:
                    self._submit(**kwargs)
                self.assertEqual(c.exception.status_code, 400)
        self.assertEqual(self._count(), 0)

    # CA-06
    def test_duplicate_reference_or_proof_creates_no_second_settlement(self):
        with patch(DEBT, return_value=Decimal("500.00")):
            self._submit()
            with self.assertRaises(HTTPException) as c:  # misma referencia (otra capitalización) y otro archivo
                self._submit(ref=" bac-1001 ", content=JPEG, ctype="image/jpeg")
            self.assertEqual(c.exception.status_code, 409)
            with self.assertRaises(HTTPException) as c:  # mismo archivo, otra referencia
                self._submit(ref="BAC-2002")
            self.assertEqual(c.exception.status_code, 409)
        self.assertEqual(self._count(), 1)

    def test_rejected_reference_can_be_resubmitted(self):
        with patch(DEBT, return_value=Decimal("500.00")):
            first = self._submit()
            row = self.db.get(CommissionSettlement, __import__("uuid").UUID(first["id"]))
            row.estado = "RECHAZADA"
            row.observacion = "Ilegible"
            row.revisado_por = self.admin["id"]
            row.revisado_en = datetime.now(timezone.utc)
            self.db.commit()
            self._submit()
        self.assertEqual(self._count(), 2)

    # Privacidad
    def test_owner_view_never_exposes_file_path_and_shows_formats_before_upload(self):
        with patch(DEBT, return_value=Decimal("250.00")):
            self._submit()
            summary = get_settlement_summary(self.tech, self.db)
            other = get_settlement_summary(self.other_tech, self.db)
        self.assertEqual(summary["tamano_maximo_bytes"], MAX_DOCUMENT_BYTES)
        self.assertEqual(summary["formatos_permitidos"], ["image/jpeg", "image/png", "image/webp"])
        self.assertNotIn("private://", str(summary))
        self.assertNotIn("ruta_privada", str(summary))
        self.assertEqual(other["liquidaciones"], [])

    def test_proof_is_stored_encrypted_and_only_admin_can_read_it(self):
        with patch(DEBT, return_value=Decimal("250.00")):
            created = self._submit()
        stored = [p for p in Path(self.tmp.name).rglob("*.enc")]
        self.assertEqual(len(stored), 1)
        self.assertNotIn(PNG, stored[0].read_bytes())
        sid = __import__("uuid").UUID(created["id"])
        content, media = get_proof_for_admin(sid, self.admin, self.db, storage=self.storage)
        self.assertEqual((content, media), (PNG, "image/png"))
        for who in (self.tech, self.client):
            with self.assertRaises(HTTPException) as c:
                get_proof_for_admin(sid, who, self.db, storage=self.storage)
            self.assertEqual(c.exception.status_code, 403)

    def test_admin_inbox_filters_and_is_admin_only(self):
        with patch(DEBT, return_value=Decimal("250.00")):
            self._submit()
        inbox = list_admin_settlements(self.admin, self.db, "pendiente")
        self.assertEqual(len(inbox["liquidaciones"]), 1)
        self.assertEqual(inbox["liquidaciones"][0]["tecnico_nombre"], "tech-1")
        self.assertEqual(list_admin_settlements(self.admin, self.db, "RECHAZADA")["liquidaciones"], [])
        with self.assertRaises(HTTPException) as c:
            list_admin_settlements(self.admin, self.db, "NOPE")
        self.assertEqual(c.exception.status_code, 400)
        with self.assertRaises(HTTPException) as c:
            list_admin_settlements(self.tech, self.db)
        self.assertEqual(c.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
