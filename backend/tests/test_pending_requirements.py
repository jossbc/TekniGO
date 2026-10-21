import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User, UserRole, TechnicianProfile
from app.permissions import require_verified_technician
from app.security import get_current_user
from app.users import Registration, register


class PendingRequirementsTests(unittest.TestCase):
    def test_concurrent_duplicate_registrations(self):
        # Independent connections hit the same database at the same time.
        for duplicate in ('dni', 'telefono'):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
                engine = create_engine('sqlite:///' + str(Path(folder) / 'race.db'),
                                       connect_args={'timeout': 10})
                Base.metadata.create_all(engine)
                barrier = Barrier(2)

                def attempt(index):
                    data = Registration(nombre_completo='Registro Test', rol='CLIENTE',
                        dni='0801200012345' if duplicate == 'dni' else f'080120001234{index}',
                        telefono='99999999' if duplicate == 'telefono' else f'8888888{index}')
                    with Session(engine) as db:
                        barrier.wait(timeout=5)
                        try:
                            register(data, {'uid': f'user-{index}', 'email': f'{index}@example.com'}, db)
                            return 201
                        except HTTPException as error:
                            return error.status_code
                try:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(attempt, (1, 2)))
                    self.assertEqual(sorted(results), [201, 409])
                    with Session(engine) as db:
                        self.assertEqual(db.scalar(select(func.count()).select_from(User)), 1)
                        self.assertEqual(db.scalar(select(func.count()).select_from(UserRole)), 1)
                finally:
                    engine.dispose()

    def test_technician_requires_both_identity_verifications(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                register(Registration(nombre_completo='Tecnico Test', dni='0801200012345',
                    telefono='99999999', rol='TECNICO'), {'uid': 'tech', 'email': 't@example.com'}, db)
                user = db.scalar(select(User))
                technician = db.get(TechnicianProfile, user.id)
                for identity, professional in [('PENDIENTE', 'NO_VERIFICADO'),
                                                ('VERIFICADO', 'NO_VERIFICADO'),
                                                ('PENDIENTE', 'VERIFICADO')]:
                    user.estado_identidad = identity
                    technician.estado_verificacion = professional
                    with self.assertRaises(HTTPException) as error:
                        require_verified_technician(user, db)
                    self.assertEqual(error.exception.status_code, 403)
                user.estado_identidad = technician.estado_verificacion = 'VERIFICADO'
                self.assertIs(require_verified_technician(user, db), technician)
                user.roles.clear()
                with self.assertRaises(HTTPException):
                    require_verified_technician(user, db)
        finally:
            engine.dispose()

    @patch('app.security.auth.verify_id_token')
    def test_revoked_token_is_rejected(self, verify):
        from firebase_admin import auth
        verify.side_effect = auth.RevokedIdTokenError('revoked')
        with self.assertRaises(HTTPException) as error:
            get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials='old-token'))
        self.assertEqual(error.exception.status_code, 401)
        verify.assert_called_once_with('old-token', check_revoked=True)
