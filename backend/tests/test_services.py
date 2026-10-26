import unittest
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Service, Specialty, TechnicianProfile, TechnicianSpecialty, User, UserRole
from app.services import ServiceIn, ServiceUpdate, create_service, my_services, public_services, update_service

NAME = 'Instalacion de tomacorrientes'
DESCRIPTION = 'Instalo y reparo tomacorrientes en casas y locales.'


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tech = self.add_user('tech-1', 'tecnico1@example.com', '0801200000001', '+50491111111', 'TECNICO')
        self.other_tech = self.add_user('tech-2', 'tecnico2@example.com', '0801200000002', '+50492222222', 'TECNICO')
        self.client = self.add_user('client-1', 'cliente@example.com', '0801200000003', '+50493333333', 'CLIENTE')
        self.fontaneria = self.add_specialty('Fontaneria')
        self.electricidad = self.add_specialty('Electricidad')
        self.pintura = self.add_specialty('Pintura')
        # El tecnico 1 tiene registradas Fontaneria y Electricidad (no Pintura); el tecnico 2 solo Pintura.
        self.register(self.tech, self.fontaneria, self.electricidad)
        self.register(self.other_tech, self.pintura)
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

    def user_id(self, claims):
        return self.db.scalars(select(User).where(User.firebase_uid == claims['uid'])).one().id

    def register(self, claims, *specialty_ids):
        for specialty_id in specialty_ids:
            self.db.add(TechnicianSpecialty(usuario_id=self.user_id(claims), especialidad_id=specialty_id))

    def create(self, nombre=NAME, descripcion=DESCRIPTION, especialidad_id=None, claims=None):
        data = ServiceIn(nombre=nombre, descripcion=descripcion, especialidad_id=especialidad_id or self.electricidad)
        return create_service(data, claims or self.tech, self.db)

    def update(self, service_id, claims=None, **changes):
        return update_service(uuid.UUID(service_id), ServiceUpdate(**changes), claims or self.tech, self.db)

    def status_of(self, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        return caught.exception.status_code

    def names(self, result):
        return [item['nombre'] for item in result]

    # ---- CA-01: crear servicio con nombre y descripcion ----

    def test_technician_can_create_service(self):
        result = self.create()
        self.assertEqual(result['nombre'], NAME)
        self.assertEqual(result['descripcion'], DESCRIPTION)
        self.assertTrue(result['activo'])
        self.assertEqual(result['especialidad']['nombre'], 'Electricidad')

    def test_created_service_is_listed(self):
        self.create()
        self.assertEqual(self.names(my_services(self.tech, self.db)), [NAME])

    def test_name_and_description_are_cleaned(self):
        result = self.create(nombre='  Pintura   de   paredes  ', descripcion='  Pinto paredes interiores y exteriores.  ',
                             especialidad_id=self.fontaneria)
        self.assertEqual(result['nombre'], 'Pintura de paredes')
        self.assertEqual(result['descripcion'], 'Pinto paredes interiores y exteriores.')

    def test_short_name_is_rejected(self):
        self.assertEqual(self.status_of(lambda: self.create(nombre='ab')), 400)
        self.assertEqual(my_services(self.tech, self.db), [])

    def test_short_description_is_rejected(self):
        self.assertEqual(self.status_of(lambda: self.create(descripcion='corta')), 400)
        self.assertEqual(my_services(self.tech, self.db), [])

    def test_missing_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            ServiceIn(nombre=NAME, especialidad_id=self.electricidad)
        with self.assertRaises(ValidationError):
            ServiceIn(descripcion=DESCRIPTION, especialidad_id=self.electricidad)

    def test_too_long_name_is_rejected(self):
        with self.assertRaises(ValidationError):
            ServiceIn(nombre='x' * 101, descripcion=DESCRIPTION, especialidad_id=self.electricidad)

    def test_unexpected_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            ServiceIn(nombre=NAME, descripcion=DESCRIPTION, especialidad_id=self.electricidad, usuario_id='otro')
        with self.assertRaises(ValidationError):
            ServiceUpdate(usuario_id='otro')

    # ---- CA-02: el servicio pertenece a una especialidad registrada del tecnico ----

    def test_specialty_must_be_registered_by_technician(self):
        self.assertEqual(self.status_of(lambda: self.create(especialidad_id=self.pintura)), 400)
        self.assertEqual(my_services(self.tech, self.db), [])

    def test_unknown_specialty_is_rejected(self):
        self.assertEqual(self.status_of(lambda: self.create(especialidad_id=uuid.uuid4())), 400)

    def test_duplicate_service_in_same_specialty_is_rejected(self):
        self.create()
        self.assertEqual(self.status_of(lambda: self.create(nombre=NAME.upper())), 409)
        self.assertEqual(len(my_services(self.tech, self.db)), 1)

    def test_same_name_in_other_specialty_is_allowed(self):
        self.create()
        self.create(especialidad_id=self.fontaneria)
        self.assertEqual(len(my_services(self.tech, self.db)), 2)

    def test_two_technicians_can_use_same_service_name(self):
        self.create()
        self.create(especialidad_id=self.pintura, claims=self.other_tech)
        self.assertEqual(len(my_services(self.other_tech, self.db)), 1)

    # ---- CA-03: editar y desactivar ----

    def test_service_can_be_edited(self):
        created = self.create()
        result = self.update(created['id'], nombre='Cambio de interruptores', descripcion='Cambio interruptores danados.')
        self.assertEqual(result['nombre'], 'Cambio de interruptores')
        self.assertEqual(result['descripcion'], 'Cambio interruptores danados.')
        self.assertEqual(self.names(my_services(self.tech, self.db)), ['Cambio de interruptores'])

    def test_service_can_move_to_another_registered_specialty(self):
        created = self.create()
        result = self.update(created['id'], especialidad_id=self.fontaneria)
        self.assertEqual(result['especialidad']['nombre'], 'Fontaneria')

    def test_service_cannot_move_to_unregistered_specialty(self):
        created = self.create()
        self.assertEqual(self.status_of(lambda: self.update(created['id'], especialidad_id=self.pintura)), 400)
        self.assertEqual(my_services(self.tech, self.db)[0]['especialidad']['nombre'], 'Electricidad')

    def test_edit_cannot_create_a_duplicate(self):
        self.create()
        other = self.create(nombre='Cambio de interruptores')
        self.assertEqual(self.status_of(lambda: self.update(other['id'], nombre=NAME)), 409)

    def test_service_can_be_deactivated_and_reactivated(self):
        created = self.create()
        self.assertFalse(self.update(created['id'], activo=False)['activo'])
        self.assertEqual(len(my_services(self.tech, self.db)), 1)  # no se borra: sigue en su lista
        self.assertTrue(self.update(created['id'], activo=True)['activo'])

    def test_deactivating_does_not_delete_the_row(self):
        created = self.create()
        self.update(created['id'], activo=False)
        self.assertEqual(len(self.db.scalars(select(Service)).all()), 1)

    def test_empty_update_is_rejected(self):
        created = self.create()
        self.assertEqual(self.status_of(lambda: self.update(created['id'])), 400)

    def test_null_values_in_update_are_rejected(self):
        created = self.create()
        self.assertEqual(self.status_of(lambda: self.update(created['id'], nombre=None)), 400)

    def test_unknown_service_returns_not_found(self):
        self.assertEqual(self.status_of(lambda: self.update(str(uuid.uuid4()), activo=False)), 404)

    # ---- Permisos: solo el titular ----

    def test_client_cannot_manage_services(self):
        for action in (lambda: my_services(self.client, self.db), lambda: self.create(claims=self.client)):
            self.assertEqual(self.status_of(action), 403)

    def test_unregistered_account_is_rejected(self):
        self.assertEqual(self.status_of(lambda: my_services({'uid': 'desconocido'}, self.db)), 404)

    def test_technician_only_sees_own_services(self):
        self.create()
        self.create(nombre='Pintura de paredes', especialidad_id=self.pintura, claims=self.other_tech)
        self.assertEqual(self.names(my_services(self.tech, self.db)), [NAME])
        self.assertEqual(self.names(my_services(self.other_tech, self.db)), ['Pintura de paredes'])

    def test_technician_cannot_edit_or_deactivate_other_technician_service(self):
        created = self.create()
        self.assertEqual(self.status_of(lambda: self.update(created['id'], claims=self.other_tech, nombre='Hackeado')), 404)
        self.assertEqual(self.status_of(lambda: self.update(created['id'], claims=self.other_tech, activo=False)), 404)
        service = my_services(self.tech, self.db)[0]
        self.assertEqual(service['nombre'], NAME)
        self.assertTrue(service['activo'])

    def test_client_cannot_edit_services(self):
        created = self.create()
        self.assertEqual(self.status_of(lambda: self.update(created['id'], claims=self.client, activo=False)), 403)

    # ---- CA-04: perfil publico con servicios activos ----

    def test_public_profile_shows_only_active_services(self):
        active = self.create()
        inactive = self.create(nombre='Cambio de interruptores')
        self.update(inactive['id'], activo=False)
        result = public_services(self.user_id(self.tech), self.client, self.db)
        self.assertEqual(self.names(result), [NAME])
        self.assertEqual(result[0]['id'], active['id'])

    def test_public_profile_without_services_returns_empty_list(self):
        self.assertEqual(public_services(self.user_id(self.tech), self.client, self.db), [])

    def test_public_profile_with_only_inactive_services_returns_empty_list(self):
        created = self.create()
        self.update(created['id'], activo=False)
        self.assertEqual(public_services(self.user_id(self.tech), self.client, self.db), [])

    def test_public_profile_does_not_mix_technicians(self):
        self.create()
        self.create(nombre='Pintura de paredes', especialidad_id=self.pintura, claims=self.other_tech)
        result = public_services(self.user_id(self.other_tech), self.client, self.db)
        self.assertEqual(self.names(result), ['Pintura de paredes'])

    def test_public_profile_hides_private_data(self):
        self.create()
        result = public_services(self.user_id(self.tech), self.client, self.db)
        self.assertEqual(set(result[0]), {'id', 'nombre', 'descripcion', 'especialidad'})

    def test_public_profile_of_unknown_technician_is_not_found(self):
        self.assertEqual(self.status_of(lambda: public_services(uuid.uuid4(), self.client, self.db)), 404)

    def test_public_profile_of_a_client_is_not_found(self):
        self.assertEqual(self.status_of(lambda: public_services(self.user_id(self.client), self.client, self.db)), 404)


if __name__ == '__main__':
    unittest.main()