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
from app.service_reception import (
    AdminResolveIncidentSchema,
    ConfirmReceptionSchema,
    OpenServiceIncidentSchema,
    ReportCompletionSchema,
    _COMPLETION_EVENTS_BY_REQUEST,
    _FINANCIAL_EFFECTS_BY_REQUEST,
    _RECEPTION_STATE_BY_REQUEST,
    confirm_service_reception_by_client,
    is_review_window_open,
    open_service_incident_by_client,
    report_service_completion_by_technician,
    resolve_service_incident_by_admin,
    run_scheduled_auto_completion,
    validate_sprint3_financial_settlement,
)

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
        self._original_open_request = models.OpenRequest

    def tearDown(self):
        models.OpenRequest = self._original_open_request
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


class ServiceReceptionHU35Tests(unittest.TestCase):
    _add_user = ClientRequestsPaginationTests._add_user
    _add_direct_request = ClientRequestsPaginationTests._add_direct_request

    def setUp(self):
        ClientRequestsPaginationTests.setUp(self)
        _RECEPTION_STATE_BY_REQUEST.clear()
        _COMPLETION_EVENTS_BY_REQUEST.clear()
        _FINANCIAL_EFFECTS_BY_REQUEST.clear()
        self.admin_user = self._add_user("admin-1", "Admin TekniGO", "ADMIN", "9")
        self.db.commit()

    def tearDown(self):
        _RECEPTION_STATE_BY_REQUEST.clear()
        _COMPLETION_EVENTS_BY_REQUEST.clear()
        _FINANCIAL_EFFECTS_BY_REQUEST.clear()
        ClientRequestsPaginationTests.tearDown(self)
    def test_hu35_ca01_ca02_permisos_confirmacion_inmutabilidad_y_plazo_resena_48h(self):
        """CA-01, CA-02 y plazos independientes: solo el propietario confirma En proceso con finalización reportada."""
        req = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="1000.00",
        )
        t0 = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
        # Sin reporte de finalización aún no se puede confirmar
        with self.assertRaises(HTTPException) as err_not_in_progress:
            confirm_service_reception_by_client(
                req.id,
                {"uid": self.client_a.firebase_uid},
                self.db,
                now=t0,
            )
        self.assertEqual(err_not_in_progress.exception.status_code, 409)
        # El técnico reporta la finalización -> pasa a 'En proceso' e inicia plazo de 48h
        report_res = report_service_completion_by_technician(
            req.id,
            {"uid": self.tech_only.firebase_uid},
            self.db,
            payload=ReportCompletionSchema(notas_finalizacion="Instalación completada y probada"),
            now=t0,
        )
        self.assertEqual(report_res["estado"], "EN PROCESO")
        self.assertTrue(report_res["seguimiento"]["finalizacion_reportada"])
        self.assertTrue(
            report_res["seguimiento"]["finalizacion_reportada_tegucigalpa"].endswith("-06:00")
        )
        # CA-01: Otro cliente no propietario recibe 403 Forbidden
        with self.assertRaises(HTTPException) as err_foreign:
            confirm_service_reception_by_client(
                req.id,
                {"uid": self.client_b.firebase_uid},
                self.db,
                now=t0 + timedelta(hours=2),
            )
        self.assertEqual(err_foreign.exception.status_code, 403)
        # CA-01 y CA-02: El cliente propietario confirma a las 10 horas del reporte
        t_confirm = t0 + timedelta(hours=10)
        confirm_res = confirm_service_reception_by_client(
            req.id,
            {"uid": self.client_a.firebase_uid},
            self.db,
            payload=ConfirmReceptionSchema(comentario="Excelente trabajo"),
            now=t_confirm,
        )
        self.assertEqual(confirm_res["estado"], "COMPLETADA")
        self.assertEqual(confirm_res["autor_cierre"], "CLIENTE")
        self.assertTrue(confirm_res["acuerdo_inmutable"])
        self.assertTrue(confirm_res["fecha_cierre_tegucigalpa"].endswith("-06:00"))
        # Plazo independiente de 48h para reseñas empieza en t_confirm
        self.assertTrue(is_review_window_open(req.id, now=t_confirm + timedelta(hours=47, minutes=59)))
        self.assertFalse(is_review_window_open(req.id, now=t_confirm + timedelta(hours=48, seconds=1)))
        # CA-02: Acuerdo y estados inmutables tras completar
        with self.assertRaises(HTTPException) as err_immutable:
            apply_client_request_action(
                req.id,
                "CANCELAR",
                {"uid": self.client_a.firebase_uid},
                self.db,
            )
        self.assertEqual(err_immutable.exception.status_code, 409)
    def test_hu35_ca03_proceso_automatico_48h_con_reloj_controlado_y_autor_sistema(self):
        """CA-03: Si transcurren 48h sin confirmación ni incidencia, el proceso automático completa una sola vez con autor SISTEMA."""
        req = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="800.00",
        )
        t0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        report_service_completion_by_technician(
            req.id,
            {"uid": self.tech_only.firebase_uid},
            self.db,
            now=t0,
        )
        # A las 47h 59m aún NO vence el plazo de confirmación
        before_48h = run_scheduled_auto_completion(
            self.db,
            now=t0 + timedelta(hours=47, minutes=59),
        )
        self.assertEqual(before_48h["total_completadas_automaticamente"], 0)
        # Exactamente a las 48h se completa automáticamente con autor SISTEMA
        at_48h = run_scheduled_auto_completion(
            self.db,
            now=t0 + timedelta(hours=48),
        )
        self.assertEqual(at_48h["total_completadas_automaticamente"], 1)
        self.assertIn(req.id, at_48h["solicitudes_completadas"])
        detail = get_client_request_detail(req.id, {"uid": self.client_a.firebase_uid}, self.db)
        self.assertEqual(detail["estado"], "COMPLETADA")
        self.assertEqual(detail["seguimiento_recepcion"]["autor_cierre"], "SISTEMA")
        self.assertTrue(detail["seguimiento_recepcion"]["acuerdo_inmutable"])
        # Segunda ejecución del proceso programado no vuelve a completarla
        second_run = run_scheduled_auto_completion(
            self.db,
            now=t0 + timedelta(hours=50),
        )
        self.assertEqual(second_run["total_completadas_automaticamente"], 0)
        self.assertIn(req.id, second_run["solicitudes_ya_completadas"])
    def test_hu35_ca04_incidencia_previa_detiene_cierre_automatico_hasta_resolucion_admin(self):
        """CA-04: Si el cliente abre incidencia dentro de las 48h, se detiene el cierre automático y la liberación de fondos."""
        req = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="1200.00",
        )
        t0 = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
        report_service_completion_by_technician(
            req.id,
            {"uid": self.tech_only.firebase_uid},
            self.db,
            now=t0,
        )
        # El cliente abre una incidencia a las 12h (dentro del plazo de 48h)
        inc_res = open_service_incident_by_client(
            req.id,
            OpenServiceIncidentSchema(motivo="El tomacorriente instalado presenta falso contacto"),
            {"uid": self.client_a.firebase_uid},
            self.db,
            now=t0 + timedelta(hours=12),
        )
        self.assertTrue(inc_res["seguimiento"]["incidencia_abierta"])
        self.assertTrue(inc_res["seguimiento"]["cierre_automatico_detenido"])
        self.assertTrue(inc_res["seguimiento"]["liberacion_fondos_retenida"])
        # Mientras la incidencia esté abierta, ni el cliente puede confirmar ni el job de 48h puede cerrarla
        with self.assertRaises(HTTPException) as err_blocked_confirm:
            confirm_service_reception_by_client(
                req.id,
                {"uid": self.client_a.firebase_uid},
                self.db,
                now=t0 + timedelta(hours=20),
            )
        self.assertEqual(err_blocked_confirm.exception.status_code, 409)
        auto_after_72h = run_scheduled_auto_completion(
            self.db,
            now=t0 + timedelta(hours=72),
        )
        self.assertEqual(auto_after_72h["total_completadas_automaticamente"], 0)
        self.assertIn(req.id, auto_after_72h["solicitudes_bloqueadas_por_incidencia"])
        self.assertNotIn(req.id, _COMPLETION_EVENTS_BY_REQUEST)
        # Resolución administrativa COMPLETAR_SERVICIO libera el cierre y emite el evento único
        admin_res = resolve_service_incident_by_admin(
            req.id,
            AdminResolveIncidentSchema(
                decision="COMPLETAR_SERVICIO",
                resolucion="Se verificó la corrección técnica en sitio y procede el cierre.",
            ),
            {"uid": self.admin_user.firebase_uid},
            self.db,
            now=t0 + timedelta(hours=80),
        )
        self.assertEqual(admin_res["estado"], "COMPLETADA")
        self.assertEqual(admin_res["seguimiento"]["autor_cierre"], "ADMIN")
        self.assertFalse(admin_res["seguimiento"]["liberacion_fondos_retenida"])
        self.assertEqual(admin_res["evento_cierre"]["veces_emitido"], 1)
    def test_hu35_ca05_ca06_ca07_evento_unico_efectivo_sprint2_sprint3_y_concurrencia(self):
        """CA-05, CA-06 y CA-07: Evento único con efectivo/tarjeta, validación Sprint 3 sin duplicar y concurrencia."""
        # 1. Solicitud en efectivo (Sprint 2 registra deuda por comisión y Sprint 3 consolida sin duplicar)
        req_cash = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="1000.00",
        )
        req_cash.metodo_pago = "efectivo"
        self.db.commit()
        t0 = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        report_service_completion_by_technician(
            req_cash.id,
            {"uid": self.tech_only.firebase_uid},
            self.db,
            now=t0,
        )
        # El proceso automático cierra a las 48h y en el mismo instante el cliente envía confirmación manual (CA-07)
        t_48h = t0 + timedelta(hours=48)
        auto_res = run_scheduled_auto_completion(self.db, now=t_48h)
        self.assertEqual(auto_res["total_completadas_automaticamente"], 1)
        concurrent_client_res = confirm_service_reception_by_client(
            req_cash.id,
            {"uid": self.client_a.firebase_uid},
            self.db,
            now=t_48h,
        )
        self.assertTrue(concurrent_client_res["reintento_idempotente"])
        self.assertEqual(concurrent_client_res["autor_cierre"], "SISTEMA")
        self.assertTrue(concurrent_client_res["evento_cierre"]["reutilizado_idempotente"])
        self.assertEqual(concurrent_client_res["evento_cierre"]["veces_emitido"], 1)
        self.assertEqual(
            concurrent_client_res["efecto_financiero"]["tipo_efecto"],
            "DEUDA_COMISION_EFECTIVO_REGISTRADA",
        )
        self.assertEqual(concurrent_client_res["efecto_financiero"]["monto_comision"], 100.0)
        self.assertEqual(concurrent_client_res["efecto_financiero"]["deuda_comision_efectivo"], 100.0)
        self.assertEqual(concurrent_client_res["efecto_financiero"]["credito_digital_acreditado"], 0.0)
        self.assertEqual(concurrent_client_res["efecto_financiero"]["aplicaciones_financieras"], 1)
        # CA-06: En Sprint 3 se valida el efecto financiero completo sin duplicar cierres anteriores
        sprint3_res = validate_sprint3_financial_settlement(
            req_cash.id,
            now=t_48h + timedelta(days=5),
        )
        self.assertFalse(sprint3_res["duplicado_generado"])
        self.assertTrue(sprint3_res["efecto_financiero"]["validado_sprint_3"])
        self.assertEqual(sprint3_res["efecto_financiero"]["estado_liquidacion"], "CONSOLIDADO_SPRINT_3")
        self.assertEqual(sprint3_res["efecto_financiero"]["aplicaciones_financieras"], 1)
        # 2. Solicitud con tarjeta (acredita fondos digitales netos al técnico una sola vez)
        req_card = self._add_direct_request(
            client_uid=self.client_a.firebase_uid,
            estado="Aceptada",
            monto_base="500.00",
        )
        req_card.metodo_pago = "tarjeta"
        self.db.commit()
        report_service_completion_by_technician(
            req_card.id,
            {"uid": self.tech_only.firebase_uid},
            self.db,
            now=t0,
        )
        card_close = confirm_service_reception_by_client(
            req_card.id,
            {"uid": self.client_a.firebase_uid},
            self.db,
            now=t0 + timedelta(hours=4),
        )
        self.assertEqual(
            card_close["efecto_financiero"]["tipo_efecto"],
            "FONDOS_DIGITALES_ACREDITADOS",
        )
        self.assertEqual(card_close["efecto_financiero"]["monto_comision"], 50.0)
        self.assertEqual(card_close["efecto_financiero"]["credito_digital_acreditado"], 450.0)
        self.assertEqual(card_close["efecto_financiero"]["deuda_comision_efectivo"], 0.0)