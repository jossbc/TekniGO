import tempfile
import unittest
import uuid
from unittest.mock import patch, Mock
from types import SimpleNamespace
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import (
    UploadedPart,
    get_document_content_for_admin,
    get_my_identity_documents,
    get_my_identity_history,
    submit_identity_documents,
)
from app.models import IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, User, UserRole
from app.storage import MAX_DOCUMENT_BYTES, PrivateDocumentStorage
from app.identity_processing import IdentityChecks, process_current_identity
from app.permissions import require_enabled_client

VALID_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
VALID_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
VALID_WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00"


class IdentityDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.storage_env = patch.dict('os.environ', {'PRIVATE_STORAGE_SECRET': 'test-only-private-storage-secret-32-bytes', 'IDENTITY_PROCESSOR': ''})
        self.storage_env.start()
        self.addCleanup(self.storage_env.stop)
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
            self.assertTrue(raw_on_disk.startswith(b"TKGO2"))
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
        self.assertEqual(len(docs_in_db), 6)
        self.assertEqual(resubmitted['version'], 2)
        self.assertEqual(len(get_my_identity_documents(self.client_claims, self.db)['documentos']), 3)

    def test_resubmission_preserves_old_files_results_and_observations(self):
        first = self._submit()
        first_id = uuid.UUID(first['solicitud_id'])
        first_doc = self.db.get(IdentityDocument, uuid.UUID(first['documentos'][0]['id']))
        first_doc.estado = 'RECHAZADO'
        first_doc.observaciones = 'DNI ilegible'
        original_path, original_hash = first_doc.ruta_privada, first_doc.sha256
        self.db.add(IdentityVerificationResult(
            solicitud_id=first_id, estado='RECHAZADO', motivo='DNI ilegible',
            proceso_responsable='PROCESO_DE_PRUEBA', version_proceso='test-1',
        ))
        user = self.db.scalar(select(User).where(User.firebase_uid == 'client-1'))
        user.estado_identidad = 'RECHAZADO'
        self.db.commit()
        second = self._submit()
        self.assertNotEqual(first['solicitud_id'], second['solicitud_id'])
        self.db.refresh(first_doc)
        self.assertEqual((first_doc.ruta_privada, first_doc.sha256), (original_path, original_hash))
        self.assertEqual((first_doc.estado, first_doc.observaciones), ('RECHAZADO', 'DNI ilegible'))
        content, _ = get_document_content_for_admin(first_doc.id, self.admin_claims, self.db, self.storage)
        self.assertEqual(content, VALID_JPEG)
        history = get_my_identity_history(self.client_claims, self.db)['solicitudes']
        self.assertEqual([item['version'] for item in history], [2, 1])
        self.assertEqual([len(item['documentos']) for item in history], [3, 3])
        self.assertTrue(any(result['motivo'] == 'DNI ilegible' and result['version_proceso'] == 'test-1'
                            for result in history[1]['resultados']))
        for item in history:
            self.assertTrue(item['creado_en'])
            for doc in item['documentos']:
                self.assertNotIn('ruta_privada', doc)
                self.assertNotIn('sha256', doc)

    def test_history_contains_only_current_owners_submissions(self):
        first = self._submit()
        other = submit_identity_documents(
            dni_frontal=UploadedPart(VALID_JPEG, 'image/jpeg'),
            dni_reverso=UploadedPart(VALID_PNG, 'image/png'),
            selfie=UploadedPart(VALID_WEBP, 'image/webp'),
            claims=self.other_claims, db=self.db, storage=self.storage,
        )
        own = get_my_identity_history(self.client_claims, self.db)['solicitudes']
        others = get_my_identity_history(self.other_claims, self.db)['solicitudes']
        self.assertEqual([item['id'] for item in own], [first['solicitud_id']])
        self.assertEqual([item['id'] for item in others], [other['solicitud_id']])
        self.assertEqual(get_my_identity_history(self.tech_claims, self.db), {'solicitudes': []})

    def test_pending_duplicate_does_not_create_another_version(self):
        self._submit()
        with self.assertRaises(HTTPException) as error:
            self._submit()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationRequest)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationResult)), 2)

    def test_invalid_later_file_does_not_store_partial_submission(self):
        with self.assertRaises(HTTPException):
            self._submit(selfie=UploadedPart(b'invalid', 'image/jpeg'))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationRequest)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityDocument)), 0)
        self.assertFalse(any(Path(self.temp_dir.name).rglob('*.enc')))

    def test_database_rejects_repeated_version_for_same_user(self):
        from sqlalchemy.exc import IntegrityError
        self._submit()
        user = self.db.scalar(select(User).where(User.firebase_uid == 'client-1'))
        self.db.add(IdentityVerificationRequest(usuario_id=user.id, version=1))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_rejected_submission_then_corrected_resend_preserves_history_and_requires_email(self):
        processor = Mock(name='processor')
        processor.name, processor.version = 'TEST_ONLY', '1'
        processor.evaluate.side_effect = [IdentityChecks(True, False, True), IdentityChecks(True, True, True)]
        with patch('app.identity_processing.configured_processor', return_value=processor):
            rejected = self._submit()
            self.assertEqual(rejected['estado_identidad'], 'RECHAZADO')
            self.assertIn('datos del DNI', rejected['procesamiento']['observacion'])
            original_ids = {doc['id'] for doc in rejected['documentos']}
            approved = self._submit()
        self.assertEqual(approved['estado_identidad'], 'VERIFICADO')
        self.assertEqual(approved['version'], 2)
        self.assertTrue(original_ids.isdisjoint({doc['id'] for doc in approved['documentos']}))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityDocument)), 6)
        for document_id in original_ids:
            self.assertEqual(self.db.get(IdentityDocument, uuid.UUID(document_id)).estado, 'RECHAZADO')
        history = get_my_identity_history(self.client_claims, self.db)['solicitudes']
        self.assertEqual([item['version'] for item in history], [2, 1])
        self.assertTrue(any(result['estado'] == 'RECHAZADO' for result in history[1]['resultados']))
        self.assertTrue(any(result['estado'] == 'VERIFICADO' for result in history[0]['resultados']))
        user = self.db.scalar(select(User).where(User.firebase_uid == self.client_claims['uid']))
        firebase_user = SimpleNamespace(email=user.correo, email_verified=False, disabled=False)
        with patch('app.email_verification.auth.get_user', return_value=firebase_user):
            self.assertEqual(self._status_of(lambda: require_enabled_client(user)), 403)
            firebase_user.email_verified = True
            self.assertIs(require_enabled_client(user), user)

    def test_processing_error_preserves_documents_and_retry_appends_success_to_same_request(self):
        processor = Mock()
        processor.name, processor.version = 'TEST_ONLY', '1'
        processor.evaluate.side_effect = TimeoutError('private provider detail')
        with patch('app.identity_processing.configured_processor', return_value=processor):
            submitted = self._submit()
        self.assertEqual(submitted['estado_identidad'], 'EN_REVISION')
        self.assertEqual(submitted['procesamiento']['estado'], 'ERROR')
        self.assertNotIn('private provider detail', str(submitted))
        from datetime import datetime, timedelta, timezone
        failure = self.db.scalar(select(IdentityVerificationResult).where(
            IdentityVerificationResult.estado == 'ERROR'))
        failure.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
        self.db.commit()
        processor.evaluate.side_effect = None
        processor.evaluate.return_value = IdentityChecks(True, True, True)
        success = process_current_identity(self.client_claims, self.db, storage=self.storage, processor=processor)
        self.assertEqual(success.estado, 'VERIFICADO')
        self.assertEqual(str(success.solicitud_id), submitted['solicitud_id'])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationRequest)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityDocument)), 3)
        self.db.refresh(failure)
        self.assertEqual(failure.estado, 'ERROR')
        self.assertEqual(get_my_identity_documents(self.client_claims, self.db)['procesamiento']['estado'], 'VERIFICADO')

    def test_other_client_cannot_select_another_expedient_with_extra_claim_fields(self):
        submitted = self._submit()
        forged = {**self.other_claims, 'usuario_id': submitted['solicitud_id'],
                  'solicitud_id': submitted['solicitud_id'], 'rol': 'ADMIN'}
        own = get_my_identity_documents(forged, self.db)
        self.assertEqual(own['documentos'], [])
        self.assertIsNone(own['solicitud_id'])
        self.assertEqual(get_my_identity_history(forged, self.db), {'solicitudes': []})
        # Even a forged ADMIN claim cannot bypass the database role check.
        document_id = uuid.UUID(submitted['documentos'][0]['id'])
        self.assertEqual(self._status_of(lambda: get_document_content_for_admin(
            document_id, forged, self.db, self.storage)), 403)

    def test_invalid_resend_preserves_previous_request_and_rejection(self):
        processor = Mock()
        processor.name, processor.version = 'TEST_ONLY', '1'
        processor.evaluate.return_value = IdentityChecks(True, True, False)
        with patch('app.identity_processing.configured_processor', return_value=processor):
            first = self._submit()
        self.assertEqual(first['estado_identidad'], 'RECHAZADO')
        self.assertEqual(self._status_of(lambda: self._submit(selfie=UploadedPart(b'bad', 'image/jpeg'))), 400)
        current = get_my_identity_documents(self.client_claims, self.db)
        self.assertEqual(current['solicitud_id'], first['solicitud_id'])
        self.assertEqual(current['estado_identidad'], 'RECHAZADO')
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationRequest)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityDocument)), 3)


if __name__ == "__main__":
    unittest.main()

