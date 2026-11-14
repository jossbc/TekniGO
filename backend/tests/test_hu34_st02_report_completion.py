from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models
from app.client_requests import get_client_request_detail
from app.database import Base
from app.main import app as fastapi_app
from app.models import (
    DirectRequest,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.service_completion import (
    ReportServiceCompletionSchema,
    clear_completion_events,
    clear_pending_adjustments,
    get_completion_events,
    register_pending_adjustment,
    report_direct_request_completion_endpoint,
    report_open_request_completion_endpoint,
    report_service_completion,
    report_service_completion_endpoint,
    resolve_pending_adjustments,
)


class Hu34St02ReportCompletionEndpointTests(unittest.TestCase):
    def setUp(self):
        clear_completion_events()
        clear_pending_adjustments()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client = self._add_user("client-st02-1", "Lucía Cliente", "CLIENTE", "1")
        self.tech_selected = self._add_user("tech-st02-1", "Marco Técnico", "TECNICO", "2")
        self.tech_other = self._add_user("tech-st02-2", "Pedro Otro Técnico", "TECNICO", "3")

        self.db.add_all(
            [
                TechnicianProfile(
                    usuario_id=self.tech_selected.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
                TechnicianProfile(
                    usuario_id=self.tech_other.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
            ]
        )

        self.specialty = Specialty(nombre="Plomería")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech_selected.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación de cisterna",
            descripcion="Cambio de válvula y flotador",
            tarifa_base=Decimal("500.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        clear_completion_events()
        clear_pending_adjustments()
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080119990000{suffix}",
            telefono=f"+5049888000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _create_direct_request(self, *, estado: str = "EN_PROCESO", tecnico_uid: str | None = None) -> DirectRequest:
        now = datetime(2026, 11, 10, 14, 0, 0, tzinfo=timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=tecnico_uid or self.tech_selected.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle 3, casa #15",
            fecha_hora=now + timedelta(days=1),
            descripcion="Reparación urgente de cisterna",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("500.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal("500.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def _create_open_request(self, *, estado: str = "En proceso", tecnico_uid: str | None = None) -> OpenRequest:
        now = datetime(2026, 11, 10, 14, 0, 0, tzinfo=timezone.utc)
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=tecnico_uid if tecnico_uid is not None else self.tech_selected.firebase_uid,
            especialidad_id=str(self.specialty.id),
            servicio_id=str(self.service.id),
            modalidad="ABIERTA",
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle 3, casa #15",
            fecha_hora=now + timedelta(days=1),
            descripcion="Cambio de tubería exterior",
            presupuesto=Decimal("800.00"),
            monto_base=Decimal("800.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_routes_are_registered_on_fastapi_app(self):
        paths = fastapi_app.openapi()["paths"]
        self.assertIn("/technicians/me/requests/{solicitud_id}/report-completion", paths)
        self.assertIn("/technicians/me/direct-requests/{solicitud_id}/report-completion", paths)
        self.assertIn("/open-requests/{solicitud_id}/report-completion", paths)
        self.assertIn("/direct-requests/{solicitud_id}/report-completion", paths)

    def test_ca01_ca02_ca03_selected_technician_reports_completion_from_en_proceso_and_notifies_client(self):
        req = self._create_direct_request(estado="EN_PROCESO")
        clock_t0 = datetime(2026, 11, 12, 16, 15, 0, tzinfo=timezone.utc)
        response = Response()

        result = report_service_completion_endpoint(
            solicitud_id=req.id,
            response=response,
            payload=ReportServiceCompletionSchema(
                comentario="Válvula reemplazada y probada sin fugas.",
                now=clock_t0,
            ),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )

        self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")
        self.assertEqual(result["id"], req.id)
        self.assertEqual(result["modalidad"], "DIRECTA")
        # CA-02: Conserva En proceso con pendiente_recepcion, no marca Completada ni libera fondos
        self.assertEqual(result["estado"], "EN_PROCESO")
        self.assertTrue(result["pendiente_recepcion"])
        self.assertFalse(result["completada"])
        self.assertFalse(result["fondos_liberados"])
        self.assertEqual(result["estado_fondos"], "RETENIDOS_PENDIENTE_CONFIRMACION")
        self.assertFalse(result["reintento_idempotente"])

        # CA-03: Plazo máximo de 48 horas con reloj controlado y zona America/Tegucigalpa (-06:00)
        self.assertEqual(result["plazo_confirmacion_horas"], 48)
        self.assertEqual(result["finalizacion_reportada_en"], "2026-11-12T16:15:00+00:00")
        self.assertEqual(result["vencimiento_confirmacion_en"], "2026-11-14T16:15:00+00:00")
        self.assertEqual(result["finalizacion_reportada_local"], "2026-11-12T10:15:00-06:00")
        self.assertEqual(result["vencimiento_confirmacion_local"], "2026-11-14T10:15:00-06:00")

        # CA-01: Se informa al cliente mediante evento y está visible en su detalle
        self.assertEqual(len(result["eventos"]), 1)
        self.assertEqual(result["evento"]["tipo"], "FINALIZACION_REPORTADA")
        self.assertEqual(result["evento"]["cliente_id"], self.client.firebase_uid)
        self.assertIn("48 horas", result["evento"]["mensaje"])

        client_detail = get_client_request_detail(
            req.id,
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(client_detail["estado"], "EN_PROCESO")
        self.assertTrue(client_detail["pendiente_recepcion"])
        self.assertEqual(client_detail["vencimiento_confirmacion_en"], "2026-11-14T16:15:00+00:00")
        self.assertEqual(client_detail["vencimiento_confirmacion_local"], "2026-11-14T10:15:00-06:00")
        self.assertEqual(len(client_detail["eventos"]), 1)

    def test_ca04_repeated_report_is_idempotent_does_not_reset_deadline_or_duplicate_events(self):
        open_req = self._create_open_request(estado="En proceso")
        clock_t0 = datetime(2026, 11, 12, 18, 0, 0, tzinfo=timezone.utc)
        clock_t1 = clock_t0 + timedelta(hours=12)

        first = report_open_request_completion_endpoint(
            solicitud_id=open_req.id,
            response=Response(),
            payload=ReportServiceCompletionSchema(now=clock_t0),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        self.assertFalse(first["reintento_idempotente"])
        self.assertEqual(first["finalizacion_reportada_en"], "2026-11-12T18:00:00+00:00")
        self.assertEqual(first["vencimiento_confirmacion_en"], "2026-11-14T18:00:00+00:00")
        self.assertEqual(len(first["eventos"]), 1)
        first_event_id = first["evento"]["evento_id"]

        # Reintento 12 horas después con nuevo reloj: NO reinicia el plazo ni duplica eventos
        second = report_open_request_completion_endpoint(
            solicitud_id=open_req.id,
            response=Response(),
            payload=ReportServiceCompletionSchema(now=clock_t1),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        self.assertTrue(second["reintento_idempotente"])
        self.assertEqual(second["finalizacion_reportada_en"], "2026-11-12T18:00:00+00:00")
        self.assertEqual(second["vencimiento_confirmacion_en"], "2026-11-14T18:00:00+00:00")
        self.assertEqual(second["evento"]["evento_id"], first_event_id)
        self.assertEqual(len(get_completion_events(open_req.id)), 1)

    def test_ca01_permissions_and_invalid_states_are_rejected(self):
        req_in_progress = self._create_direct_request(estado="EN_PROCESO")

        # Otro técnico no seleccionado -> 403
        with self.assertRaises(HTTPException) as other_tech_err:
            report_service_completion(
                req_in_progress.id,
                {"uid": self.tech_other.firebase_uid},
                self.db,
            )
        self.assertEqual(other_tech_err.exception.status_code, 403)

        # Cliente intentando reportar finalización -> 403
        with self.assertRaises(HTTPException) as client_err:
            report_service_completion(
                req_in_progress.id,
                {"uid": self.client.firebase_uid},
                self.db,
            )
        self.assertEqual(client_err.exception.status_code, 403)

        # Solicitud inexistente -> 404
        with self.assertRaises(HTTPException) as missing_err:
            report_service_completion(
                str(uuid.uuid4()),
                {"uid": self.tech_selected.firebase_uid},
                self.db,
            )
        self.assertEqual(missing_err.exception.status_code, 404)

        # Estados distintos de En proceso -> 409
        for invalid_state in ("PENDIENTE", "ACEPTADA", "COMPLETADA", "CANCELADA", "RECHAZADA"):
            req_bad = self._create_direct_request(estado=invalid_state)
            with self.assertRaises(HTTPException) as state_err:
                report_direct_request_completion_endpoint(
                    solicitud_id=req_bad.id,
                    response=Response(),
                    payload=None,
                    claims={"uid": self.tech_selected.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(state_err.exception.status_code, 409)
            self.db.refresh(req_bad)
            self.assertIsNone(req_bad.finalizacion_reportada_en)
            self.assertFalse(req_bad.pendiente_recepcion)

    def test_ca05_pending_adjustment_blocks_completion_until_resolved(self):
        req = self._create_direct_request(estado="EN_PROCESO")

        # 1. Ajuste pendiente registrado en el servicio bloquea el reporte (409)
        register_pending_adjustment(
            req.id,
            monto_solicitado=750.0,
            motivo="Reemplazo adicional de llave de paso dañada",
        )
        with self.assertRaises(HTTPException) as adj_err:
            report_service_completion(
                req.id,
                {"uid": self.tech_selected.firebase_uid},
                self.db,
            )
        self.assertEqual(adj_err.exception.status_code, 409)
        self.assertIn("ajuste pendiente", adj_err.exception.detail.lower())

        self.db.refresh(req)
        self.assertIsNone(req.finalizacion_reportada_en)
        self.assertFalse(req.pendiente_recepcion)
        self.assertEqual(get_completion_events(req.id), [])

        # 2. Al resolverse el ajuste (ej. APROBADO), el técnico ya puede reportar como terminado
        resolve_pending_adjustments(req.id, nuevo_estado="APROBADO")
        completed_report = report_service_completion(
            req.id,
            {"uid": self.tech_selected.firebase_uid},
            self.db,
            now=datetime(2026, 11, 12, 19, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(completed_report["pendiente_recepcion"])
        self.assertEqual(completed_report["vencimiento_confirmacion_en"], "2026-11-14T19:00:00+00:00")

        # 3. También bloquea si existe un modelo ServiceAdjustment en BD o atributo en la entidad
        req_2 = self._create_direct_request(estado="EN_PROCESO")
        req_2.tiene_ajuste_pendiente = True
        with self.assertRaises(HTTPException) as attr_err:
            report_service_completion(
                req_2.id,
                {"uid": self.tech_selected.firebase_uid},
                self.db,
            )
        self.assertEqual(attr_err.exception.status_code, 409)

        # 4. Simular tabla/modelo ServiceAdjustment de HU-36
        class SimulatedServiceAdjustment(Base):
            __tablename__ = "ajustes_servicio_sim_st02"
            id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
            solicitud_id = Column(String, nullable=False)
            estado = Column(String, nullable=False, default="PENDIENTE")

        SimulatedServiceAdjustment.__table__.create(self.engine, checkfirst=True)
        models.ServiceAdjustment = SimulatedServiceAdjustment
        try:
            req_3 = self._create_open_request(estado="En proceso")
            adj_row = SimulatedServiceAdjustment(solicitud_id=req_3.id, estado="PENDIENTE")
            self.db.add(adj_row)
            self.db.commit()

            with self.assertRaises(HTTPException) as db_adj_err:
                report_service_completion(
                    req_3.id,
                    {"uid": self.tech_selected.firebase_uid},
                    self.db,
                )
            self.assertEqual(db_adj_err.exception.status_code, 409)

            adj_row.estado = "RECHAZADO"
            self.db.commit()
            res_3 = report_service_completion(
                req_3.id,
                {"uid": self.tech_selected.firebase_uid},
                self.db,
            )
            self.assertTrue(res_3["pendiente_recepcion"])
        finally:
            if hasattr(models, "ServiceAdjustment"):
                delattr(models, "ServiceAdjustment")


if __name__ == "__main__":
    unittest.main()
