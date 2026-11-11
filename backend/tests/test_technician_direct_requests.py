from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app as fastapi_app
from app.models import DirectRequest, Service, Specialty, TechnicianProfile, User, UserRole
from app.technician_direct_requests import (
    accept_my_direct_request_endpoint,
    get_my_direct_requests_endpoint,
    reject_my_direct_request_endpoint,
)


class TechnicianDirectRequestsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client_user = self._add_user("client-1", "Joseline Banegas", "CLIENTE", "1")
        self.tech_owner = self._add_user("tech-1", "Kristhian Benitez", "TECNICO", "2")
        self.tech_other = self._add_user("tech-2", "Otro Técnico", "TECNICO", "3")

        self.db.add(TechnicianProfile(usuario_id=self.tech_owner.id, disponible=True, estado_verificacion="VERIFICADO"))
        self.db.add(TechnicianProfile(usuario_id=self.tech_other.id, disponible=True, estado_verificacion="VERIFICADO"))

        self.specialty = Specialty(nombre="Electricidad")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech_owner.id,
            especialidad_id=self.specialty.id,
            nombre="Electricista (Cambio de tomacorrientes)",
            descripcion="Cambio seguro de tomacorrientes residenciales",
            tarifa_base=Decimal("200.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080120000000{suffix}",
            telefono=f"+5049000000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _add_direct_request(
        self,
        *,
        tecnico_uid: str,
        estado: str = "Pendiente",
        monto_base: str = "400.00",
        offset_minutes: int = 0,
    ) -> DirectRequest:
        now = datetime.now(timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=tecnico_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle principal #10",
            fecha_hora=now + timedelta(days=2),
            descripcion="Necesito cambiar 4 tomacorrientes.",
            cantidad=Decimal("2.00"),
            tarifa_aplicada=Decimal("200.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal(monto_base),
            metodo_pago="efectivo",
            estado=estado,
            creada_en=now + timedelta(minutes=offset_minutes),
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_routes_registered_on_fastapi_app(self):
        paths = fastapi_app.openapi()["paths"]
        self.assertIn("get", paths["/technicians/me/direct-requests"])
        self.assertIn("patch", paths["/technicians/me/direct-requests/{solicitud_id}/accept"])
        self.assertIn("patch", paths["/technicians/me/direct-requests/{solicitud_id}/reject"])

    def test_get_my_direct_requests_returns_only_technician_requests_with_expected_fields(self):
        req_older = self._add_direct_request(
            tecnico_uid=self.tech_owner.firebase_uid,
            estado="Pendiente",
            monto_base="400.00",
            offset_minutes=1,
        )
        req_newer = self._add_direct_request(
            tecnico_uid=self.tech_owner.firebase_uid,
            estado="Aceptada",
            monto_base="800.00",
            offset_minutes=2,
        )
        self._add_direct_request(
            tecnico_uid=self.tech_other.firebase_uid,
            estado="Pendiente",
            monto_base="999.00",
            offset_minutes=3,
        )

        response = Response()
        payload = get_my_direct_requests_endpoint(
            response=response,
            claims={"uid": self.tech_owner.firebase_uid},
            db=self.db,
        )
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")
        self.assertEqual(len(payload), 2)
        self.assertEqual([item["id"] for item in payload], [req_newer.id, req_older.id])
        self.assertEqual(
            set(payload[0].keys()),
            {"id", "cliente_nombre", "servicio_nombre", "fecha_hora", "descripcion", "estado", "total_estimado"},
        )
        self.assertEqual(payload[0]["cliente_nombre"], "Joseline Banegas")
        self.assertEqual(payload[0]["servicio_nombre"], "Electricista (Cambio de tomacorrientes)")
        self.assertEqual(payload[0]["estado"], "ACEPTADA")
        self.assertEqual(payload[0]["total_estimado"], 800.0)
        self.assertEqual(payload[1]["estado"], "PENDIENTE")
        self.assertEqual(payload[1]["total_estimado"], 400.0)

    def test_accept_and_reject_update_status_and_block_non_pending_repeats(self):
        req_1 = self._add_direct_request(tecnico_uid=self.tech_owner.firebase_uid, estado="Pendiente")
        req_2 = self._add_direct_request(tecnico_uid=self.tech_owner.firebase_uid, estado="Pendiente")
        owner_claims = {"uid": self.tech_owner.firebase_uid}

        accepted = accept_my_direct_request_endpoint(
            solicitud_id=req_1.id,
            response=Response(),
            claims=owner_claims,
            db=self.db,
        )
        self.assertEqual(accepted["estado"], "ACEPTADA")

        rejected = reject_my_direct_request_endpoint(
            solicitud_id=req_2.id,
            response=Response(),
            claims=owner_claims,
            db=self.db,
        )
        self.assertEqual(rejected["estado"], "RECHAZADA")

        with self.assertRaises(HTTPException) as repeat_err:
            reject_my_direct_request_endpoint(
                solicitud_id=req_1.id,
                response=Response(),
                claims=owner_claims,
                db=self.db,
            )
        self.assertEqual(repeat_err.exception.status_code, 409)

    def test_ownership_and_role_validation(self):
        req_owner = self._add_direct_request(tecnico_uid=self.tech_owner.firebase_uid, estado="Pendiente")

        # Another technician cannot accept or reject owner's request
        other_claims = {"uid": self.tech_other.firebase_uid}
        with self.assertRaises(HTTPException) as forbidden_accept:
            accept_my_direct_request_endpoint(
                solicitud_id=req_owner.id,
                response=Response(),
                claims=other_claims,
                db=self.db,
            )
        self.assertEqual(forbidden_accept.exception.status_code, 403)

        with self.assertRaises(HTTPException) as forbidden_reject:
            reject_my_direct_request_endpoint(
                solicitud_id=req_owner.id,
                response=Response(),
                claims=other_claims,
                db=self.db,
            )
        self.assertEqual(forbidden_reject.exception.status_code, 403)

        # Client without TECNICO role cannot list or mutate direct requests
        client_claims = {"uid": self.client_user.firebase_uid}
        with self.assertRaises(HTTPException) as client_list:
            get_my_direct_requests_endpoint(response=Response(), claims=client_claims, db=self.db)
        self.assertEqual(client_list.exception.status_code, 403)

        with self.assertRaises(HTTPException) as client_accept:
            accept_my_direct_request_endpoint(
                solicitud_id=req_owner.id,
                response=Response(),
                claims=client_claims,
                db=self.db,
            )
        self.assertEqual(client_accept.exception.status_code, 403)

        # Missing request ID returns 404
        with self.assertRaises(HTTPException) as not_found:
            accept_my_direct_request_endpoint(
                solicitud_id=str(uuid.uuid4()),
                response=Response(),
                claims={"uid": self.tech_owner.firebase_uid},
                db=self.db,
            )
        self.assertEqual(not_found.exception.status_code, 404)

        # Original request remains Pendiente
        self.db.refresh(req_owner)
        self.assertEqual(req_owner.estado, "Pendiente")
