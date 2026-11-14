from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.client_requests import get_client_request_detail, list_client_requests
from app.database import Base
from app.models import (
    CONFIRMATION_WINDOW_HOURS,
    DirectRequest,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
    calculate_confirmation_expiration,
    format_tegucigalpa_iso,
)
from app.service_completion import (
    ReportServiceCompletionSchema,
    ScheduledExpiredCheckSchema,
    clear_completion_events,
    clear_pending_adjustments,
    clear_service_incidents,
    get_auto_close_request_events,
    get_completion_events,
    get_request_completion_events_endpoint,
    register_pending_adjustment,
    register_service_incident,
    report_direct_request_completion_endpoint,
    report_open_request_completion_endpoint,
    report_service_completion,
    report_service_completion_endpoint,
    resolve_pending_adjustments,
    resolve_service_incidents,
    run_scheduled_expired_completions_check,
    run_scheduled_expired_completions_check_endpoint,
)
from app.technician_direct_requests import list_technician_direct_requests


class Hu34St05RolesRetriesControlledClockMatrixTests(unittest.TestCase):
    """HU-34 ST-05: Probar roles, reintentos y reloj controlado del plazo."""

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

        self.client_owner = self._add_user("client-st05-owner", "Lucía Propietaria", "CLIENTE", "1")
        self.client_other = self._add_user("client-st05-other", "Pedro Cliente Ajeno", "CLIENTE", "2")
        self.tech_selected = self._add_user("tech-st05-selected", "Marco Técnico Asignado", "TECNICO", "3")
        self.tech_other = self._add_user("tech-st05-other", "Raúl Técnico No Seleccionado", "TECNICO", "4")
        self.admin_only = self._add_user("admin-st05-1", "Carla Admin", "ADMIN", "5")

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

        self.specialty = Specialty(nombre="Electricidad Industrial")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech_selected.id,
            especialidad_id=self.specialty.id,
            nombre="Balanceo de cargas y tablero principal",
            descripcion="Servicio eléctrico certificado",
            tarifa_base=Decimal("950.00"),
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
            dni=f"080119990000{suffix}",
            telefono=f"+5049666000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _create_direct_request(
        self,
        *,
        estado: str = "EN_PROCESO",
        tecnico_uid: str | None = None,
    ) -> DirectRequest:
        now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_owner.firebase_uid,
            tecnico_id=self.tech_selected.firebase_uid if tecnico_uid is None else tecnico_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Ave. República de Chile, edificio #4",
            fecha_hora=now + timedelta(days=1),
            descripcion="Revisión de disyuntores principales",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("950.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal("950.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def _create_open_request(
        self,
        *,
        estado: str = "En proceso",
        tecnico_uid: str | None = "DEFAULT",
    ) -> OpenRequest:
        now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
        resolved_tech = self.tech_selected.firebase_uid if tecnico_uid == "DEFAULT" else tecnico_uid
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_owner.firebase_uid,
            tecnico_id=resolved_tech,
            especialidad_id=str(self.specialty.id),
            servicio_id=str(self.service.id),
            modalidad="ABIERTA",
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Ave. República de Chile, edificio #4",
            fecha_hora=now + timedelta(days=1),
            descripcion="Instalación de transferencia automática",
            presupuesto=Decimal("1800.00"),
            monto_base=Decimal("1800.00"),
            metodo_pago="tarjeta",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_roles_and_permissions_matrix_across_direct_and_open_requests(self):
        """CA-01: Matriz exhaustiva de roles (sin sesión, no registrado, cliente propietario, cliente ajeno,
        admin sin rol técnico, técnico no seleccionado, solicitud abierta sin técnico y técnico seleccionado)."""
        direct_req = self._create_direct_request(estado="EN_PROCESO")
        open_req = self._create_open_request(estado="En proceso")
        open_unassigned = self._create_open_request(estado="En proceso", tecnico_uid=None)

        # 1. Sin claims / UID vacío -> 401
        for target_id in (direct_req.id, open_req.id):
            with self.assertRaises(HTTPException) as unauth_err:
                report_service_completion(target_id, {}, self.db)
            self.assertEqual(unauth_err.exception.status_code, 401)

        # 2. Usuario inexistente en base de datos -> 404
        with self.assertRaises(HTTPException) as missing_user_err:
            report_service_completion(direct_req.id, {"uid": "ghost-uid-999"}, self.db)
        self.assertEqual(missing_user_err.exception.status_code, 404)

        # 3. Roles sin permiso de técnico o técnicos no seleccionados -> 403
        forbidden_cases = [
            (self.client_owner.firebase_uid, "Solo las cuentas con rol de técnico"),
            (self.client_other.firebase_uid, "Solo las cuentas con rol de técnico"),
            (self.admin_only.firebase_uid, "Solo las cuentas con rol de técnico"),
            (self.tech_other.firebase_uid, "Solo el técnico seleccionado"),
        ]
        for uid, expected_detail in forbidden_cases:
            for target_id in (direct_req.id, open_req.id):
                with self.assertRaises(HTTPException) as role_err:
                    report_service_completion(target_id, {"uid": uid}, self.db)
                self.assertEqual(role_err.exception.status_code, 403)
                self.assertIn(expected_detail, role_err.exception.detail)

        # 4. Solicitud abierta sin técnico asignado -> 403 incluso para un técnico válido
        with self.assertRaises(HTTPException) as unassigned_err:
            report_service_completion(
                open_unassigned.id,
                {"uid": self.tech_selected.firebase_uid},
                self.db,
            )
        self.assertEqual(unassigned_err.exception.status_code, 403)
        self.assertIn("Solo el técnico seleccionado", unassigned_err.exception.detail)

        # 5. Ningún intento fallido alteró el estado ni emitió eventos
        self.db.refresh(direct_req)
        self.db.refresh(open_req)
        self.assertFalse(direct_req.pendiente_recepcion)
        self.assertIsNone(direct_req.finalizacion_reportada_en)
        self.assertEqual(get_completion_events(direct_req.id), [])
        self.assertFalse(open_req.pendiente_recepcion)
        self.assertIsNone(open_req.finalizacion_reportada_en)
        self.assertEqual(get_completion_events(open_req.id), [])

        # 6. El técnico seleccionado puede reportar exitosamente en ambas modalidades
        t0 = datetime(2026, 11, 12, 17, 0, 0, tzinfo=timezone.utc)
        res_direct = report_direct_request_completion_endpoint(
            solicitud_id=direct_req.id,
            response=Response(),
            payload=ReportServiceCompletionSchema(now=t0),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        res_open = report_open_request_completion_endpoint(
            solicitud_id=open_req.id,
            response=Response(),
            payload=ReportServiceCompletionSchema(now=t0),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        self.assertTrue(res_direct["pendiente_recepcion"])
        self.assertFalse(res_direct["completada"])
        self.assertFalse(res_direct["fondos_liberados"])
        self.assertTrue(res_open["pendiente_recepcion"])
        self.assertFalse(res_open["completada"])
        self.assertFalse(res_open["fondos_liberados"])

    def test_invalid_states_rejected_and_idempotent_retries_across_all_endpoints(self):
        """CA-01, CA-02, CA-04: Estados distintos de En proceso se rechazan (409) y múltiples reintentos
        con reloj avanzado en todos los endpoints conservan el instante original sin duplicar eventos."""
        for invalid_direct_state in ("PENDIENTE", "ACEPTADA", "CANCELADA", "RECHAZADA", "COMPLETADA"):
            req = self._create_direct_request(estado=invalid_direct_state)
            with self.assertRaises(HTTPException) as state_err:
                report_service_completion(req.id, {"uid": self.tech_selected.firebase_uid}, self.db)
            self.assertEqual(state_err.exception.status_code, 409)

        for invalid_open_state in ("Publicada", "Con ofertas", "Asignada", "Cancelada", "Completada"):
            req = self._create_open_request(estado=invalid_open_state)
            with self.assertRaises(HTTPException) as state_err:
                report_service_completion(req.id, {"uid": self.tech_selected.firebase_uid}, self.db)
            self.assertEqual(state_err.exception.status_code, 409)

        # Solicitud en proceso reportada en t0 y reintentada en múltiples instantes y rutas
        req = self._create_direct_request(estado="EN_PROCESO")
        t0 = datetime(2026, 11, 12, 18, 15, 0, tzinfo=timezone.utc)
        expected_exp = t0 + timedelta(hours=48)

        first = report_service_completion_endpoint(
            solicitud_id=req.id,
            response=Response(),
            payload=ReportServiceCompletionSchema(now=t0),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        self.assertFalse(first["reintento_idempotente"])
        self.assertEqual(first["finalizacion_reportada_en"], t0.isoformat())
        self.assertEqual(first["vencimiento_confirmacion_en"], expected_exp.isoformat())

        retry_endpoints = [
            (report_direct_request_completion_endpoint, t0 + timedelta(minutes=5)),
            (report_service_completion_endpoint, t0 + timedelta(hours=6)),
            (report_direct_request_completion_endpoint, t0 + timedelta(hours=24)),
            (report_service_completion_endpoint, t0 + timedelta(hours=47, minutes=59)),
        ]
        for endpoint_fn, retry_time in retry_endpoints:
            retry_res = endpoint_fn(
                solicitud_id=req.id,
                response=Response(),
                payload=ReportServiceCompletionSchema(now=retry_time),
                claims={"uid": self.tech_selected.firebase_uid},
                db=self.db,
            )
            self.assertTrue(retry_res["reintento_idempotente"])
            self.assertEqual(retry_res["finalizacion_reportada_en"], t0.isoformat())
            self.assertEqual(retry_res["vencimiento_confirmacion_en"], expected_exp.isoformat())
            self.assertEqual(retry_res["estado"], "EN_PROCESO")
            self.assertTrue(retry_res["pendiente_recepcion"])
            self.assertFalse(retry_res["completada"])
            self.assertFalse(retry_res["fondos_liberados"])

        # Exactamente 1 evento publicado tras 5 llamadas (1 inicial + 4 reintentos)
        events = get_completion_events(req.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["finalizacion_reportada_en"], t0.isoformat())
        self.assertEqual(events[0]["vencimiento_confirmacion_en"], expected_exp.isoformat())

    def test_controlled_clock_48h_exact_boundaries_independent_deadlines_and_tegucigalpa_timezone(self):
        """CA-03, CA-05, CA-06 y Condición Transversal:
        - Bloqueo por ajuste pendiente y desbloqueo al resolverlo.
        - Reloj controlado en frontera exacta (48h - 1s vs 48h + 0s) sin esperar dos días reales.
        - Independencia entre el plazo de confirmación (48h desde reporte en En proceso) y el plazo de reseñas (48h desde Completada).
        - Referencia horaria inequívoca en UTC y presentación en America/Tegucigalpa (-06:00).
        """
        req_direct = self._create_direct_request(estado="EN_PROCESO")
        req_open = self._create_open_request(estado="En proceso")

        # CA-05: Ajuste pendiente bloquea el reporte
        register_pending_adjustment(
            req_direct.id,
            motivo="Cambio de cableado calibre 8 AWG pendiente de aprobación",
            estado="PENDIENTE",
        )
        with self.assertRaises(HTTPException) as adj_err:
            report_service_completion(
                req_direct.id,
                {"uid": self.tech_selected.firebase_uid},
                self.db,
                now=datetime(2026, 11, 12, 19, 0, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(adj_err.exception.status_code, 409)

        # Resolver ajuste permite reportar con reloj controlado en t_report = 2026-11-12 20:00:00 UTC (14:00 America/Tegucigalpa)
        resolve_pending_adjustments(req_direct.id, nuevo_estado="APROBADO")
        t_report = datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone.utc)
        res_direct = report_service_completion(
            req_direct.id,
            {"uid": self.tech_selected.firebase_uid},
            self.db,
            now=t_report,
        )
        res_open = report_service_completion(
            req_open.id,
            {"uid": self.tech_selected.firebase_uid},
            self.db,
            now=t_report,
        )

        # Verificar zona horaria America/Tegucigalpa (-06:00) y plazo de 48 horas
        self.assertEqual(res_direct["zona_horaria_usuario"], "America/Tegucigalpa")
        self.assertEqual(res_direct["plazo_confirmacion_horas"], CONFIRMATION_WINDOW_HOURS)
        self.assertEqual(res_direct["finalizacion_reportada_local"], "2026-11-12T14:00:00-06:00")
        self.assertEqual(res_direct["vencimiento_confirmacion_local"], "2026-11-14T14:00:00-06:00")
        self.assertEqual(res_open["vencimiento_confirmacion_local"], "2026-11-14T14:00:00-06:00")

        # Visibilidad para ambas partes (técnico y cliente)
        tech_list = list_technician_direct_requests(
            {"uid": self.tech_selected.firebase_uid},
            self.db,
        )
        tech_item = next(item for item in tech_list if item["id"] == req_direct.id)
        self.assertEqual(tech_item["vencimiento_confirmacion_local"], "2026-11-14T14:00:00-06:00")
        self.assertTrue(tech_item["pendiente_recepcion"])

        client_list_res = list_client_requests(
            {"uid": self.client_owner.firebase_uid},
            self.db,
        )
        client_direct_item = next(
            item for item in client_list_res["solicitudes"] if item["id"] == req_direct.id
        )
        self.assertEqual(client_direct_item["vencimiento_confirmacion_local"], "2026-11-14T14:00:00-06:00")
        self.assertTrue(client_direct_item["pendiente_recepcion"])

        client_open_detail = get_client_request_detail(
            req_open.id,
            {"uid": self.client_owner.firebase_uid},
            self.db,
        )
        self.assertEqual(client_open_detail["vencimiento_confirmacion_local"], "2026-11-14T14:00:00-06:00")
        self.assertTrue(client_open_detail["pendiente_recepcion"])

        # Frontera exacta: 1 segundo antes de las 48h (t_report + 47h 59m 59s) -> 0 vencidos
        one_sec_before = t_report + timedelta(hours=48) - timedelta(seconds=1)
        check_before = run_scheduled_expired_completions_check(self.db, now=one_sec_before)
        self.assertEqual(check_before["total_vencidos"], 0)
        self.assertEqual(check_before["total_elegibles_cierre_automatico"], 0)

        # Abrir incidencia en req_open 30 segundos antes del vencimiento
        register_service_incident(req_open.id, motivo="Falta ajuste de tapa de registro", estado="EN_REVISION")

        # Frontera exacta: a las 48h en punto (t_report + 48h 00m 00s) -> 2 vencidos (1 elegible, 1 bloqueado por incidencia)
        exact_48h = t_report + timedelta(hours=48)
        check_exact = run_scheduled_expired_completions_check_endpoint(
            response=Response(),
            payload=ScheduledExpiredCheckSchema(now=exact_48h),
            claims={"uid": self.tech_selected.firebase_uid},
            db=self.db,
        )
        self.assertEqual(check_exact["fecha_ejecucion_local"], "2026-11-14T14:00:00-06:00")
        self.assertEqual(check_exact["total_vencidos"], 2)
        self.assertEqual(check_exact["total_elegibles_cierre_automatico"], 1)
        self.assertEqual(check_exact["total_bloqueados_por_incidencia"], 1)
        self.assertEqual(check_exact["elegibles_cierre_automatico"][0]["id"], req_direct.id)
        self.assertEqual(check_exact["bloqueados_por_incidencia"][0]["id"], req_open.id)

        # Condición transversal: Plazos independientes
        # El plazo de confirmación inició en t_report (mientras sigue En proceso).
        # Cuando el servicio pasa posteriormente a Completada en t_completed (ej. 12h después del reporte),
        # su plazo de 48h para publicación de reseñas inicia en t_completed sin alterar los registros del reporte de finalización.
        t_completed = t_report + timedelta(hours=12)
        review_window_expiration = calculate_confirmation_expiration(t_completed)
        self.assertEqual(format_tegucigalpa_iso(t_completed), "2026-11-13T02:00:00-06:00")
        self.assertEqual(format_tegucigalpa_iso(review_window_expiration), "2026-11-15T02:00:00-06:00")
        self.assertNotEqual(
            res_direct["vencimiento_confirmacion_en"],
            review_window_expiration.isoformat(),
        )

        # Al resolver la incidencia de req_open, el proceso programado puede solicitar su cierre automático
        resolve_service_incidents(req_open.id, nuevo_estado="CERRADA")
        check_after_incident = run_scheduled_expired_completions_check(
            self.db,
            now=exact_48h + timedelta(minutes=15),
        )
        self.assertEqual(check_after_incident["total_elegibles_cierre_automatico"], 2)
        self.assertEqual(check_after_incident["total_bloqueados_por_incidencia"], 0)
        self.assertEqual(len(get_auto_close_request_events(req_direct.id)), 1)
        self.assertEqual(len(get_auto_close_request_events(req_open.id)), 1)

        # Consultar eventos del reporte verifica trazabilidad completa para cliente y técnico
        events_summary = get_request_completion_events_endpoint(
            solicitud_id=req_open.id,
            response=Response(),
            claims={"uid": self.client_owner.firebase_uid},
            db=self.db,
        )
        self.assertEqual(len(events_summary["eventos_finalizacion"]), 1)
        self.assertEqual(len(events_summary["eventos_cierre_automatico"]), 1)


if __name__ == "__main__":
    unittest.main()
