"""HU-42 ST-03: almacenamiento privado cifrado y acceso exclusivo administrativo al comprobante."""
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import UploadedPart
from app.models import CommissionSettlement, User, UserRole
from app.security import get_current_user
from app.settlements_router import (
    admin_settlement_proof_endpoint,
    admin_settlements_endpoint,
    my_settlements_endpoint,
    router as settlements_router,
    submit_settlement,
)
from app.storage import PrivateDocumentStorage

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
DEBT = "app.settlements.commission_debt_generated"


class SettlementPrivateAccessTests(unittest.TestCase):
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

        self.tech = self._add_user("tech-1", "TECNICO", 1)
        self.other_tech = self._add_user("tech-2", "TECNICO", 2)
        self.client_user = self._add_user("cli-1", "CLIENTE", 3)
        self.admin = self._add_user("adm-1", "ADMIN", 4)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

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

    def _create_settlement(self, ref: str = "BAC-9001") -> dict:
        with patch(DEBT, return_value=Decimal("300.00")):
            return submit_settlement(
                importe="120.50",
                referencia=ref,
                comprobante=UploadedPart(PNG, "image/png"),
                claims=self.tech,
                db=self.db,
                storage=self.storage,
            )

    def test_all_settlement_routes_require_bearer_auth_and_reject_missing_token(self):
        expected_paths = {
            "/users/me/settlements",
            "/admin/settlements",
            "/admin/settlements/{settlement_id}/proof",
            "/admin/settlements/{settlement_id}/decision",
            "/admin/settlements/{settlement_id}/audit",
        }
        seen = set()
        for route in settlements_router.routes:
            seen.add(route.path)
            deps = [dep.call for dep in route.dependant.dependencies]
            self.assertIn(get_current_user, deps, f"Falta get_current_user en {route.path}")
        self.assertEqual(seen, expected_paths)

        with self.assertRaises(HTTPException) as caught:
            get_current_user(credentials=None)
        self.assertEqual(caught.exception.status_code, 401)

    def test_only_admin_can_download_proof_and_headers_and_audit_log_are_strict(self):
        created = self._create_settlement()
        sid = uuid.UUID(created["id"])

        for actor in (self.tech, self.other_tech, self.client_user):
            with self.assertRaises(HTTPException) as caught:
                admin_settlement_proof_endpoint(sid, claims=actor, db=self.db)
            self.assertEqual(caught.exception.status_code, 403)

        with self.assertLogs("app.settlements_router", level="INFO") as logs:
            resp = admin_settlement_proof_endpoint(sid, claims=self.admin, db=self.db)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, PNG)
        self.assertEqual(resp.media_type, "image/png")
        self.assertEqual(resp.headers["cache-control"], "no-store, private")
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resp.headers["content-disposition"], 'inline; filename="comprobante"')
        self.assertIn("sandbox", resp.headers["content-security-policy"])

        joined_logs = "\n".join(logs.output)
        self.assertIn(str(self.admin["id"]), joined_logs)
        self.assertIn(created["id"], joined_logs)
        self.assertNotIn("private://", joined_logs)

    def test_proof_stored_encrypted_under_liquidaciones_and_paths_never_exposed(self):
        created = self._create_settlement()
        row = self.db.get(CommissionSettlement, uuid.UUID(created["id"]))
        self.assertIsNotNone(row)
        self.assertTrue(
            row.ruta_privada.startswith(
                f"private://documentos-identidad/liquidaciones/{self.tech['id']}/comprobante_liquidacion_"
            )
        )

        stored_files = list(Path(self.tmp.name).rglob("*.enc"))
        self.assertEqual(len(stored_files), 1)
        self.assertIn("liquidaciones", stored_files[0].parts)
        self.assertNotIn("identidad", stored_files[0].parts)
        self.assertNotIn(PNG, stored_files[0].read_bytes())

        owner_res = Response()
        with patch(DEBT, return_value=Decimal("300.00")):
            owner_body = my_settlements_endpoint(owner_res, claims=self.tech, db=self.db)
        self.assertEqual(owner_res.headers["cache-control"], "no-store, private")
        for payload in (str(created), str(owner_body)):
            self.assertNotIn("private://", payload)
            self.assertNotIn("ruta_privada", payload)
            self.assertNotIn(".enc", payload)

        admin_res = Response()
        admin_body = admin_settlements_endpoint(admin_res, estado=None, claims=self.admin, db=self.db)
        self.assertEqual(admin_res.headers["cache-control"], "no-store, private")
        self.assertNotIn("private://", str(admin_body))
        self.assertNotIn("ruta_privada", str(admin_body))

    def test_tampered_encrypted_proof_returns_502_without_leaking_bytes(self):
        created = self._create_settlement()
        stored_file = next(Path(self.tmp.name).rglob("*.enc"))
        raw = bytearray(stored_file.read_bytes())
        raw[-1] ^= 0xFF
        stored_file.write_bytes(bytes(raw))

        with self.assertRaises(HTTPException) as caught:
            admin_settlement_proof_endpoint(uuid.UUID(created["id"]), claims=self.admin, db=self.db)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("integridad", caught.exception.detail)

    def test_malicious_or_cross_folder_paths_are_rejected(self):
        uid = uuid.uuid4()
        hx = uuid.uuid4().hex
        bad_paths = (
            f"private://documentos-identidad/identidad/{uid}/comprobante_liquidacion_{hx}.png.enc",
            f"private://documentos-identidad/liquidaciones/{uid}/selfie_{hx}.png.enc",
            f"private://documentos-identidad/liquidaciones/../identidad/{uid}/selfie_{hx}.png.enc",
            f"private://otro-bucket/liquidaciones/{uid}/comprobante_liquidacion_{hx}.png.enc",
        )
        for bad in bad_paths:
            with self.assertRaises(ValueError, msg=bad):
                self.storage.read_private(bad)
            with self.assertRaises(ValueError, msg=bad):
                self.storage.delete_private(bad)

    def test_orphan_encrypted_file_is_deleted_if_commit_fails(self):
        with patch(DEBT, return_value=Decimal("300.00")), patch.object(
            self.db, "commit", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(HTTPException) as caught:
                submit_settlement(
                    importe="50.00",
                    referencia="BAC-RACE-1",
                    comprobante=UploadedPart(PNG, "image/png"),
                    claims=self.tech,
                    db=self.db,
                    storage=self.storage,
                )
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(list(Path(self.tmp.name).rglob("*.enc")), [])


if __name__ == "__main__":
    unittest.main()
