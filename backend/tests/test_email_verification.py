import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from firebase_admin import auth, exceptions

from app.email_verification import email_verification_status, require_verified_email, get_email_verification
from app.users import ProfileUpdate, update_me


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(firebase_uid='user-1', correo='ana@example.com')

    @patch('app.email_verification.auth.get_user')
    def test_only_current_verified_matching_address_is_accepted(self, get_user):
        for email, verified, expected in [
            ('ana@example.com', True, True), ('ANA@example.com', True, True),
            ('ana@example.com', False, False), ('old@example.com', True, False),
            (None, True, False),
        ]:
            with self.subTest(email=email, verified=verified):
                get_user.return_value = SimpleNamespace(email=email, email_verified=verified, disabled=False)
                self.assertEqual(email_verification_status(self.user)['email_verified'], expected)
        get_user.assert_called_with('user-1')

    @patch('app.email_verification.auth.get_user')
    def test_guard_denies_pending_and_allows_verified_email(self, get_user):
        get_user.return_value = SimpleNamespace(email='ana@example.com', email_verified=False, disabled=False)
        with self.assertRaises(HTTPException) as error:
            require_verified_email(self.user)
        self.assertEqual(error.exception.status_code, 403)
        get_user.return_value.email_verified = True
        require_verified_email(self.user)

    @patch('app.email_verification.auth.get_user')
    def test_disabled_deleted_and_unavailable_do_not_approve(self, get_user):
        get_user.return_value = SimpleNamespace(email='ana@example.com', email_verified=True, disabled=True)
        with self.assertRaises(HTTPException) as error:
            email_verification_status(self.user)
        self.assertEqual(error.exception.status_code, 401)
        for failure, status in [(auth.UserNotFoundError('missing'), 401), (exceptions.FirebaseError('UNAVAILABLE', 'offline'), 503)]:
            get_user.side_effect = failure
            with self.assertRaises(HTTPException) as error:
                email_verification_status(self.user)
            self.assertEqual(error.exception.status_code, status)

    @patch('app.email_verification.auth.get_user')
    def test_endpoint_ignores_client_verified_flag_and_requires_registered_account(self, get_user):
        db = Mock()
        db.scalar.return_value = self.user
        get_user.return_value = SimpleNamespace(email='ana@example.com', email_verified=False, disabled=False)
        self.assertFalse(get_email_verification({'uid': 'user-1', 'email_verified': True}, db)['email_verified'])
        db.scalar.return_value = None
        with self.assertRaises(HTTPException) as error:
            get_email_verification({'uid': 'unknown'}, db)
        self.assertEqual(error.exception.status_code, 404)

    @patch('app.users.auth.update_user')
    def test_email_change_resets_verification(self, update_user):
        user = SimpleNamespace(id='id', firebase_uid='user-1', correo='ana@example.com', telefono='+50499999999',
                               nombre_completo='Ana', roles=[], estado_identidad='PENDIENTE', dni='0801200012345')
        db = Mock()
        db.scalar.side_effect = [user, None]
        update_me(ProfileUpdate(correo='new@example.com'), {'uid': 'user-1'}, db)
        update_user.assert_called_once_with('user-1', email='new@example.com', email_verified=False)


if __name__ == '__main__':
    unittest.main()
