import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth
from google.auth import jwt

from app.security import get_current_user, TOKEN_CLOCK_SKEW_SECONDS


class SecurityTests(unittest.TestCase):
    @patch('google.auth._helpers.utcnow', return_value=datetime.fromtimestamp(1000, timezone.utc))
    def test_small_clock_difference_is_allowed_but_larger_skew_and_expiry_are_rejected(self, _now):
        jwt._verify_iat_and_exp({'iat': 1001, 'exp': 4601}, TOKEN_CLOCK_SKEW_SECONDS)
        for claims in ({'iat': 1006, 'exp': 4606}, {'iat': 900, 'exp': 994}):
            with self.subTest(claims=claims), self.assertRaises(ValueError):
                jwt._verify_iat_and_exp(claims, TOKEN_CLOCK_SKEW_SECONDS)

    @patch('app.security.auth.verify_id_token')
    def test_invalid_sessions_are_rejected(self, verify):
        for error in (
            auth.InvalidIdTokenError('invalid'),
            auth.ExpiredIdTokenError('expired', cause=None),
            auth.RevokedIdTokenError('revoked'),
            auth.UserDisabledError('disabled'),
            ValueError('malformed'),
        ):
            with self.subTest(error=type(error).__name__):
                verify.side_effect = error
                with self.assertRaises(HTTPException) as caught:
                    get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials='test-token'))
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.headers, {'WWW-Authenticate': 'Bearer'})

    @patch('app.security.auth.verify_id_token', return_value={'uid': 'test'})
    def test_valid_token_still_checks_revocation_and_clock_tolerance(self, verify):
        claims = get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials='valid-token'))
        self.assertEqual(claims, {'uid': 'test'})
        verify.assert_called_once_with('valid-token', check_revoked=True, clock_skew_seconds=5)
