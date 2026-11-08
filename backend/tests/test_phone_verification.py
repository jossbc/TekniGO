from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi import HTTPException
from firebase_admin import auth, exceptions
from app.phone_verification import phone_verification_status, confirm_phone_verification


class PhoneVerificationTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=uuid.uuid4(), firebase_uid='user-1', telefono='+50498765432', telefono_verificado=False)

    @patch('app.phone_verification.auth.get_user')
    def test_only_number_of_the_same_firebase_account_matches(self, get_user):
        for number, expected in [(None, False), ('+50499999999', False), ('+50498765432', True)]:
            get_user.return_value = SimpleNamespace(phone_number=number, disabled=False)
            self.assertEqual(phone_verification_status(self.user)['telefono_verificado'], expected)
        get_user.assert_called_with('user-1')

    @patch('app.phone_verification.auth.get_user')
    def test_disabled_deleted_or_unavailable_provider_never_approves(self, get_user):
        get_user.return_value = SimpleNamespace(phone_number=self.user.telefono, disabled=True)
        with self.assertRaises(HTTPException): phone_verification_status(self.user)
        for failure, status in [(auth.UserNotFoundError('missing'), 401), (exceptions.FirebaseError('UNAVAILABLE', 'private message'), 503)]:
            get_user.side_effect = failure
            with self.assertRaises(HTTPException) as caught: phone_verification_status(self.user)
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn('private message', caught.exception.detail)

    @patch('app.phone_verification.auth.get_user')
    def test_sync_ignores_forged_claims_and_phone_change_invalidates_verification(self, get_user):
        db = Mock(); db.scalar.return_value = self.user; db.get.return_value = None
        get_user.return_value = SimpleNamespace(phone_number=self.user.telefono, disabled=False)
        self.assertTrue(confirm_phone_verification({'uid':'user-1', 'telefono_verificado':False}, db)['telefono_verificado'])
        self.assertTrue(self.user.telefono_verificado)
        self.user.telefono = '+50499999999'
        self.assertFalse(confirm_phone_verification({'uid':'user-1', 'telefono_verificado':True}, db)['telefono_verificado'])
        self.assertFalse(self.user.telefono_verificado)
        query = db.scalar.call_args.args[0]
        self.assertIsNotNone(query._for_update_arg)


if __name__ == '__main__': unittest.main()
