import unittest
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.database import Base
from app.models import User, TechnicianProfile
from app.security import get_current_user
from app.users import Registration, RoleAddition, add_role, register, me, logout

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.claims = {'uid': 'firebase-test', 'email': 'ana@example.com'}
        self.payload = {'nombre_completo': 'Ana Test', 'dni': '0801-2000-12345', 'telefono': '9999-9999', 'rol': 'CLIENTE'}
    def tearDown(self):
        self.db.close()
        self.engine.dispose()
    def register(self, **changes):
        return register(Registration(**{**self.payload, **changes}), self.claims, self.db)
    def test_same_identity_can_have_both_roles(self):
        first = self.register()
        for _ in range(2):
            result = self.register(rol='TECNICO')
            self.assertEqual(result['roles'], ['CLIENTE', 'TECNICO'])
            self.assertEqual(result['id'], first['id'])
        self.assertEqual(len(self.db.scalars(select(User)).all()), 1)
        technician = self.db.scalars(select(TechnicianProfile)).one()
        self.assertFalse(technician.disponible)
        self.assertEqual(technician.estado_verificacion, 'NO_VERIFICADO')
    def test_add_role_uses_saved_identity_and_is_idempotent(self):
        first = self.register()
        before = self.db.scalars(select(User)).one()
        identity = (before.nombre_completo, before.correo, before.dni, before.telefono, before.estado_identidad)
        for _ in range(2):
            result = add_role(RoleAddition(rol='TECNICO'), self.claims, self.db)
            self.assertEqual(result['id'], first['id'])
            self.assertEqual(result['roles'], ['CLIENTE', 'TECNICO'])
        after = self.db.scalars(select(User)).one()
        self.assertEqual((after.nombre_completo, after.correo, after.dni, after.telefono, after.estado_identidad), identity)
        self.assertEqual(len(self.db.scalars(select(User)).all()), 1)
        technician = self.db.scalars(select(TechnicianProfile)).one()
        self.assertFalse(technician.disponible)
        self.assertEqual(technician.estado_verificacion, 'NO_VERIFICADO')
    def test_technician_can_add_client_without_resetting_professional_profile(self):
        self.register(rol='TECNICO')
        technician = self.db.scalars(select(TechnicianProfile)).one()
        technician.tarifa_base = 250
        self.db.commit()
        result = add_role(RoleAddition(rol='CLIENTE'), self.claims, self.db)
        self.assertEqual(result['roles'], ['CLIENTE', 'TECNICO'])
        self.assertEqual(self.db.scalars(select(TechnicianProfile)).one().tarifa_base, 250)
    def test_add_role_cannot_grant_admin_or_target_another_user(self):
        for data in ({'rol': 'ADMIN'}, {'rol': 'TECNICO', 'usuario_id': 'other'}, {'rol': 'TECNICO', 'dni': '0801200012345'}):
            with self.assertRaises(ValidationError):
                RoleAddition(**data)
    def test_add_role_requires_registered_account(self):
        self.register()
        with self.assertRaises(HTTPException) as caught:
            add_role(RoleAddition(rol='TECNICO'), {'uid': 'other-user'}, self.db)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(me(self.claims, self.db)['roles'], ['CLIENTE'])
        self.assertEqual(self.db.scalars(select(TechnicianProfile)).all(), [])
    def test_normalized_duplicates_rejected(self):
        self.register()
        self.claims = {'uid': 'other-user', 'email': 'otro@example.com'}
        with self.assertRaises(HTTPException) as caught:
            self.register(dni='0801200012345', telefono='+50499999999')
        self.assertEqual(caught.exception.status_code, 409)
    def test_each_identity_field_is_unique_independently(self):
        self.register()
        self.claims = {'uid': 'other-user', 'email': 'otro@example.com'}
        for values in ({'telefono': '88888888'}, {'dni': '0801200099999'}):
            with self.subTest(values=values):
                with self.assertRaises(HTTPException) as caught:
                    self.register(**values)
                self.assertEqual(caught.exception.status_code, 409)
    def test_registration_stays_pending_identity_verification(self):
        result = self.register()
        self.assertEqual(result['estado_identidad'], 'PENDIENTE')
        self.assertEqual(self.db.scalars(select(User)).one().estado_identidad, 'PENDIENTE')
    def test_required_personal_data_and_formats(self):
        for values in ({'nombre_completo': '  '}, {'dni': ''}, {'telefono': ''}, {'dni': 'ABC'}, {'telefono': 'ABC'}):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    self.register(**values)
    def test_honduran_mobile_formats_normalize_to_same_number(self):
        for value in ('98765432', '9876-5432', '9876 5432', '+50498765432', '+504 9876-5432', ' +504 (9876) 5432 '):
            with self.subTest(value=value):
                data = Registration(**{**self.payload, 'telefono': value})
                self.assertEqual(data.telefono, '+50498765432')
        for value in ('87654321', '34567890'):
            self.assertEqual(Registration(**{**self.payload, 'telefono': value}).telefono, '+504' + value)
    def test_invalid_mobile_numbers_are_rejected_before_persistence(self):
        values = ('+504 00000', '00000000', '9876543', '987654321', '+50398765432',
                  '+504987654321', '9876A432', '++50498765432', '50498765432')
        values += tuple(str(prefix) + '1234567' for prefix in (0, 1, 2, 4, 5, 6, 7))
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.register(telefono=value)
        self.assertEqual(self.db.scalars(select(User)).all(), [])
    def test_registration_cannot_grant_admin(self):
        with self.assertRaises(ValidationError):
            self.register(rol='ADMIN')
    def test_identity_cannot_be_overwritten(self):
        self.register()
        with self.assertRaises(HTTPException) as caught:
            self.register(telefono='88888888', rol='TECNICO')
        self.assertEqual(caught.exception.status_code, 409)
    def test_missing_profile_and_private_fields(self):
        with self.assertRaises(HTTPException) as caught:
            me(self.claims, self.db)
        self.assertEqual(caught.exception.status_code, 404)
        self.register()
        result = me(self.claims, self.db)
        self.assertNotIn('dni', result)
        self.assertNotIn('telefono', result)
    @patch('app.users.auth.revoke_refresh_tokens')
    def test_logout_revokes_authenticated_account(self, revoke):
        logout(self.claims)
        revoke.assert_called_once_with('firebase-test')
    def test_missing_token_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            get_current_user(None)
        self.assertEqual(caught.exception.status_code, 401)

if __name__ == '__main__':
    unittest.main()
