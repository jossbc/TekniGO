import hashlib
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.identity_documents import process_my_identity_endpoint
from app.identity_processing import IdentityChecks, process_current_identity, PROCESS
from app.models import IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, User, UserRole

IMAGE = b'\xff\xd8\xffsynthetic-document'


class IdentityProcessingTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'IDENTITY_PROCESSOR': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(firebase_uid='client', nombre_completo='Ana Test', correo='test@example.com',
                         dni='0801200000001', telefono='+50491111111', estado_identidad='EN_REVISION')
        self.db.add(self.user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=self.user.id, rol='CLIENTE'))
        self.request = IdentityVerificationRequest(usuario_id=self.user.id, version=1)
        self.db.add(self.request)
        self.db.flush()
        for kind in ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'):
            self.db.add(IdentityDocument(
                usuario_id=self.user.id, solicitud_id=self.request.id, tipo=kind,
                ruta_privada=f'private://test/{kind}', content_type='image/jpeg',
                tamano_bytes=len(IMAGE), sha256=hashlib.sha256(IMAGE).hexdigest(), estado='PENDIENTE',
            ))
        self.db.commit()
        self.claims = {'uid': 'client'}
        self.storage = Mock()
        self.storage.read_private.return_value = IMAGE
        self.processor = Mock()
        self.processor.name = 'TEST_ONLY'
        self.processor.version = 'test-1'

    def process(self, checks):
        self.processor.evaluate.return_value = checks
        return process_current_identity(self.claims, self.db, storage=self.storage, processor=self.processor)

    def test_without_provider_pending_and_repeat_does_not_duplicate_result(self):
        first = process_current_identity(self.claims, self.db, storage=self.storage)
        self.assertEqual(first.estado, 'INCIERTO')
        self.assertEqual(self.user.estado_identidad, 'EN_REVISION')
        again = process_current_identity(self.claims, self.db, storage=self.storage)
        self.assertEqual(first.id, again.id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationResult)), 1)
        self.storage.read_private.assert_not_called()
        self.assertTrue(all(value is None for value in first.evidencia['controles'].values()))

    def test_three_required_controls_allow_basic_approval_with_unimplemented_controls(self):
        result = self.process(IdentityChecks(True, True, True))
        self.assertEqual(result.estado, 'VERIFICADO')
        self.assertEqual(self.user.estado_identidad, 'VERIFICADO')
        self.assertEqual(len(result.evidencia['documentos']), 3)
        self.assertEqual(result.evidencia['version_solicitud'], 1)
        self.assertEqual(result.evidencia['proveedor']['version'], 'test-1')
        self.assertEqual(result.proceso_responsable, PROCESS)
        self.assertEqual(result.version_proceso, '2')
        self.assertIsNone(result.evidencia['controles']['vida_confirmada'])
        self.assertIsNone(result.evidencia['controles']['documento_autentico'])
        self.assertEqual(result.evidencia['controles_requeridos'], ['documento_legible', 'datos_coinciden', 'rostro_coincide'])
        self.assertIsNotNone(result.creado_en)
        self.processor.evaluate.assert_called_once_with(
            dni='0801200000001', nombre_completo='Ana Test',
            documents={kind: IMAGE for kind in ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE')},
        )
        self.assertTrue(all(doc.estado == 'APROBADO' for doc in self.db.scalars(select(IdentityDocument))))
        # Approval is final for this document version, not re-run by client retries.
        process_current_identity(self.claims, self.db, storage=self.storage, processor=self.processor)
        self.processor.evaluate.assert_called_once()

    def test_facial_match_alone_does_not_verify_identity(self):
        result = self.process(IdentityChecks(rostro_coincide=True))
        self.assertEqual(result.estado, 'INCIERTO')
        self.assertEqual(self.user.estado_identidad, 'EN_REVISION')

    def test_discrepancy_returns_understandable_rejection(self):
        result = self.process(IdentityChecks(True, False, True, True, True))
        self.assertEqual(result.estado, 'RECHAZADO')
        self.assertIn('datos del DNI', result.motivo)
        self.assertEqual(self.user.estado_identidad, 'RECHAZADO')

    def test_unreadable_document_is_rejected(self):
        result = self.process(IdentityChecks(documento_legible=False))
        self.assertEqual(result.estado, 'RECHAZADO')
        self.assertIn('legible', result.motivo)

    def test_known_negative_controls_are_not_ignored_but_unimplemented_ones_are_optional(self):
        from app.identity_processing import _decision
        for checks in (IdentityChecks(True, True, True, False, True), IdentityChecks(True, True, True, True, False)):
            self.assertEqual(_decision(checks)[0], 'RECHAZADO')
        for checks in (IdentityChecks(True, True, True, None, True), IdentityChecks(True, True, True, True, None)):
            self.assertEqual(_decision(checks)[0], 'VERIFICADO')

    def test_each_required_control_must_be_conclusive_and_positive(self):
        from app.identity_processing import _decision
        for field in ('documento_legible', 'datos_coinciden', 'rostro_coincide'):
            for value, expected in ((None, 'INCIERTO'), (False, 'RECHAZADO')):
                controls = dict(documento_legible=True, datos_coinciden=True, rostro_coincide=True)
                controls[field] = value
                self.assertEqual(_decision(IdentityChecks(**controls))[0], expected)

    def test_policy_change_creates_new_result_without_rewriting_previous_evaluation(self):
        old = IdentityVerificationResult(
            solicitud_id=self.request.id, estado='INCIERTO', motivo='Faltaban controles del alcance anterior',
            proceso_responsable=PROCESS, version_proceso='1',
            evidencia={'controles': {'vida_confirmada': None}},
            creado_en=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
        self.db.add(old)
        self.db.commit()
        result = self.process(IdentityChecks(True, True, True))
        self.assertEqual(result.estado, 'VERIFICADO')
        self.assertNotEqual(result.id, old.id)
        self.db.refresh(old)
        self.assertEqual(old.estado, 'INCIERTO')
        self.assertEqual(old.version_proceso, '1')
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationResult)), 2)

    def test_timeout_is_saved_without_provider_details_and_retry_is_limited(self):
        self.processor.evaluate.side_effect = TimeoutError('sensitive token and DNI')
        result = process_current_identity(self.claims, self.db, storage=self.storage, processor=self.processor)
        self.assertEqual(result.estado, 'ERROR')
        self.assertNotIn('sensitive', result.motivo)
        self.assertEqual(self.user.estado_identidad, 'EN_REVISION')
        with self.assertRaises(HTTPException) as error:
            process_current_identity(self.claims, self.db, storage=self.storage, processor=self.processor)
        self.assertEqual(error.exception.status_code, 429)
        result.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
        self.db.commit()
        self.processor.evaluate.side_effect = None
        next_result = self.process(IdentityChecks(rostro_coincide=True))
        self.assertNotEqual(next_result.id, result.id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(IdentityVerificationResult)), 2)

    def test_changed_file_hash_prevents_provider_call(self):
        self.storage.read_private.return_value = IMAGE + b'tampered'
        result = self.process(IdentityChecks(True, True, True, True, True))
        self.assertEqual(result.estado, 'ERROR')
        self.processor.evaluate.assert_not_called()

    def test_missing_required_document_prevents_provider_call(self):
        doc = self.db.scalar(select(IdentityDocument).where(IdentityDocument.tipo == 'SELFIE'))
        self.db.delete(doc)
        self.db.commit()
        result = self.process(IdentityChecks(True, True, True, True, True))
        self.assertEqual(result.estado, 'INCIERTO')
        self.assertIn('Faltan documentos', result.motivo)
        self.processor.evaluate.assert_not_called()

    def test_malformed_provider_response_cannot_approve(self):
        result = self.process(IdentityChecks(True, True, True, True, 'true'))
        self.assertEqual(result.estado, 'ERROR')
        self.assertEqual(self.user.estado_identidad, 'EN_REVISION')

    def test_old_request_is_not_processed_instead_of_current_version(self):
        newer = IdentityVerificationRequest(usuario_id=self.user.id, version=2)
        self.db.add(newer)
        self.db.commit()
        result = self.process(IdentityChecks(True, True, True, True, True))
        self.assertEqual(result.solicitud_id, newer.id)
        self.assertEqual(result.estado, 'INCIERTO')
        self.processor.evaluate.assert_not_called()

    def test_endpoint_uses_own_uid_and_ignores_extra_identity_claims(self):
        response = process_my_identity_endpoint({
            **self.claims, 'usuario_id': str(uuid.uuid4()), 'estado': 'VERIFICADO',
            'vida_confirmada': True, 'documento_autentico': True,
        }, self.db)
        self.assertEqual(response['estado_identidad'], 'EN_REVISION')
        self.assertEqual(response['procesamiento']['estado'], 'INCIERTO')
        self.assertNotIn('evidencia', response['procesamiento'])

    def test_unknown_account_and_technician_are_not_processed(self):
        with self.assertRaises(HTTPException) as error:
            process_current_identity({'uid': 'missing'}, self.db)
        self.assertEqual(error.exception.status_code, 404)
        role = self.db.scalar(select(UserRole))
        role.rol = 'TECNICO'
        self.db.commit()
        self.db.expire_all()
        with self.assertRaises(HTTPException) as error:
            process_current_identity(self.claims, self.db)
        self.assertEqual(error.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
