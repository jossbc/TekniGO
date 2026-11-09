from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models
from app.client_requests import (
    ClientRequestActionSchema,
    apply_client_request_action,
    execute_my_request_action_endpoint,
    get_client_request_detail,
    get_my_request_detail_endpoint,
    list_client_requests,
    list_my_requests_endpoint,
    router,
)
from app.database import Base
from app.models import DirectRequest, Service, Specialty, TechnicianProfile, User, UserRole


class ClientRequestsPaginationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client_a = self._add_user("client-a", "Ana Cliente", "CLIENTE", "1")
        self.client_b = self._add_user("client-b", "Beto Cliente", "CLIENTE", "2")
        self.tech_only = self._add_user("tech-1", "Carlos Técnico", "TECNICO", "3")
        self.profile = TechnicianProfile(usuario_id=self.tech_only.id, disponible=True, estado_verificacion="VERIFICADO")
        self.db.add(self.profile)

        self.specialty = Specialty(nombre="Electricidad")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech_only.id,
            especialidad_id=self.specialty.id,
            nombre="Instalación de tomacorrientes",
            descripcion="Instalación residencial segura",
            tarifa_base=Decimal("350.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        if hasattr(models, "OpenRequest"):
            delattr(models, "OpenRequest")
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
        client_uid: str,
        estado: str = "Pendiente",
        cantidad: str = "2.00",
        tarifa: str = "350.00",
        monto_base: str = "700.00",
        created_offset_minutes: int = 0,
    ) -> DirectRequest:
        now = datetime.now(timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=client_uid,
            tecnico_id=self.tech_only.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle principal, casa 12",
            fecha_hora=now + timedelta(days=2),
            descripcion="Revisión e instalación eléctrica",
            cantidad=Decimal(cantidad),
            tarifa_aplicada=Decimal(tarifa),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal(monto_base),
            metodo_pago="efectivo",
            estado=estado,
            creada_en=now + timedelta(minutes=created_offset_minutes),
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_ca01_and_ca02_client_queries_only_own_requests_with_modality_date_budget_and_state(self):
        req_a1 = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Pendiente",
            monto_base="700.00",
            created_offset_minutes=1,
        )
        req_a2 = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="1050.00",
            created_offset_minutes=5,
        )
        self._add_direct_request(
            client_uid=self.client_b.firebase_uid,
            estado="Pendiente",
            monto_base="350.00",
            created_offset_minutes=10,
        )

        response = Response()
        result = list_my_requests_endpoint(
            response=response,
            modalidad="TODOS",
            estado="TODOS",
            offset=0,
            limit=20,
            claims={"uid": self.client_a.firebase_uid},
            db=self.db,
        )
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(result["total"], 2)
        self.assertEqual([item["id"] for item in result["solicitudes"]], [req_a2.id, req_a1.id])

        first = result["solicitudes"][0]
        self.assertEqual(first["modalidad"], "DIRECTA")
        self.assertEqual(first["estado"], "ACEPTADA")
        self.assertEqual(first["presupuesto"], 1050.0)
        self.assertIsNotNone(first["fecha_hora"])
        self.assertEqual(first["servicio"]["nombre"], "Instalación de tomacorrientes")
        self.assertEqual(first["tecnico"]["nombre_completo"], "Carlos Técnico")

    def test_st01_filters_by_estado_and_modalidad_and_paginates(self):
        self._add_direct_request(client_uid=self.client_a.firebase_uid, estado="Pendiente", created_offset_minutes=1)
        r2 = self._add_direct_request(client_uid=self.client_a.firebase_uid, estado="Completada", created_offset_minutes=2)
        r3 = self._add_direct_request(client_uid=self.client_a.firebase_uid, estado="Cancelada", created_offset_minutes=3)
        r4 = self._add_direct_request(client_uid=self.client_a.firebase_uid, estado="Completada", created_offset_minutes=4)

        # Filtro por estado COMPLETADA
        completed = list_client_requests(
            {"uid": self.client_a.firebase_uid},
            self.db,
            modalidad="DIRECTA",
            estado="completada",
            offset=0,
            limit=10,
        )
        self.assertEqual(completed["total"], 2)
        self.assertEqual([item["id"] for item in completed["solicitudes"]], [r4.id, r2.id])

        # Paginación offset/limit sobre todas las solicitudes de client_a
        page_1 = list_client_requests(
            {"uid": self.client_a.firebase_uid},
            self.db,
            modalidad="TODOS",
            estado="TODOS",
            offset=0,
            limit=2,
        )
        page_2 = list_client_requests(
            {"uid": self.client_a.firebase_uid},
            self.db,
            modalidad="TODOS",
            estado="TODOS",
            offset=2,
            limit=2,
        )
        self.assertEqual(page_1["total"], 4)
        self.assertEqual(len(page_1["solicitudes"]), 2)
        self.assertEqual(page_1["solicitudes"][0]["id"], r4.id)
        self.assertEqual(page_1["solicitudes"][1]["id"], r3.id)
        self.assertEqual(len(page_2["solicitudes"]), 2)
        self.assertEqual(page_2["solicitudes"][0]["id"], r2.id)

        # Modalidad ABIERTA sin modelo aún devuelve lista vacía sin error
        open_only = list_client_requests(
            {"uid": self.client_a.firebase_uid},
            self.db,
            modalidad="ABIERTA",
            estado="TODOS",
            offset=0,
            limit=10,
        )
        self.assertEqual(open_only["total"], 0)
        self.assertEqual(open_only["solicitudes"], [])

    def test_st01_combines_direct_and_open_requests_when_open_model_is_available(self):
        class SimulatedOpenRequest(Base):
            __tablename__ = "solicitudes_abiertas_sim"
            id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
            cliente_id = Column(String, ForeignKey("usuarios.firebase_uid"), nullable=False)
            tecnico_id = Column(String, ForeignKey("usuarios.firebase_uid"), nullable=True)
            servicio_id = Column(String, ForeignKey("servicios.id"), nullable=True)
            municipio = Column(String, nullable=False)
            sector = Column(String, nullable=False)
            direccion_exacta = Column(String, nullable=False)
            fecha_hora = Column(DateTime(timezone=True), nullable=False)
            descripcion = Column(Text, nullable=False)
            presupuesto = Column(Numeric(10, 2), nullable=False)
            estado = Column(String, nullable=False, default="Pendiente")
            creada_en = Column(DateTime(timezone=True), nullable=False)

        SimulatedOpenRequest.__table__.create(self.engine, checkfirst=True)
        models.OpenRequest = SimulatedOpenRequest

        direct_req = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Pendiente",
            monto_base="500.00",
            created_offset_minutes=1,
        )
        now = datetime.now(timezone.utc)
        open_req = SimulatedOpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_a.firebase_uid,
            tecnico_id=None,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Tepeyac",
            direccion_exacta="Bloque B, casa 4",
            fecha_hora=now + timedelta(days=3),
            descripcion="Cotización abierta de revisión general",
            presupuesto=Decimal("1200.00"),
            estado="Pendiente",
            creada_en=now + timedelta(minutes=5),
        )
        self.db.add(open_req)
        self.db.commit()

        both = list_client_requests({"uid": self.client_a.firebase_uid}, self.db, modalidad="TODOS")
        self.assertEqual(both["total"], 2)
        self.assertEqual([item["id"] for item in both["solicitudes"]], [open_req.id, direct_req.id])
        self.assertEqual([item["modalidad"] for item in both["solicitudes"]], ["ABIERTA", "DIRECTA"])

        only_open = list_client_requests({"uid": self.client_a.firebase_uid}, self.db, modalidad="ABIERTA")
        self.assertEqual(only_open["total"], 1)
        self.assertEqual(only_open["solicitudes"][0]["id"], open_req.id)
        self.assertEqual(only_open["solicitudes"][0]["modalidad"], "ABIERTA")
        self.assertEqual(only_open["solicitudes"][0]["presupuesto"], 1200.0)
        self.assertIsNone(only_open["solicitudes"][0]["tecnico"])

    def test_ca05_empty_list_returns_without_error_and_reflects_persisted_updates(self):
        empty = list_client_requests({"uid": self.client_a.firebase_uid}, self.db)
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["solicitudes"], [])

        req = self._add_direct_request(client_uid=self.client_a.firebase_uid, estado="Pendiente")
        after_create = list_client_requests({"uid": self.client_a.firebase_uid}, self.db)
        self.assertEqual(after_create["total"], 1)
        self.assertEqual(after_create["solicitudes"][0]["estado"], "PENDIENTE")

        req.estado = "Cancelada"
        self.db.commit()

        after_update = list_client_requests({"uid": self.client_a.firebase_uid}, self.db)
        self.assertEqual(after_update["solicitudes"][0]["estado"], "CANCELADA")

    def test_permissions_and_invalid_filter_parameters(self):
        app = FastAPI()
        app.include_router(router)
        self.assertIn("/users/me/requests", app.openapi()["paths"])

        # Técnico sin rol CLIENTE no puede usar la consulta de contrataciones de cliente
        with self.assertRaises(HTTPException) as forbidden:
            list_client_requests({"uid": self.tech_only.firebase_uid}, self.db)
        self.assertEqual(forbidden.exception.status_code, 403)

        # Modalidad o estado inválidos retornan 400
        with self.assertRaises(HTTPException) as bad_mod:
            list_client_requests({"uid": self.client_a.firebase_uid}, self.db, modalidad="URGENTE")
        self.assertEqual(bad_mod.exception.status_code, 400)

        with self.assertRaises(HTTPException) as bad_state:
            list_client_requests({"uid": self.client_a.firebase_uid}, self.db, estado="DESCONOCIDO")
        self.assertEqual(bad_state.exception.status_code, 400)

    def test_st02_detail_endpoint_enforces_owner_and_returns_proposal_timeline_and_allowed_actions(self):
        req_pending = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Pendiente",
            cantidad="2.00",
            tarifa="350.00",
            monto_base="700.00",
        )
        req_completed = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Completada",
            cantidad="1.00",
            tarifa="350.00",
            monto_base="350.00",
        )
        req_cancelled = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Cancelada",
            cantidad="1.00",
            tarifa="350.00",
            monto_base="350.00",
        )

        # CA-02: Un cliente distinto (client_b) no puede acceder por URL/API al registro de client_a
        with self.assertRaises(HTTPException) as other_client_err:
            get_client_request_detail(req_pending.id, {"uid": self.client_b.firebase_uid}, self.db)
        self.assertEqual(other_client_err.exception.status_code, 403)

        # ID inexistente devuelve 404
        with self.assertRaises(HTTPException) as not_found_err:
            get_client_request_detail(str(uuid.uuid4()), {"uid": self.client_a.firebase_uid}, self.db)
        self.assertEqual(not_found_err.exception.status_code, 404)

        # CA-03 y CA-04: Propietario consulta el detalle con técnico, propuesta, línea de tiempo y acciones permitidas
        response = Response()
        detail_pending = get_my_request_detail_endpoint(
            solicitud_id=req_pending.id,
            response=response,
            claims={"uid": self.client_a.firebase_uid},
            db=self.db,
        )
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(detail_pending["id"], req_pending.id)
        self.assertEqual(detail_pending["tecnico"]["nombre_completo"], "Carlos Técnico")
        self.assertEqual(detail_pending["tecnico"]["estado_verificacion"], "VERIFICADO")
        self.assertEqual(detail_pending["propuesta"]["monto_pactado"], 700.0)
        self.assertEqual(detail_pending["propuesta"]["tarifa_aplicada"], 350.0)
        self.assertEqual(detail_pending["propuesta"]["cantidad"], 2.0)
        self.assertEqual(len(detail_pending["linea_tiempo"]), 1)
        self.assertEqual(detail_pending["linea_tiempo"][0]["estado"], "PENDIENTE")
        self.assertEqual(detail_pending["acciones_permitidas"], ["CANCELAR"])

        # Solicitud completada muestra línea de tiempo con transição y sin acciones de edición/cancelación
        detail_completed = get_client_request_detail(
            req_completed.id,
            {"uid": self.client_a.firebase_uid},
            self.db,
        )
        self.assertEqual(
            [ev["estado"] for ev in detail_completed["linea_tiempo"]],
            ["PENDIENTE", "COMPLETADA"],
        )
        self.assertEqual(detail_completed["acciones_permitidas"], [])

        # Solicitud cancelada tampoco permite acciones adicionales
        detail_cancelled = get_client_request_detail(
            req_cancelled.id,
            {"uid": self.client_a.firebase_uid},
            self.db,
        )
        self.assertEqual(
            [ev["estado"] for ev in detail_cancelled["linea_tiempo"]],
            ["PENDIENTE", "CANCELADA"],
        )
        self.assertEqual(detail_cancelled["acciones_permitidas"], [])

    def test_st04_executes_allowed_actions_and_rejects_disallowed_or_foreign_actions(self):
        req_pending = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Pendiente",
        )
        req_accepted = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
        )

        # Otro cliente no puede ejecutar acciones sobre la solicitud de client_a
        with self.assertRaises(HTTPException) as foreign_err:
            apply_client_request_action(
                req_pending.id,
                "CANCELAR",
                {"uid": self.client_b.firebase_uid},
                self.db,
            )
        self.assertEqual(foreign_err.exception.status_code, 403)

        # Solicitud PENDIENTE no puede marcarse como COMPLETAR directamente
        with self.assertRaises(HTTPException) as invalid_transition:
            apply_client_request_action(
                req_pending.id,
                "COMPLETAR",
                {"uid": self.client_a.firebase_uid},
                self.db,
            )
        self.assertEqual(invalid_transition.exception.status_code, 409)

        # Solicitud PENDIENTE sí permite CANCELAR y refleja el cambio en detalle y listado
        response = Response()
        cancelled_detail = execute_my_request_action_endpoint(
            solicitud_id=req_pending.id,
            payload=ClientRequestActionSchema(accion="CANCELAR"),
            response=response,
            claims={"uid": self.client_a.firebase_uid},
            db=self.db,
        )
        self.assertEqual(cancelled_detail["estado"], "CANCELADA")
        self.assertEqual(cancelled_detail["acciones_permitidas"], [])
        self.assertEqual(
            [ev["estado"] for ev in cancelled_detail["linea_tiempo"]],
            ["PENDIENTE", "CANCELADA"],
        )

        # Intentar volver a cancelar una solicitud ya CANCELADA devuelve 409
        with self.assertRaises(HTTPException) as repeat_err:
            apply_client_request_action(
                req_pending.id,
                "CANCELAR",
                {"uid": self.client_a.firebase_uid},
                self.db,
            )
        self.assertEqual(repeat_err.exception.status_code, 409)

        # Solicitud ACEPTADA permite COMPLETAR
        completed_detail = apply_client_request_action(
            req_accepted.id,
            "COMPLETAR",
            {"uid": self.client_a.firebase_uid},
            self.db,
        )
        self.assertEqual(completed_detail["estado"], "COMPLETADA")
        self.assertEqual(completed_detail["acciones_permitidas"], [])

    def test_st05_foreign_requests_final_states_and_empty_results(self):
        # 1. Resultados vacíos: cliente sin solicitudes o con filtros sin coincidencias no produce error
        for mod in ("TODOS", "DIRECTA", "ABIERTA"):
            for est in ("TODOS", "PENDIENTE", "ACEPTADA", "RECHAZADA", "CANCELADA", "COMPLETADA"):
                empty_res = list_client_requests(
                    {"uid": self.client_a.firebase_uid},
                    self.db,
                    modalidad=mod,
                    estado=est,
                    offset=0,
                    limit=10,
                )
                self.assertEqual(empty_res["total"], 0)
                self.assertEqual(empty_res["solicitudes"], [])

        # 2. Estados finales (CANCELADA, RECHAZADA, COMPLETADA): sin acciones permitidas e inmutables
        for raw_final_state, expected_norm in (
            ("Cancelada", "CANCELADA"),
            ("Rechazada", "RECHAZADA"),
            ("Completada", "COMPLETADA"),
        ):
            req_final = self._add_direct_request(
                client_uid=self.client_a.firebase_uid,
                estado=raw_final_state,
            )
            detail = get_client_request_detail(
                req_final.id,
                {"uid": self.client_a.firebase_uid},
                self.db,
            )
            self.assertEqual(detail["estado"], expected_norm)
            self.assertEqual(detail["acciones_permitidas"], [])
            self.assertEqual(detail["linea_tiempo"][-1]["estado"], expected_norm)

            for attempted_action in ("CANCELAR", "COMPLETAR"):
                with self.assertRaises(HTTPException) as final_err:
                    apply_client_request_action(
                        req_final.id,
                        attempted_action,
                        {"uid": self.client_a.firebase_uid},
                        self.db,
                    )
                self.assertEqual(final_err.exception.status_code, 409)

            # Verificar que el estado sigue intacto tras el intento rechazado
            unchanged = get_client_request_detail(
                req_final.id,
                {"uid": self.client_a.firebase_uid},
                self.db,
            )
            self.assertEqual(unchanged["estado"], expected_norm)

            # 3. Solicitudes ajenas: client_b no las ve en su listado ni accede a detalle ni acciones
            list_b = list_client_requests({"uid": self.client_b.firebase_uid}, self.db)
            self.assertEqual(list_b["total"], 0)
            self.assertEqual(list_b["solicitudes"], [])

            with self.assertRaises(HTTPException) as detail_foreign:
                get_client_request_detail(req_final.id, {"uid": self.client_b.firebase_uid}, self.db)
            self.assertEqual(detail_foreign.exception.status_code, 403)

            with self.assertRaises(HTTPException) as action_foreign:
                apply_client_request_action(
                    req_final.id,
                    "CANCELAR",
                    {"uid": self.client_b.firebase_uid},
                    self.db,
                )
            self.assertEqual(action_foreign.exception.status_code, 403)
