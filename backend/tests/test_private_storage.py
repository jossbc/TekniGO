import hashlib
import hmac
import os
import tempfile
import unittest
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.storage import (
    PrivateDocumentStorage, encrypt_bytes, decrypt_bytes, _cipher_stream,
)

SECRET = 'test-only-private-storage-secret-32-bytes'
IMAGE = b'\xff\xd8\xff' + b'private document'


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'PRIVATE_STORAGE_SECRET': SECRET,
            'PRIVATE_STORAGE_LEGACY_SECRET': '',
            'SUPABASE_URL': 'https://example.supabase.co',
            'SUPABASE_SERVICE_ROLE_KEY': 'server-key',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_authenticated_encryption_rejects_wrong_key_or_modified_content(self):
        payload = encrypt_bytes(IMAGE)
        self.assertTrue(payload.startswith(b'TKGO2'))
        self.assertNotIn(IMAGE, payload)
        self.assertNotEqual(payload, encrypt_bytes(IMAGE))
        self.assertEqual(decrypt_bytes(payload), IMAGE)
        for broken in (payload[:-1], payload[:20] + bytes([payload[20] ^ 1]) + payload[21:]):
            with self.assertRaises(ValueError):
                decrypt_bytes(broken)
        with self.assertRaises(ValueError):
            decrypt_bytes(payload, 'another-strong-secret-of-at-least-32-bytes')

    def test_missing_or_weak_key_never_encrypts(self):
        for secret in ('', 'short', 'teknigo-private-storage-key'):
            with patch.dict(os.environ, {'PRIVATE_STORAGE_SECRET': secret}):
                with self.assertRaises(HTTPException) as error:
                    encrypt_bytes(IMAGE)
                self.assertEqual(error.exception.status_code, 503)

    def test_legacy_reader_requires_explicit_original_secret(self):
        # Fixture representing files produced before TKGO2, including the old default.
        legacy = 'teknigo-private-storage-key'
        nonce = b'0' * 16
        enc_key = hashlib.sha256(b'enc:' + legacy.encode()).digest()
        mac_key = hashlib.sha256(b'mac:' + legacy.encode()).digest()
        ciphertext = _cipher_stream(IMAGE, enc_key, nonce)
        payload = b'TKGO1' + nonce + hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest() + ciphertext
        with self.assertRaises(ValueError):
            decrypt_bytes(payload)
        with patch.dict(os.environ, {'PRIVATE_STORAGE_LEGACY_SECRET': legacy}):
            self.assertEqual(decrypt_bytes(payload), IMAGE)
            self.assertTrue(encrypt_bytes(IMAGE).startswith(b'TKGO2'))

    @patch('app.storage._private_urlopen')
    def test_supabase_round_trip_uses_authenticated_endpoint_and_encrypted_bytes(self, open_url):
        response = MagicMock()
        response.status = 200
        open_url.return_value.__enter__.return_value = response
        store = PrivateDocumentStorage()
        saved = store.store(user_id=uuid.uuid4(), doc_type='SELFIE', content=IMAGE,
                            declared_content_type='image/jpeg', label='selfie')
        upload = open_url.call_args.args[0]
        self.assertEqual(upload.get_method(), 'POST')
        self.assertEqual(upload.get_header('Authorization'), 'Bearer server-key')
        self.assertNotIn(IMAGE, upload.data)
        response.read.return_value = upload.data
        self.assertEqual(store.read_private(saved.ruta_privada), IMAGE)
        download = open_url.call_args.args[0]
        self.assertEqual(download.get_method(), 'GET')
        self.assertIn('/object/authenticated/documentos-identidad/identidad/', download.full_url)
        self.assertEqual(download.get_header('Apikey'), 'server-key')
        response.read.assert_called_once()

    @patch('app.storage._private_urlopen')
    def test_private_download_errors_do_not_expose_provider_details(self, open_url):
        path = f'private://documentos-identidad/identidad/{uuid.uuid4()}/selfie_{uuid.uuid4().hex}.jpg.enc'
        for code, expected in ((404, FileNotFoundError), (401, HTTPException), (403, HTTPException), (500, HTTPException)):
            open_url.side_effect = urllib.error.HTTPError('https://example', code, 'sensitive provider details', {}, None)
            with self.assertRaises(expected) as error:
                PrivateDocumentStorage().read_private(path)
            self.assertNotIn('sensitive provider details', str(error.exception))
        open_url.side_effect = TimeoutError('sensitive details')
        with self.assertRaises(HTTPException) as error:
            PrivateDocumentStorage().read_private(path)
        self.assertEqual(error.exception.status_code, 502)

    @patch('app.storage._private_urlopen')
    def test_bad_paths_and_partial_config_never_make_remote_request(self, open_url):
        store = PrivateDocumentStorage()
        for path in ('private://other/file', 'private://documentos-identidad/../secret',
                     'private://documentos-identidad/identidad/%2e%2e/file'):
            with self.assertRaises(ValueError):
                store.read_private(path)
        for values in ({'SUPABASE_SERVICE_ROLE_KEY': ''}, {'SUPABASE_URL': 'http://example.supabase.co'},
                       {'SUPABASE_URL': 'https://example.supabase.co/other'}):
            with patch.dict(os.environ, values):
                with self.assertRaises(HTTPException):
                    store.store(user_id=uuid.uuid4(), doc_type='SELFIE', content=IMAGE,
                                declared_content_type='image/jpeg', label='selfie')
        open_url.assert_not_called()

    def test_explicit_local_storage_round_trip(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateDocumentStorage(base_dir=Path(directory))
            saved = store.store(user_id=uuid.uuid4(), doc_type='DNI_FRONTAL', content=IMAGE,
                                declared_content_type='image/jpeg', label='DNI')
            self.assertEqual(store.read_private(saved.ruta_privada), IMAGE)

    @patch('app.storage._private_urlopen')
    def test_corrupted_or_oversized_remote_file_never_returns_content(self, open_url):
        from app.storage import MAX_DOCUMENT_BYTES
        path = f'private://documentos-identidad/identidad/{uuid.uuid4()}/selfie_{uuid.uuid4().hex}.jpg.enc'
        response = MagicMock()
        response.status = 200
        open_url.return_value.__enter__.return_value = response
        for content in (b'not encrypted', encrypt_bytes(IMAGE)[:-1], b'x' * (MAX_DOCUMENT_BYTES + 1025)):
            response.read.return_value = content
            with self.assertRaises(HTTPException) as error:
                PrivateDocumentStorage().read_private(path)
            self.assertEqual(error.exception.status_code, 502)

    def test_missing_supabase_config_does_not_silently_fall_back_to_disk(self):
        with patch.dict(os.environ, {'SUPABASE_URL': '', 'SUPABASE_SERVICE_ROLE_KEY': '',
                                     'PRIVATE_STORAGE_BACKEND': ''}):
            with self.assertRaises(HTTPException) as error:
                PrivateDocumentStorage()._supabase_config()
            self.assertEqual(error.exception.status_code, 503)

    def test_redirects_cannot_forward_private_credentials(self):
        from app.storage import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))


if __name__ == '__main__':
    unittest.main()
