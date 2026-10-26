import unittest
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Specialty, TechnicianProfile, TechnicianSpecialty, User, UserRole
from app.specialties import SpecialtySelection, list_specialties, my_specialties, save_my_specialties


class SpecialtyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tech = self.add_user('tech-1', 'tecnico1@example.com', '0801200000001', '+50491111111', 'TECNICO')
        self.other_tech = self.add_user('tech-2', 'tecnico2@example.com', '0801200000002', '+50492222222', 'TECNICO')
        self.client = self.add_user('client-1', 'cliente@example.com', '0801200000003', '+50493333333', 'CLIENTE')
        self.fontaneria = self.add_specialty('Fontanería')
        self.electricidad = self.add_specialty('Electricidad')
        self.pintura = self.add_specialty('Pintura')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_user(self, uid, email, dni, phone, role):
        user = User(firebase_uid=uid, correo=email, nombre_completo='Persona Test', dni=dni, telefono=phone)
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        if role == 'TECNICO':
            self.db.add(TechnicianProfile(usuario_id=user.id))
        return {'uid': uid}

    def add_specialty(self, name):
        specialty = Specialty(nombre=name)
        self.db.add(specialty)
        self.db.flush()
        return specialty.id

    def save(self, ids, claims=None):
        return save_my_specialties(SpecialtySelection(especialidad_ids=ids), claims or self.tech, self.db)

    def names(self, result):
        return [item['nombre'] for item in result]

    def test_catalog_lists_available_specialties_sorted(self):
        result = list_specialties(self.tech, self.db)
        self.assertEqual(self.names(result), ['Electricidad', 'Fontanería', 'Pintura'])

    def test_technician_can_select_several_specialties(self):
        result = self.save([self.fontaneria, self.pintura])
        self.assertEqual(self.names(result), ['Fontanería', 'Pintura'])

    def test_selected_specialties_are_saved_in_profile(self):
        self.save([self.fontaneria, self.electricidad])
        result = my_specialties(self.tech, self.db)
        self.assertEqual(self.names(result), ['Electricidad', 'Fontanería'])

    def test_new_selection_replaces_previous_one(self):
        self.save([self.fontaneria, self.electricidad])
        result = self.save([self.electricidad, self.pintura])
        self.assertEqual(self.names(result), ['Electricidad', 'Pintura'])
        self.assertEqual(len(self.db.scalars(select(TechnicianSpecialty)).all()), 2)

    def test_repeated_specialty_in_request_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.save([self.fontaneria, self.fontaneria])
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(my_specialties(self.tech, self.db), [])

    def test_database_rejects_duplicate_pair(self):
        owner = self.db.scalars(select(User).where(User.firebase_uid == 'tech-1')).one().id
        row = {'usuario_id': owner, 'especialidad_id': self.fontaneria}
        self.db.execute(insert(TechnicianSpecialty).values(**row))
        with self.assertRaises(IntegrityError):
            self.db.execute(insert(TechnicianSpecialty).values(**row))
        self.db.rollback()

    def test_unknown_specialty_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.save([self.fontaneria, uuid.uuid4()])
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(my_specialties(self.tech, self.db), [])

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(ValidationError):
            SpecialtySelection(especialidad_ids=[])

    def test_unexpected_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            SpecialtySelection(especialidad_ids=[self.fontaneria], usuario_id='otro')

    def test_client_cannot_manage_specialties(self):
        for action in (lambda: my_specialties(self.client, self.db), lambda: self.save([self.fontaneria], self.client)):
            with self.assertRaises(HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 403)

    def test_unregistered_account_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            my_specialties({'uid': 'desconocido'}, self.db)
        self.assertEqual(caught.exception.status_code, 404)

    def test_technician_only_changes_own_specialties(self):
        self.save([self.fontaneria], self.other_tech)
        self.save([self.pintura])
        self.assertEqual(self.names(my_specialties(self.tech, self.db)), ['Pintura'])
        self.assertEqual(self.names(my_specialties(self.other_tech, self.db)), ['Fontanería'])


if __name__ == '__main__':
    unittest.main()