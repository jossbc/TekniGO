import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, Mock

from app.identity_processing import configured_processor, _decision
from app.identity_text import compare_document_text
from app.python_identity import PythonIdentityProcessor
from app.identity_worker import compare_faces


class FaceDiagnosticTests(unittest.TestCase):
    class NoFace(ValueError):
        pass

    def setUp(self):
        self.deepface = Mock()
        self.images = {'DNI_FRONTAL': object(), 'SELFIE': object()}

    def face(self, width=200, height=200):
        return {'face': object(), 'facial_area': {'w': width, 'h': height}}

    def compare(self):
        return compare_faces(self.images, self.deepface, not_detected_error=self.NoFace)

    def test_no_face_on_front_does_not_skip_selfie_diagnostic(self):
        self.deepface.extract_faces.side_effect = [self.NoFace('private details'), [self.face()]]
        match, diagnostic = self.compare()
        self.assertIsNone(match)
        self.assertEqual(diagnostic['DNI_FRONTAL']['rostros_detectados'], 0)
        self.assertEqual(diagnostic['SELFIE']['rostros_detectados'], 1)
        self.deepface.verify.assert_not_called()
        self.assertNotIn('private details', str(diagnostic))

    def test_multiple_document_portraits_do_not_choose_a_face_automatically(self):
        self.deepface.extract_faces.side_effect = [[self.face(), self.face()], [self.face()]]
        match, diagnostic = self.compare()
        self.assertIsNone(match)
        self.assertEqual(diagnostic['DNI_FRONTAL']['estado'], 'MULTIPLES_ROSTROS')
        self.deepface.verify.assert_not_called()

    def test_single_face_per_image_runs_comparison(self):
        for verified in (True, False):
            with self.subTest(verified=verified):
                self.deepface.extract_faces.side_effect = [[self.face()], [self.face()]]
                self.deepface.verify.return_value = {'verified': verified}
                match, diagnostic = self.compare()
                self.assertEqual(match, verified)
                self.assertEqual(diagnostic['comparacion']['estado'], 'COINCIDE' if verified else 'NO_COINCIDE')

    def test_detector_and_comparison_errors_are_distinguishable(self):
        self.deepface.extract_faces.side_effect = [RuntimeError('private detector details'), [self.face()]]
        match, diagnostic = self.compare()
        self.assertIsNone(match)
        self.assertEqual(diagnostic['DNI_FRONTAL']['estado'], 'ERROR_DETECCION')
        self.assertNotIn('private detector details', str(diagnostic))
        self.deepface.extract_faces.side_effect = [[self.face()], [self.face()]]
        self.deepface.verify.side_effect = ValueError('private comparison details')
        match, diagnostic = self.compare()
        self.assertIsNone(match)
        self.assertEqual(diagnostic['comparacion']['estado'], 'ERROR_COMPARACION')
        self.assertNotIn('private comparison details', str(diagnostic))

    def test_large_main_document_portrait_is_compared_using_its_exact_crop(self):
        main, secondary, selfie = self.face(300, 300), self.face(100, 100), self.face()
        self.deepface.extract_faces.side_effect = [[secondary, main], [selfie]]
        self.deepface.verify.return_value = {'verified': True}
        match, diagnostic = self.compare()
        self.assertTrue(match)
        self.assertEqual(diagnostic['DNI_FRONTAL']['seleccion'], 'RETRATO_PRINCIPAL_POR_TAMANO')
        self.assertEqual(diagnostic['DNI_FRONTAL']['rostros_detectados'], 2)
        self.assertIs(self.deepface.verify.call_args.kwargs['img1_path'], main['face'])
        self.assertIs(self.deepface.verify.call_args.kwargs['img2_path'], selfie['face'])
        self.assertEqual(self.deepface.verify.call_args.kwargs['detector_backend'], 'skip')

    def test_distance_diagnostic_does_not_override_the_model_decision(self):
        self.deepface.extract_faces.side_effect = [[self.face()], [self.face()]]
        self.deepface.verify.return_value = {'verified': False, 'distance': .42, 'threshold': .3}
        match, diagnostic = self.compare()
        self.assertFalse(match)
        self.assertEqual(diagnostic['comparacion'], {'estado': 'NO_COINCIDE', 'distancia': .42, 'umbral': .3})

    def test_ambiguous_document_faces_or_multiple_selfie_faces_remain_uncertain(self):
        for documents, selfies in (
            ([self.face(200, 200), self.face(180, 180)], [self.face()]),
            ([self.face(300, 300), self.face(80, 80)], [self.face(), self.face(30, 30)]),
            ([self.face(), self.face(), self.face()], [self.face()]),
        ):
            self.deepface.extract_faces.side_effect = [documents, selfies]
            match, diagnostic = self.compare()
            self.assertIsNone(match)
            self.assertEqual(diagnostic['comparacion']['estado'], 'OMITIDA')
        self.deepface.verify.assert_not_called()

