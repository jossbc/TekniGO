import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from firebase_admin import exceptions

from app.permissions import (
    client_hiring_eligibility, require_enabled_client, get_enabled_client, get_my_hiring_eligibility,
)


class ClientEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(firebase_uid='client-1', correo='ana@example.com',
                                    estado_identidad='VERIFICADO', roles=[SimpleNamespace(rol='CLIENTE')])
        self.identity = SimpleNamespace(email='ana@example.com', email_verified=True, disabled=False)
        provider = patch('app.email_verification.auth.get_user', return_value=self.identity)
        self.get_user = provider.start()
        self.addCleanup(provider.stop)

    def test_only_both_confirmed_conditions_enable_hiring(self):
        for state in ('PENDIENTE', 'EN_REVISION', 'RECHAZADO', 'INCIERTO', 'ERROR', 'VERIFICADO'):
            for email in (False, True):
                with self.subTest(state=state, email=email):
                    self.user.estado_identidad = state
                    self.identity.email_verified = email
                    status = client_hiring_eligibility(self.user)
                    enabled = state == 'VERIFICADO' and email
                    self.assertEqual(status['puede_contratar'], enabled)
                    self.assertEqual(len(status['motivos']), int(state != 'VERIFICADO') + int(not email))
                    if enabled:
                        self.assertIs(require_enabled_client(self.user), self.user)
                    else:
                        with self.assertRaises(HTTPException) as error:
                            require_enabled_client(self.user)
                        self.assertEqual(error.exception.status_code, 403)

    def test_current_email_is_checked_again_after_previous_approval(self):
        self.assertTrue(client_hiring_eligibility(self.user)['puede_contratar'])
        self.identity.email = 'new@example.com'
        self.assertFalse(client_hiring_eligibility(self.user)['puede_contratar'])
        with self.assertRaises(HTTPException) as error:
            require_enabled_client(self.user)
        self.assertEqual(error.exception.status_code, 403)

    def test_same_account_can_have_both_roles_but_technician_only_cannot_hire(self):
        self.user.roles.append(SimpleNamespace(rol='TECNICO'))
        self.assertIs(require_enabled_client(self.user), self.user)
        self.user.roles = [SimpleNamespace(rol='TECNICO')]
        with self.assertRaises(HTTPException) as error:
            require_enabled_client(self.user)
        self.assertEqual(error.exception.status_code, 403)

    def test_disabled_account_and_provider_failure_never_enable_hiring(self):
        self.identity.disabled = True
        with self.assertRaises(HTTPException) as error:
            require_enabled_client(self.user)
        self.assertEqual(error.exception.status_code, 401)
        self.get_user.side_effect = exceptions.FirebaseError('UNAVAILABLE', 'sensitive provider detail')
        with self.assertRaises(HTTPException) as error:
            require_enabled_client(self.user)
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn('sensitive', error.exception.detail)

    def test_dependency_uses_authenticated_uid_and_ignores_forged_eligibility(self):
        db = Mock()
        db.scalar.return_value = self.user
        self.user.estado_identidad = 'EN_REVISION'
        claims = {'uid': 'client-1', 'puede_contratar': True, 'estado_identidad': 'VERIFICADO', 'email_verified': True}
        with self.assertRaises(HTTPException) as error:
            get_enabled_client(claims, db)
        self.assertEqual(error.exception.status_code, 403)
        query = db.scalar.call_args.args[0]
        self.assertTrue(query._execution_options['populate_existing'])
        self.assertIsNotNone(query._for_update_arg)
        self.assertEqual(query.compile().params['firebase_uid_1'], 'client-1')

    def test_unregistered_user_cannot_query_or_pass_guard(self):
        db = Mock()
        db.scalar.return_value = None
        for action in (get_enabled_client, get_my_hiring_eligibility):
            with self.assertRaises(HTTPException) as error:
                action({'uid': 'missing'}, db)
            self.assertEqual(error.exception.status_code, 404)
        self.get_user.assert_not_called()

    def test_reading_status_does_not_authorize_a_later_confirmation(self):
        db = Mock()
        db.scalar.return_value = self.user
        self.assertTrue(get_my_hiring_eligibility({'uid': 'client-1'}, db)['puede_contratar'])
        self.identity.email_verified = False
        with self.assertRaises(HTTPException) as error:
            get_enabled_client({'uid': 'client-1'}, db)
        self.assertEqual(error.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
