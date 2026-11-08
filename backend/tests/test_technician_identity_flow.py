"""Identity + administration flow using actual encrypted files, without ML/network."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.admin_verification import (
    AdminVerificationReviewSchema, DocumentReviewItem, apply_technician_review,
    get_technician_dossier, process_technician_identity_admin,
)
from app.database import Base
from app.identity_documents import get_document_content_for_admin
from app.identity_processing import IdentityChecks, IdentityProcessingRetry, process_current_identity, identity_processing_status
from app.models import DocumentReviewDecision, IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, TechnicianProfile, User, UserRole
from app.storage import PrivateDocumentStorage
from app.technician_activation import REQUIRED_TECHNICIAN_DOCS
from app.technicians import process_technician_identity_endpoint, upload_verification_documents

IMAGE = b'\xff\xd8\xffsynthetic-identity'
PDF = b'%PDF-1.7 synthetic-background'


class TechnicianIdentityFlowTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict('os.environ', {'PRIVATE_STORAGE_SECRET':'test-only-identity-flow-secret-32-bytes', 'IDENTITY_PROCESSOR':''})
        env.start(); self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.store = PrivateDocumentStorage(base_dir=Path(directory.name))
        self.engine = create_engine('sqlite://'); self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine); self.addCleanup(self.db.close)
        self.tech = self.add_user('tech', 'TECNICO', '1')
        self.admin = self.add_user('admin', 'ADMIN', '2')
        self.client = self.add_user('client', 'CLIENTE', '3')
        self.profile = TechnicianProfile(usuario_id=self.tech.id, disponible=True)
        self.db.add(self.profile)
        self.request = IdentityVerificationRequest(usuario_id=self.tech.id, version=1)
        self.db.add(self.request); self.db.flush()
        self.docs = {}
        for kind, _ in REQUIRED_TECHNICIAN_DOCS:
            background = kind.startswith('ANTECEDENTES')
            saved = self.store.store(user_id=self.tech.id, doc_type=kind, content=PDF if background else IMAGE,
                declared_content_type='application/pdf' if background else 'image/jpeg', label=kind)
            doc = IdentityDocument(usuario_id=self.tech.id, solicitud_id=self.request.id, tipo=kind,
                ruta_privada=saved.ruta_privada, content_type=saved.content_type,
                tamano_bytes=saved.tamano_bytes, sha256=saved.sha256, estado='PENDIENTE',
                fecha_emision_declarada=date.today() if background else None)
            self.db.add(doc); self.db.flush(); self.docs[kind] = doc
        self.db.commit()
        self.processor = Mock(); self.processor.name = 'TEST_ONLY'; self.processor.version = '1'
        self.processor.evaluate.return_value = IdentityChecks(True, True, True)

    def add_user(self, uid, role, suffix):
        user = User(firebase_uid=uid, correo=uid+'@example.com', nombre_completo=uid,
            dni='080120000000'+suffix, telefono='+5049000000'+suffix, telefono_verificado=True)
        self.db.add(user); self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role)); self.db.flush()
        return user

    def process(self):
        return process_current_identity({'uid':'tech'}, self.db, storage=self.store, processor=self.processor)

    def approve(self):
        return apply_technician_review(self.tech.id, AdminVerificationReviewSchema(documentos=[
            DocumentReviewItem(documento_id=doc.id, estado='APROBADO', legible=True, identidad_coincide=True)
            for doc in self.docs.values()
        ]), {'uid':'admin'}, self.db)

    def upload(self, files):
        body = b''
        for field, content in files.items():
            body += b'--bound\r\nContent-Disposition: form-data; name="'+field.encode()+b'"; filename="file.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'+content+b'\r\n'
        body += b'--bound--\r\n'
        async def receive(): return {'type':'http.request', 'body':body, 'more_body':False}
        request = Request({'type':'http', 'method':'POST', 'path':'/', 'headers':[(b'content-type', b'multipart/form-data; boundary=bound')]}, receive)
        with patch('app.technicians.PrivateDocumentStorage', return_value=self.store), patch('app.identity_processing.configured_processor', return_value=self.processor):
            task = upload_verification_documents(request, {'uid':'tech'}, self.db)
            try: task.send(None)
            except StopIteration as completed: return completed.value
            self.fail('The in-memory request unexpectedly suspended')

    def test_positive_automatic_result_does_not_approve_technical_documents(self):
        result = self.process()
        self.assertEqual(result.estado, 'VERIFICADO')
        self.assertTrue(result.evidencia['requiere_revision_administrativa'])
        self.assertEqual(len(result.evidencia['documentos']), 3)
        self.assertEqual(set(self.processor.evaluate.call_args.kwargs['documents']), {'DNI_FRONTAL','DNI_REVERSO','SELFIE'})
        self.assertTrue(all(doc.estado == 'PENDIENTE' for doc in self.docs.values()))
        self.assertEqual(self.tech.estado_identidad, 'EN_REVISION')
        self.assertNotEqual(self.profile.estado_verificacion, 'VERIFICADO')
        dossier = get_technician_dossier(self.tech.id, Response(), {'uid':'admin'}, self.db)
        self.assertEqual(dossier['resultados_identidad'][0]['estado'], 'VERIFICADO')
        self.profile.suspendido = True
        self.db.commit()
        approved = self.approve()
        self.assertEqual(approved['estado_verificacion'], 'VERIFICADO')
        self.assertFalse(approved['puede_recibir_contrataciones'])
        self.assertTrue(self.profile.suspendido)

    def test_real_encrypted_pdf_is_private_and_readable_by_admin_only(self):
        doc = self.docs['ANTECEDENTES_PENALES']
        for uid in ['tech','client']:
            with self.assertRaises(HTTPException) as error:
                get_document_content_for_admin(doc.id, {'uid':uid}, self.db, self.store)
            self.assertEqual(error.exception.status_code, 403)
        content, mime = get_document_content_for_admin(doc.id, {'uid':'admin'}, self.db, self.store)
        self.assertEqual(content, PDF)
        self.assertEqual(mime, 'application/pdf')

    def test_rejection_then_real_file_resubmission_preserves_audit_and_requires_new_review(self):
        self.processor.evaluate.return_value = IdentityChecks(True, True, False)
        old_result = self.process()
        self.assertEqual(old_result.estado, 'RECHAZADO')
        self.assertEqual(self.docs['ANTECEDENTES_PENALES'].estado, 'PENDIENTE')
        with self.assertRaises(HTTPException): self.approve()
        self.db.rollback()
        apply_technician_review(self.tech.id, AdminVerificationReviewSchema(documentos=[
            DocumentReviewItem(documento_id=self.docs['SELFIE'].id, estado='RECHAZADO', observaciones='Nueva selfie requerida')
        ]), {'uid':'admin'}, self.db)
        self.processor.evaluate.return_value = IdentityChecks(True, True, True)
        response = self.upload({field:IMAGE+b'new' for field in ['dni_frontal','dni_reverso','selfie']})
        self.assertEqual(response['version'], 2)
        self.assertEqual(response['procesamiento']['estado'], 'VERIFICADO')
        self.assertTrue(all(doc['estado']=='PENDIENTE' for doc in response['documentos']))
        self.assertFalse(response['puede_recibir_contrataciones'])
        self.assertEqual(len(self.db.scalars(select(IdentityDocument)).all()), 10)
        self.assertEqual(len(self.db.scalars(select(DocumentReviewDecision)).all()), 1)
        self.db.refresh(old_result)
        self.assertEqual(old_result.estado, 'RECHAZADO')
        for doc in self.db.scalars(select(IdentityDocument)).all():
            self.assertTrue(self.store.read_private(doc.ruta_privada))
        with self.assertRaises(HTTPException) as error: self.approve()
        self.assertEqual(error.exception.status_code, 409)

    def test_processing_error_retry_preserves_results_and_never_auto_approves(self):
        self.processor.evaluate.side_effect = TimeoutError('private provider detail')
        error_result = self.process()
        self.assertEqual(error_result.estado, 'ERROR')
        self.assertNotIn('private provider', error_result.motivo)
        with self.assertRaises(HTTPException) as error: self.process()
        self.assertEqual(error.exception.status_code, 429)
        error_result.creado_en = datetime.now(timezone.utc)-timedelta(seconds=61)
        self.db.commit()
        self.processor.evaluate.side_effect = None
        with patch('app.identity_processing.PrivateDocumentStorage', return_value=self.store), patch('app.identity_processing.configured_processor', return_value=self.processor):
            response = process_technician_identity_endpoint(IdentityProcessingRetry(solicitud_id=self.request.id), {'uid':'tech'}, self.db)
        self.assertEqual(response['procesamiento']['estado'], 'VERIFICADO')
        self.assertTrue(all(doc['estado']=='PENDIENTE' for doc in response['documentos']))
        self.assertEqual(len(self.db.scalars(select(IdentityVerificationResult)).all()), 2)
        self.db.refresh(error_result)
        self.assertEqual(error_result.estado, 'ERROR')

    def test_processing_permissions_and_stale_request_are_checked_before_provider(self):
        payload = IdentityProcessingRetry(solicitud_id=uuid.uuid4())
        for uid in ['client', 'tech']:
            with self.assertRaises(HTTPException) as error:
                process_technician_identity_admin(self.tech.id, payload, {'uid':uid}, self.db)
            self.assertEqual(error.exception.status_code, 403)
        for uid, expected in [('client',403), ('tech',409)]:
            with self.assertRaises(HTTPException) as error:
                process_technician_identity_endpoint(payload, {'uid':uid}, self.db)
            self.assertEqual(error.exception.status_code, expected)
        with self.assertRaises(HTTPException) as error:
            process_technician_identity_admin(self.tech.id, payload, {'uid':'admin'}, self.db)
        self.assertEqual(error.exception.status_code, 409)
        self.processor.evaluate.assert_not_called()

    def test_invalid_replacement_does_not_store_partial_files_or_change_history(self):
        before = list(self.store.base_dir.rglob('*.enc'))
        with self.assertRaises(HTTPException) as error:
            self.upload({'dni_frontal':IMAGE+b'new', 'dni_reverso':b'invalid-image'})
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(list(self.store.base_dir.rglob('*.enc')), before)
        self.assertEqual(len(self.db.scalars(select(IdentityVerificationRequest)).all()), 1)
        self.assertTrue(all(doc.estado=='PENDIENTE' for doc in self.docs.values()))


    def prepare_manually_verified_identity_without_backgrounds(self):
        for kind in ("ANTECEDENTES_POLICIALES", "ANTECEDENTES_PENALES"):
            self.db.delete(self.docs.pop(kind))
        self.db.add(IdentityVerificationResult(solicitud_id=self.request.id, estado="RECHAZADO",
            motivo="Falso negativo automático", proceso_responsable="PROCESAMIENTO_IDENTIDAD", version_proceso="2"))
        self.db.commit()
        apply_technician_review(self.tech.id, AdminVerificationReviewSchema(documentos=[
            DocumentReviewItem(documento_id=doc.id, estado="APROBADO", legible=True, identidad_coincide=True,
                observaciones="El administrador comprobó coincidencia de rostro y datos.") for doc in self.docs.values()
        ]), {"uid":"admin"}, self.db)
        self.assertEqual(self.tech.estado_identidad, "VERIFICADO")

    def test_manual_identity_result_takes_precedence_over_old_machine_rejection(self):
        self.prepare_manually_verified_identity_without_backgrounds()
        result = identity_processing_status(self.db, self.request.id)
        self.assertEqual(result["estado"], "VERIFICADO")
        self.assertEqual(result["proceso_responsable"], "REVISION_ADMINISTRATIVA_IDENTIDAD")

    def test_background_upload_keeps_identical_approved_identity_without_reprocessing(self):
        self.prepare_manually_verified_identity_without_backgrounds()
        # An interrupted reload can leave an outdated global state; missing files must remain uploadable.
        self.profile.estado_verificacion = 'EN_REVISION'
        self.db.commit()
        result = self.upload({"antecedentes_policiales":IMAGE, "antecedentes_penales":IMAGE,
            "fecha_policiales":date.today().isoformat().encode(), "fecha_penales":date.today().isoformat().encode()})
        self.assertEqual(result["estado_identidad"], "VERIFICADO")
        self.assertEqual(result["procesamiento"]["proceso_responsable"], "IDENTIDAD_REUTILIZADA")
        self.assertTrue(all(doc["estado"] == ("PENDIENTE" if doc["tipo"].startswith("ANTECEDENTES") else "APROBADO") for doc in result["documentos"]))
        self.processor.evaluate.assert_not_called()
        self.assertEqual(len(self.db.scalars(select(DocumentReviewDecision)).all()), 3)
        self.assertEqual(self.process().proceso_responsable, "IDENTIDAD_REUTILIZADA")

    def test_replacing_identity_file_does_not_carry_over_old_approval(self):
        self.prepare_manually_verified_identity_without_backgrounds()
        result = self.upload({"selfie":IMAGE+b"replacement", "antecedentes_policiales":IMAGE, "antecedentes_penales":IMAGE,
            "fecha_policiales":date.today().isoformat().encode(), "fecha_penales":date.today().isoformat().encode()})
        self.assertNotEqual(result["estado_identidad"], "VERIFICADO")
        self.assertTrue(all(doc["estado"] == "PENDIENTE" for doc in result["documentos"]))
        self.processor.evaluate.assert_called_once()


if __name__ == '__main__': unittest.main()