DNI = '0801200000001'


class IdentityTextTests(unittest.TestCase):
    def compare(self, lines, name='Ana María López'):
        return compare_document_text(lines, dni=DNI, nombre_completo=name)

    def test_exact_dni_and_accent_normalized_full_name(self):
        lines = [('DNI: 0801-2000-00001', .98), ('ANA MARIA', .99), ('LOPEZ', .99)]
        self.assertEqual(self.compare(lines), (True, True))

    def test_wrong_or_ambiguous_number_never_matches(self):
        self.assertEqual(self.compare([('0801200000002', .99)]), (True, False))
        self.assertEqual(self.compare([(DNI, .99), ('0801200000002', .99)]), (None, None))

    def test_missing_name_and_low_confidence_do_not_claim_discrepancy(self):
        self.assertEqual(self.compare([(DNI, .99), ('ANA', .99)]), (True, None))
        self.assertEqual(self.compare([(DNI, .5), ('ANA MARIA LOPEZ', .99)]), (None, None))

    def test_number_must_not_be_truncated_from_longer_numeric_sequence(self):
        for value in ('1' + DNI, '1 ' + DNI, DNI + '1', DNI + ' 1'):
            self.assertEqual(self.compare([(value, .99)]), (None, None))


class PythonProcessorTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'IDENTITY_PYTHON': __import__('sys').executable})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def evaluate(self):
        return PythonIdentityProcessor().evaluate(dni=DNI, nombre_completo='Ana Lopez', documents={'SELFIE': b'private-image'})

    @patch('app.python_identity.subprocess.run')
    def test_worker_cannot_assert_liveness_or_authenticity(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            'controles': {key: True for key in ('documento_legible', 'datos_coinciden', 'rostro_coincide', 'documento_autentico', 'vida_confirmada')},
            'modelos': {'ocr': 'PP-OCRv5/es'},
        }))
        checks = self.evaluate()
        self.assertTrue(checks.rostro_coincide)
        self.assertIsNone(checks.documento_autentico)
        self.assertIsNone(checks.vida_confirmada)
        self.assertEqual(_decision(checks)[0], 'VERIFICADO')

    @patch('app.python_identity.subprocess.run')
    def test_private_data_uses_stdin_and_backend_credentials_are_not_inherited(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, '')
        with patch.dict(os.environ, {'DATABASE_URL': 'secret-db', 'SUPABASE_SERVICE_ROLE_KEY': 'secret-key',
                                     'PRIVATE_STORAGE_SECRET': 'secret-storage', 'GOOGLE_APPLICATION_CREDENTIALS': 'secret-file'}):
            with self.assertRaises(RuntimeError):
                self.evaluate()
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertNotIn(DNI, str(command))
        self.assertEqual(json.loads(options['input'])['dni'], DNI)
        for key in ('DATABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'PRIVATE_STORAGE_SECRET', 'GOOGLE_APPLICATION_CREDENTIALS'):
            self.assertNotIn(key, options['env'])
        self.assertEqual(options['timeout'], 180)

    @patch('app.python_identity.subprocess.run')
    def test_timeout_and_invalid_output_fail_closed(self, run):
        run.side_effect = subprocess.TimeoutExpired('worker', 180)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.evaluate()
        run.side_effect = None
        for output in ('not json', json.dumps({'controles': {}})):
            run.return_value = subprocess.CompletedProcess([], 0, output)
            with self.assertRaises(ValueError):
                self.evaluate()

    def test_configuration_selects_only_server_enabled_adapter(self):
        with patch.dict(os.environ, {'IDENTITY_PROCESSOR': ''}):
            self.assertIsNone(configured_processor())
        with patch.dict(os.environ, {'IDENTITY_PROCESSOR': 'python'}):
            self.assertIsInstance(configured_processor(), PythonIdentityProcessor)


if __name__ == '__main__':
    unittest.main()
