import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import (
    UploadedPart,
    get_document_content_for_admin,
    get_my_identity_documents,
    submit_identity_documents,
)
from app.models import IdentityDocument, User, UserRole
from app.storage import MAX_DOCUMENT_BYTES, PrivateDocumentStorage

VALID_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
VALID_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
VALID_WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00"


class IdentityDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = PrivateDocumentStorage(base_dir=Path(self.temp_dir.name))

        self.client_claims = self._add_user("client-1", "cliente@example.com", "0801200000001", "+50491111111", "CLIENTE")
        self.other_claims = self._add_user("client-2", "otro@example.com", "0801200000002", "+50492222222", "CLIENTE")
        self.tech_claims = self._add_user("tech-1", "tecnico@example.com", "0801200000003", "+50493333333", "TECNICO")
        self.admin_claims = self._add_user("admin-1", "admin@example.com", "0801200000004", "+50494444444", "ADMIN")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _add_user(self, uid: str, email: str, dni: str, phone: str, role: str) -> dict:
        user = User(firebase_uid=uid, correo=email, nombre_completo="Persona Test", dni=dni, telefono=phone)
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        return {"uid": uid}

    def _submit(self, frontal=None, reverso=None, selfie=None, claims=None):
        return submit_identity_documents(
            dni_frontal=frontal or UploadedPart(VALID_JPEG, "image/jpeg"),
            dni_reverso=reverso or UploadedPart(VALID_PNG, "image/png"),
            selfie=selfie or UploadedPart(VALID_WEBP, "image/webp"),
            claims=claims or self.client_claims,
            db=self.db,
            storage=self.storage,
        )

    def _status_of(self, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        return caught.exception.status_code

    # ---- CA-01, CA-02, CA-05: carga de DNI ambos lados + selfie y paso a EN_REVISION ----

    def test_client_uploads_dni_both_sides_and_selfie_and_moves_to_review(self):
        result = self._submit()
        self.assertEqual(result["estado_identidad"], "EN_REVISION")
        self.assertEqual(len(result["documentos"]), 3)
        tipos = [d["tipo"] for d in result["documentos"]]
        self.assertEqual(tipos, ["DNI_FRONTAL", "DNI_REVERSO", "SELFIE"])
        self.assertTrue(all(d["estado"] == "PENDIENTE" for d in result["documentos"]))

        user = self.db.scalar(select(User).where(User.firebase_uid == "client-1"))
        self.assertEqual(user.estado_identidad, "EN_REVISION")

    # ---- CA-03: validacion de formatos reales y tamanos ----

    def test_rejects_empty_file(self):
        status = self._status_of(lambda: self._submit(frontal=UploadedPart(b"", "image/jpeg")))
        self.assertEqual(status, 400)

    def test_rejects_oversized_file(self):
        too_big = VALID_JPEG + (b"x" * (MAX_DOCUMENT_BYTES + 1))
        status = self._status_of(lambda: self._submit(selfie=UploadedPart(too_big, "image/jpeg")))
        self.assertEqual(status, 400)

    def test_rejects_invalid_magic_bytes_disguised_as_image(self):
        fake_pdf = b"%PDF-1.7 documento falso disfrazado de jpg"
        status = self._status_of(lambda: self._submit(reverso=UploadedPart(fake_pdf, "image/jpeg")))
        self.assertEqual(status, 400)

    def test_rejects_mismatched_declared_mime_and_real_content(self):
        status = self._status_of(lambda: self._submit(frontal=UploadedPart(VALID_PNG, "image/jpeg")))
        self.assertEqual(status, 400)

    # ---- CA-04 y Condicion transversal: almacenamiento cifrado/privado y permisos ----

    def test_files_are_encrypted_at_rest_and_private_paths_are_not_exposed_to_owner(self):
        result = self._submit()
        for item in result["documentos"]:
            self.assertNotIn("ruta_privada", item)
            self.assertEqual(set(item.keys()), {"id", "tipo", "estado", "observaciones", "creado_en"})

        own_view = get_my_identity_documents(self.client_claims, self.db)
        self.assertEqual(own_view["estado_identidad"], "EN_REVISION")
        self.assertTrue(all("ruta_privada" not in d for d in own_view["documentos"]))

        stored_files = [p for p in Path(self.temp_dir.name).rglob("*") if p.is_file()]
        self.assertEqual(len(stored_files), 3)
        for path in stored_files:
            raw_on_disk = path.read_bytes()
            self.assertTrue(raw_on_disk.startswith(b"TKGO1"))
            self.assertNotIn(b"JFIF", raw_on_disk)
            self.assertNotIn(b"IHDR", raw_on_disk)

    def test_only_admin_can_access_sensitive_document_content(self):
        result = self._submit()
        doc_id = uuid.UUID(result["documentos"][0]["id"])

        for unauthorized in (self.client_claims, self.other_claims, self.tech_claims):
            status = self._status_of(
                lambda claims=unauthorized: get_document_content_for_admin(doc_id, claims, self.db, self.storage)
            )
            self.assertEqual(status, 403)

        content, media_type = get_document_content_for_admin(doc_id, self.admin_claims, self.db, self.storage)
        self.assertEqual(content, VALID_JPEG)
        self.assertEqual(media_type, "image/jpeg")

    # ---- Estados de la solicitud (CA-05) ----

    def test_duplicate_submission_while_in_review_is_rejected(self):
        self._submit()
        status = self._status_of(lambda: self._submit())
        self.assertEqual(status, 409)

    def test_verified_user_cannot_submit_again(self):
        self._submit()
        user = self.db.scalar(select(User).where(User.firebase_uid == "client-1"))
        user.estado_identidad = "VERIFICADO"
        self.db.commit()
        status = self._status_of(lambda: self._submit())
        self.assertEqual(status, 409)

    def test_rejected_user_can_resubmit_and_returns_to_in_review(self):
        self._submit()
        user = self.db.scalar(select(User).where(User.firebase_uid == "client-1"))
        user.estado_identidad = "RECHAZADO"
        self.db.commit()

        resubmitted = self._submit()
        self.assertEqual(resubmitted["estado_identidad"], "EN_REVISION")
        docs_in_db = self.db.scalars(select(IdentityDocument).where(IdentityDocument.usuario_id == user.id)).all()
        self.assertEqual(len(docs_in_db), 3)


if __name__ == "__main__":
    unittest.main()

