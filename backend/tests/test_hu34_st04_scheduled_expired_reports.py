from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base
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
    ScheduledExpiredCheckSchema,
    clear_completion_events,
    clear_pending_adjustments,
    clear_service_incidents,
    get_auto_close_request_events,
    get_request_completion_events_endpoint,
    query_expired_completion_reports,
    register_service_incident,
    report_service_completion,
    resolve_service_incidents,
    run_scheduled_expired_completions_check,
    run_scheduled_expired_completions_check_endpoint,
)


class Hu34St04ScheduledExpiredReportsTests(unittest.TestCase):
    def setUp(self):
        clear_completion_events()
        clear_pending_adjustments()
        clear_service_incidents()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client = self._add_user("client-st04-1", "Elena Cliente", "CLIENTE", "1")
        self.client_foreign = self._add_user("client-st04-2", "Otro Cliente", "CLIENTE", "2")
        self.tech = self._add_user("tech-st04-1", "Fernando Técnico", "TECNICO", "3")
        self.tech_foreign = self._add_user("tech-st04-2", "Técnico Ajeno", "TECNICO", "4")

        self.db.add_all(
            [
                TechnicianProfile(
                    usuario_id=self.tech.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
                TechnicianProfile(
                    usuario_id=self.tech_foreign.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
            ]
        )

        self.specialty = Specialty(nombre="Refrigeración")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech.id,
            especialidad_id=self.specialty.id,
            nombre="Carga de gas y limpieza de condensador",
            descripcion="Servicio técnico de aire acondicionado",
            tarifa_base=Decimal("750.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        clear_completion_events()
        clear_pending_adjustments()
        clear_service_incidents()
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080119980000{suffix}",
            telefono=f"+5049777000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _create_direct_request(self, *, estado: str = "EN_PROCESO") -> DirectRequest:
        now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.tech.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Tepeyac",
            direccion_exacta="Bloque C, casa #9",
            fecha_hora=now + timedelta(days=1),
            descripcion="Aire acondicionado no enfría",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("750.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal("750.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def _create_open_request(self, *, estado: str = "En proceso") -> OpenRequest:
        now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.tech.firebase_uid,
            especialidad_id=str(self.specialty.id),
            servicio_id=str(self.service.id),
            modalidad="ABIERTA",
            municipio="Distrito Central",
            sector="Col. Tepeyac",
            direccion_exacta="Bloque C, casa #9",
            fecha_hora=now + timedelta(days=1),
            descripcion="Instalación de mini split 18000 BTU",
            presupuesto=Decimal("1200.00"),
            monto_base=Decimal("1200.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_ca06_scheduled_check_identifies_expired_reports_and_checks_incidents_with_controlled_clock(self):
        t0 = datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc)
        direct_clean = self._create_direct_request(estado="EN_PROCESO")
        open_with_incident = self._create_open_request(estado="En proceso")
        direct_not_expired = self._create_direct_request(estado="EN_PROCESO")

        # Reportar finalización en t0 para direct_clean y open_with_incident (vencen en t0 + 48h)
        report_service_completion(
            direct_clean.id,
            {"uid": self.tech.firebase_uid},
            self.db,
            now=t0,
        )
        report_service_completion(
            open_with_incident.id,
            {"uid": self.tech.firebase_uid},
            self.db,
            now=t0,
        )
        # Reportar 10 horas después para direct_not_expired (vence en t0 + 58h)
        report_service_completion(
            direct_not_expired.id,
            {"uid": self.tech.firebase_uid},
            self.db,
            now=t0 + timedelta(hours=10),
        )

        # Registrar incidencia abierta sobre open_with_incident dentro del plazo de 48h
        register_service_incident(
            open_with_incident.id,
            motivo="El equipo sigue presentando goteo interno.",
            estado="ABIERTA",
        )

        # 1. Antes de cumplirse las 48h (t0 + 47h 59m): ningún reporte está vencido
        before_48h = t0 + timedelta(hours=47, minutes=59)
        early_check = run_scheduled_expired_completions_check(self.db, now=before_48h)
        self.assertEqual(early_check["total_reportes_en_proceso_evaluados"], 3)
        self.assertEqual(early_check["total_vencidos"], 0)
        self.assertEqual(early_check["total_elegibles_cierre_automatico"], 0)
        self.assertEqual(early_check["total_bloqueados_por_incidencia"], 0)

        # 2. Al cumplirse las 48h exactas (t0 + 48h): 2 vencidos (1 elegible para cierre automático, 1 bloqueado por incidencia)
        at_48h = t0 + timedelta(hours=48)
        scheduled_res = run_scheduled_expired_completions_check_endpoint(
            response=Response(),
            payload=ScheduledExpiredCheckSchema(now=at_48h),
            claims={"uid": self.tech.firebase_uid},
            db=self.db,
        )
        self.assertEqual(scheduled_res["total_reportes_en_proceso_evaluados"], 3)
        self.assertEqual(scheduled_res["total_vencidos"], 2)
        self.assertEqual(scheduled_res["total_elegibles_cierre_automatico"], 1)
        self.assertEqual(scheduled_res["total_bloqueados_por_incidencia"], 1)
        self.assertEqual(scheduled_res["elegibles_cierre_automatico"][0]["id"], direct_clean.id)
        self.assertEqual(scheduled_res["bloqueados_por_incidencia"][0]["id"], open_with_incident.id)
        self.assertTrue(scheduled_res["bloqueados_por_incidencia"][0]["tiene_incidencia_abierta"])
        self.assertFalse(scheduled_res["bloqueados_por_incidencia"][0]["elegible_cierre_automatico"])

        # Se emitió solicitud de cierre automático únicamente para direct_clean
        self.assertEqual(len(get_auto_close_request_events(direct_clean.id)), 1)
        self.assertEqual(len(get_auto_close_request_events(open_with_incident.id)), 0)

        # Re-ejecutar la tarea programada no duplica eventos de cierre automático
        repeat_res = run_scheduled_expired_completions_check(self.db, now=at_48h + timedelta(minutes=30))
        self.assertEqual(repeat_res["total_elegibles_cierre_automatico"], 1)
        self.assertEqual(len(get_auto_close_request_events(direct_clean.id)), 1)

        # 3. Cuando la incidencia de open_with_incident se resuelve, pasa a ser elegible para cierre automático
        resolve_service_incidents(open_with_incident.id, nuevo_estado="RESUELTA")
        after_resolution = run_scheduled_expired_completions_check(self.db, now=at_48h + timedelta(hours=1))
        self.assertEqual(after_resolution["total_elegibles_cierre_automatico"], 2)
        self.assertEqual(after_resolution["total_bloqueados_por_incidencia"], 0)
        self.assertEqual(len(get_auto_close_request_events(open_with_incident.id)), 1)

    def test_completion_events_endpoint_permissions_and_db_incident_model_support(self):
        t0 = datetime(2026, 11, 12, 16, 0, 0, tzinfo=timezone.utc)
        req = self._create_direct_request(estado="EN_PROCESO")
        report_service_completion(req.id, {"uid": self.tech.firebase_uid}, self.db, now=t0)

        # Cliente propietario y técnico asignado pueden consultar eventos
        client_events = get_request_completion_events_endpoint(
            solicitud_id=req.id,
            response=Response(),
            claims={"uid": self.client.firebase_uid},
            db=self.db,
        )
        self.assertEqual(len(client_events["eventos_finalizacion"]), 1)
        self.assertEqual(client_events["vencimiento_confirmacion_local"], "2026-11-14T10:00:00-06:00")

        # Usuario ajeno recibe 403
        with self.assertRaises(HTTPException) as foreign_err:
            get_request_completion_events_endpoint(
                solicitud_id=req.id,
                response=Response(),
                claims={"uid": self.client_foreign.firebase_uid},
                db=self.db,
            )
        self.assertEqual(foreign_err.exception.status_code, 403)

        # Simular tabla/modelo ServiceIncident en base de datos
        class SimulatedServiceIncident(Base):
            __tablename__ = "incidencias_servicio_sim_st04"
            id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
            solicitud_id = Column(String, nullable=False)
            estado = Column(String, nullable=False, default="ABIERTA")

        SimulatedServiceIncident.__table__.create(self.engine, checkfirst=True)
        models.ServiceIncident = SimulatedServiceIncident
        try:
            self.db.add(SimulatedServiceIncident(solicitud_id=req.id, estado="ABIERTA"))
            self.db.commit()

            expired_only_eligible = query_expired_completion_reports(
                self.db,
                now=t0 + timedelta(hours=49),
                solo_elegibles_cierre=True,
            )
            self.assertEqual(expired_only_eligible, [])

            expired_all = query_expired_completion_reports(
                self.db,
                now=t0 + timedelta(hours=49),
                solo_elegibles_cierre=False,
            )
            self.assertEqual(len(expired_all), 1)
            self.assertTrue(expired_all[0]["tiene_incidencia_abierta"])
            self.assertFalse(expired_all[0]["elegible_cierre_automatico"])
        finally:
            if hasattr(models, "ServiceIncident"):
                delattr(models, "ServiceIncident")


if __name__ == "__main__":
    unittest.main()
