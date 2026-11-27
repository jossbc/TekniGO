import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from firebase_admin import auth, exceptions
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ACCOUNT_DELETED, Service, Specialty, TechnicianProfile, User, UserRole
from app.technician_activation import run_scheduled_expiration_review
from app.users import RoleAddition, add_role, delete_my_account, me


class DeleteAccountTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.user = User(firebase_uid="owner", correo="owner@example.com", nombre_completo="Titular",
                         dni="0801200000001", telefono="+50490000001", telefono_verificado=True)
        self.other = User(firebase_uid="other", correo="other@example.com", nombre_completo="Otra cuenta",
                          dni="0801200000002", telefono="+50490000002")
        category = Specialty(nombre="Electricidad")
        self.db.add_all([self.user, self.other, category])
        self.db.flush()
        self.technician = TechnicianProfile(usuario_id=self.user.id, disponible=True)
        self.db.add(self.technician)
        self.db.add_all(UserRole(usuario_id=self.user.id, rol=role) for role in ("TECNICO", "CLIENTE", "ADMIN"))
        self.db.flush()
        self.service = Service(usuario_id=self.user.id, especialidad_id=category.id,
                               nombre="Instalación", descripcion="Prueba de eliminación")
        self.db.add(self.service)
        self.db.commit()
        self.claims = {"uid": "owner", "auth_time": time.time()}

    def test_rejects_stale_session_without_changes(self):
        with self.assertRaises(HTTPException) as failure:
            delete_my_account({**self.claims, "auth_time": time.time() - 600}, self.db)
        self.assertEqual(failure.exception.status_code, 403)
        self.assertEqual(self.user.nombre_completo, "Titular")

    @patch("app.users.auth.delete_user")
    def test_erases_profile_and_removes_firebase_login(self, remove_identity):
        original_id = self.user.id
        delete_my_account(self.claims, self.db)
        remove_identity.assert_called_once_with("owner")
        self.assertEqual(self.user.id, original_id)
        self.assertEqual(self.user.estado_identidad, ACCOUNT_DELETED)
        self.assertEqual(self.user.roles, [])
        self.assertNotEqual(self.user.dni, "0801200000001")
        self.assertNotEqual(self.user.correo, "owner@example.com")
        self.assertFalse(self.technician.disponible)
        self.assertFalse(self.service.activo)
        self.assertEqual(self.other.nombre_completo, "Otra cuenta")
        self.assertEqual(run_scheduled_expiration_review(self.db)["total_evaluados"], 0)
        for action in (lambda: me(self.claims, self.db),
                       lambda: add_role(RoleAddition(rol="CLIENTE"), self.claims, self.db)):
            with self.assertRaises(HTTPException) as failure:
                action()
            self.assertEqual(failure.exception.status_code, 410)

    @patch("app.users.auth.delete_user")
    def test_already_deleted_profile_can_finish_removing_firebase_login(self, remove_identity):
        self.user.estado_identidad = ACCOUNT_DELETED
        self.db.commit()
        remove_identity.side_effect = [exceptions.FirebaseError("UNAVAILABLE", "Unavailable"), None,
                                       auth.UserNotFoundError("Already removed")]
        with self.assertRaises(HTTPException) as failure:
            delete_my_account(self.claims, self.db)
        self.assertEqual(failure.exception.status_code, 503)
        delete_my_account(self.claims, self.db)
        delete_my_account(self.claims, self.db)
        self.assertEqual([call.args for call in remove_identity.call_args_list], [("owner",)] * 3)
