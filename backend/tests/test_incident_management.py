"""HU-51: Pruebas unitarias para gestión de incidencias, tickets bilaterales, permisos y resoluciones."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import DirectRequest, Service, Specialty, TechnicianProfile, User, UserRole
from app.service_reception import (
    report_service_completion_by_technician,
    confirm_service_reception_by_client,
    run_scheduled_auto_completion,
)
from app.incident_management import (
    _INCIDENT_TICKETS,
    _TICKETS_BY_SERVICE,
    CreateIncidentTicketSchema,
    AdminResolveTicketSchema,
    AddActuationSchema,
    TipoIncidenciaEnum,
    DecisionResolucionEnum,
    create_incident_ticket,
    get_incident_ticket_detail,
    list_admin_incident_inbox,
    append_ticket_actuation,
    resolve_incident_ticket,
    has_active_incident_ticket,
    compute_user_incident_penalties,
)


class IncidentManagementHU51Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        _INCIDENT_TICKETS.clear()
        _TICKETS_BY_SERVICE.clear()

        # Usuarios de prueba
        self.client = self._add_user("client-1", "Ana Cliente", "CLIENTE", "1")
        self.other_client = self._add_user("client-2", "Beto Ajeno", "CLIENTE", "2")
        self.technician = self._add_user("tech-1", "Carlos Técnico", "TECNICO", "3")
        self.admin = self._add_user("admin-1", "Admin Moderador", "ADMIN", "9")

        self.specialty = Specialty(nombre="Fontanería")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.technician.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación de fuga",
            descripcion="Reparación residencial",
            tarifa_base=Decimal("500.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        _INCIDENT_TICKETS.clear()
        _TICKETS_BY_SERVICE.clear()
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080119900000{suffix}",
            telefono=f"+5049500000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _add_direct_request(self, *, estado: str = "Aceptada", monto: str = "1000.00") -> DirectRequest:
        now = datetime.now(timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.technician.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Calle 5, casa 12",
            fecha_hora=now + timedelta(days=2),
            descripcion="Fuga persistente en tubería principal",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal(monto),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal(monto),
            metodo_pago="efectivo",
            estado=estado,
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        return req

    def test_hu51_ca01_ca02_reporte_bilateral_prioridad_y_permisos_acceso(self):
        """CA-01 y CA-02: Cliente y técnico pueden reportar; admin accede a evidencias privadas y usuarios ajenos son rechazados."""
        req = self._add_direct_request(estado="En proceso")
        t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

        # 1. El cliente participante reporta un incumplimiento con evidencias privadas
        payload_client = CreateIncidentTicketSchema(
            solicitud_id=req.id,
            tipo=TipoIncidenciaEnum.INCUMPLIMIENTO_SERVICIO,
            motivo="El trabajo quedó incompleto y gotea agua.",
            evidencias=["evidencias/fuga_incompleta.jpg", "evidencias/recibo.pdf"],
        )
        ticket_client = create_incident_ticket(
            payload_client,
            {"uid": self.client.firebase_uid},
            self.db,
            now=t0,
        )
        self.assertEqual(ticket_client["prioridad"], "ALTA")
        self.assertEqual(ticket_client["rol_reportante"], "CLIENTE")
        self.assertEqual(len(ticket_client["evidencias_privadas"]), 2)

        # 2. El técnico participante también puede reportar una conducta inapropiada bilateralmente (CA-01)
        payload_tech = CreateIncidentTicketSchema(
            solicitud_id=req.id,
            tipo=TipoIncidenciaEnum.CONDUCTA_INAPROPIADA,
            motivo="El cliente mostró una conducta agresiva al momento de revisar la instalación.",
            evidencias=["evidencias/audio_amenaza.mp3"],
        )
        ticket_tech = create_incident_ticket(
            payload_tech,
            {"uid": self.technician.firebase_uid},
            self.db,
            now=t0 + timedelta(minutes=15),
        )
        self.assertEqual(ticket_tech["rol_reportante"], "TECNICO")

        # 3. Usuario ajeno no puede abrir un reporte ni ver el expediente (CA-01, CA-02)
        with self.assertRaises(HTTPException) as err_foreign_create:
            create_incident_ticket(
                payload_client,
                {"uid": self.other_client.firebase_uid},
                self.db,
                now=t0,
            )
        self.assertEqual(err_foreign_create.exception.status_code, 403)

        with self.assertRaises(HTTPException) as err_foreign_read:
            get_incident_ticket_detail(
                ticket_client["ticket_id"],
                {"uid": self.other_client.firebase_uid},
                self.db,
            )
        self.assertEqual(err_foreign_read.exception.status_code, 403)

        # 4. Solo el administrador consulta la bandeja priorizada y accede a las evidencias de ambos
        inbox = list_admin_incident_inbox({"uid": self.admin.firebase_uid}, self.db)
        self.assertEqual(len(inbox), 2)
        self.assertEqual(inbox[0]["prioridad"], "ALTA")

        admin_view = get_incident_ticket_detail(ticket_client["ticket_id"], {"uid": self.admin.firebase_uid}, self.db)
        self.assertEqual(len(admin_view["evidencias_privadas"]), 2)

    def test_hu51_ca03_bloqueo_cierre_automatico_48h_y_fondos(self):
        """CA-03: Un reporte abierto dentro de las 48h posteriores a la finalización bloquea cierre y fondos."""
        req = self._add_direct_request(estado="En proceso", monto="1200.00")
        t0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

        # Técnico reporta finalización
        report_service_completion_by_technician(req.id, {"uid": self.technician.firebase_uid}, self.db, now=t0)

        # Cliente abre un ticket de incidencia dentro de las 48h
        payload = CreateIncidentTicketSchema(
            solicitud_id=req.id,
            tipo=TipoIncidenciaEnum.RIESGO_SEGURIDAD,
            motivo="Riesgo de cortocircuito por contacto de tubería con cable expuesto.",
        )
        ticket = create_incident_ticket(payload, {"uid": self.client.firebase_uid}, self.db, now=t0 + timedelta(hours=5))
        self.assertTrue(ticket["bloquea_cierre_automatico"])
        self.assertTrue(ticket["bloquea_liberacion_fondos"])
        self.assertTrue(has_active_incident_ticket(req.id))

        # El job de 48h transcurre pero NO cierra el servicio porque está bloqueado por el ticket
        auto_run = run_scheduled_auto_completion(self.db, now=t0 + timedelta(hours=50))
        self.assertEqual(auto_run["total_completadas_automaticamente"], 0)
        self.assertIn(req.id, auto_run["solicitudes_bloqueadas_por_incidencia"])

    def test_hu51_ca04_ca05_resolucion_inmutable_y_apelacion_fuerza_mayor(self):
        """CA-04 y CA-05: El admin registra resolución sin borrar historial y aprueba justificaciones excluyendo falta."""
        req = self._add_direct_request(estado="En proceso")
        t0 = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)

        # Técnico abre ticket por apelación de fuerza mayor (ej. tormenta que impidió llegar a tiempo)
        payload = CreateIncidentTicketSchema(
            solicitud_id=req.id,
            tipo=TipoIncidenciaEnum.FUERZA_MAYOR,
            motivo="Cierre de calles por derrumbe durante la tormenta impidió acceso al sector.",
            es_apelacion_justificada=True,
            evidencias=["evidencias/comunicado_copeco.pdf"],
        )
        ticket = create_incident_ticket(payload, {"uid": self.technician.firebase_uid}, self.db, now=t0)
        ticket_id = ticket["ticket_id"]

        # El administrador registra una actuación intermedia previa (Patrón Audit Trail)
        append_ticket_actuation(
            ticket_id,
            AddActuationSchema(accion="REVISION_DOCUMENTAL", observaciones="Se verificó alerta roja de COPECO."),
            {"uid": self.admin.firebase_uid},
            self.db,
            now=t0 + timedelta(hours=2),
        )

        # El administrador emite resolución aprobando la justificación de fuerza mayor (CA-05)
        resolve_payload = AdminResolveTicketSchema(
            decision=DecisionResolucionEnum.DESBLOQUEAR_CONFIRMACION,
            observaciones="Justificación aprobada por causa fortuita documentada. Se permite reprogramación sin falta.",
            aprobar_justificacion_fuerza_mayor=True,
        )
        resolved = resolve_incident_ticket(ticket_id, resolve_payload, {"uid": self.admin.firebase_uid}, self.db, now=t0 + timedelta(hours=4))

        # Verificar inmutabilidad del historial de actuaciones (CA-04): deben existir las 3 actuaciones en orden
        self.assertEqual(len(resolved["historial_actuaciones"]), 3)
        self.assertEqual(resolved["historial_actuaciones"][0]["accion"], "CREACION_TICKET")
        self.assertEqual(resolved["historial_actuaciones"][1]["accion"], "REVISION_DOCUMENTAL")
        self.assertEqual(resolved["historial_actuaciones"][2]["accion"], "RESOLUCION_DESBLOQUEAR_CONFIRMACION")

        # CA-05: La falta queda excluida del cómputo
        self.assertFalse(resolved["falta_computada"])
        penalties = compute_user_incident_penalties(self.technician.firebase_uid)
        self.assertEqual(penalties["total_faltas_computables"], 0)
        self.assertEqual(penalties["total_justificaciones_excluidas"], 1)

    def test_hu51_ca06_ca07_servicio_completado_inmutable_y_suspension_conciliacion(self):
        """CA-06 y CA-07: Reporte sobre Completado no modifica el servicio; permite enlazar a suspensión y conciliación."""
        req = self._add_direct_request(estado="En proceso", monto="2000.00")
        t0 = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)

        # Servicio se completa formalmente
        report_service_completion_by_technician(req.id, {"uid": self.technician.firebase_uid}, self.db, now=t0)
        confirm_service_reception_by_client(req.id, {"uid": self.client.firebase_uid}, self.db, now=t0 + timedelta(hours=2))

        # CA-06: Se abre un reporte posterior sobre el servicio ya Completado
        payload = CreateIncidentTicketSchema(
            solicitud_id=req.id,
            tipo=TipoIncidenciaEnum.CONDUCTA_INAPROPIADA,
            motivo="El técnico solicitó cobros extra no pactados días después de cerrar el trabajo.",
        )
        ticket = create_incident_ticket(payload, {"uid": self.client.firebase_uid}, self.db, now=t0 + timedelta(days=3))
        self.assertEqual(ticket["estado_servicio_al_reportar"], "COMPLETADA")

        # CA-07: El admin dictamina SUSPENDER_USUARIO y conciliación de reembolso
        resolve_payload = AdminResolveTicketSchema(
            decision=DecisionResolucionEnum.SUSPENDER_USUARIO,
            observaciones="Se procede a suspensión preventiva de la cuenta por extorsión comprobada y conciliación de mediación.",
            vincular_suspension=True,
            conciliacion_reembolso=True,
        )
        resolved = resolve_incident_ticket(ticket["ticket_id"], resolve_payload, {"uid": self.admin.firebase_uid}, self.db, now=t0 + timedelta(days=3, hours=2))

        # CA-06: El servicio original NO fue alterado ni cancelado (inmutable)
        self.assertFalse(resolved["efecto_servicio"]["servicio_modificado"])
        self.db.refresh(req)
        self.assertEqual(req.estado.upper(), "COMPLETADA")

        # CA-07: Se enlazaron la suspensión y la conciliación sin simular pagos no bancarizados
        self.assertTrue(resolved["resolucion_final"]["suspension_vinculada"])
        self.assertTrue(resolved["resolucion_final"]["conciliacion_reembolso"])


if __name__ == "__main__":
    unittest.main()