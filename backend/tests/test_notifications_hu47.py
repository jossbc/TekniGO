"""HU-47 ST-05: Pruebas completas de bandeja de notificaciones (CA-01 a CA-07)."""
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException, Response, WebSocketDisconnect
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adjustment_policy import default_adjustment_policy
from app.adjustment_repository import SqlAlchemyAdjustmentRepository
from app.adjustment_service import AdjustmentDecision, request_adjustment, respond_adjustment
from app.admin_verification import AdminVerificationReviewSchema, DocumentReviewItem, apply_technician_review
from app.cancellation_policy import default_cancellation_policy
from app.cancellation_repository import SqlAlchemyCancellationRepository
from app.cancellation_service import CancellationCause, cancel_request
from app.certifications import CertificateDecision, review_certificate
from app.database import Base
from app.direct_requests_repository import DirectRequestRepository
from app.identity_documents import UploadedPart
from app.incident_management import (
    AdminResolveTicketSchema,
    CreateIncidentTicketSchema,
    DecisionResolucionEnum,
    TipoIncidenciaEnum,
    create_incident_ticket,
    resolve_incident_ticket,
)
from app.models import (
    CoverageZone,
    DirectRequest,
    IdentityDocument,
    IdentityVerificationRequest,
    NotificationEvent,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    TechnicianSpecialty,
    TradeCertificate,
    User,
    UserNotification,
    UserRole,
)
from app.notifications_consumer import process_pending_notification_events
from app.notifications_events import enqueue_notification_event
from app.notifications_realtime import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHORIZED,
    notification_manager,
)
from app.notifications_router import (
    get_notification_detail_endpoint,
    list_notifications_endpoint,
    mark_all_notifications_read_endpoint,
    mark_notification_read_endpoint,
    notifications_websocket_endpoint,
)
from app.notifications_templates import (
    EVENT_AJUSTE_PRESUPUESTO_APROBADO,
    EVENT_AJUSTE_PRESUPUESTO_RECHAZADO,
    EVENT_AJUSTE_PRESUPUESTO_SOLICITADO,
    EVENT_CERTIFICACION_APROBADA,
    EVENT_CERTIFICACION_RECHAZADA,
    EVENT_CIERRE_AUTOMATICO_48H,
    EVENT_DESEMBOLSO_PAGADO,
    EVENT_DESEMBOLSO_RECHAZADO,
    EVENT_DESEMBOLSO_SOLICITADO,
    EVENT_FINALIZACION_REPORTADA,
    EVENT_INCIDENCIA_ABIERTA,
    EVENT_INCIDENCIA_RESUELTA,
    EVENT_LIQUIDACION_APROBADA,
    EVENT_LIQUIDACION_RECHAZADA,
    EVENT_LIQUIDACION_RECIBIDA,
    EVENT_NUEVA_CONTRAOFERTA_ABIERTA,
    EVENT_NUEVA_OFERTA_ABIERTA,
    EVENT_NUEVA_SOLICITUD_DIRECTA,
    EVENT_PROPUESTA_ADJUDICADA,
    EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION,
    EVENT_PROPUESTA_RECHAZADA,
    EVENT_RECEPCION_CONFIRMADA,
    EVENT_RESTRICCION_OPERATIVA,
    EVENT_SERVICIO_CANCELADO,
    EVENT_SERVICIO_INICIADO,
    EVENT_SOLICITUD_DIRECTA_ACEPTADA,
    EVENT_SOLICITUD_DIRECTA_RECHAZADA,
    EVENT_VERIFICACION_APROBADA,
    NotificationPrivacySanitizer,
)
from app.open_requests import (
    AwardProposalSchema,
    CounterOfferSchema,
    RejectProposalSchema,
    accept_published_budget_for_open_request,
    award_open_request_proposal_by_owner,
    reject_open_request_proposal_by_owner,
    submit_counter_offer_for_open_request,
)
from app.payouts_router import (
    BankAccountInput,
    PayoutDecisionInput,
    PayoutRequestInput,
    decide_payout,
    request_payout,
    save_my_bank_account,
)
from app.request_states import start_request_service
from app.service_completion import report_service_completion
from app.service_reception import confirm_service_reception_by_client, run_scheduled_auto_completion
from app.settlements_router import SettlementDecisionSchema, decide_settlement, submit_settlement
from app.storage import PrivateDocumentStorage
from app.technician_activation import run_scheduled_expiration_review
from app.technician_direct_requests import respond_to_direct_request

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _run_coro(coro):
    """Ejecuta corrutinas puras sin abrir sockets locales del event loop."""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def _make_user(
    db: Session,
    uid: str,
    role: str,
    n: int,
    *,
    dni: str | None = None,
    phone: str | None = None,
    verified: bool = True,
    financially_restricted: bool = False,
) -> User:
    user = User(
        id=uuid.uuid4(),
        firebase_uid=uid,
        nombre_completo=f"Persona {uid}",
        correo=f"{uid}@teknigo.test",
        dni=dni or f"08011999{n:05d}",
        telefono=phone or f"+5049{n:07d}",
        telefono_verificado=True,
        estado_identidad="VERIFICADO" if verified else "EN_REVISION",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(usuario_id=user.id, rol=role))
    if role == "TECNICO":
        db.add(
            TechnicianProfile(
                usuario_id=user.id,
                disponible=True,
                estado_verificacion="VERIFICADO" if verified else "EN_REVISION",
                restriccion_financiera=financially_restricted,
                servicios_efectivo_sin_liquidar=2 if financially_restricted else 0,
            )
        )
    db.flush()
    return user


def _make_catalog(db: Session, owner_id: uuid.UUID, *, suffix: str = "1"):
    spec = Specialty(id=uuid.uuid4(), nombre=f"Electricidad {suffix}", activa=True)
    db.add(spec)
    db.flush()
    srv = Service(
        id=uuid.uuid4(),
        usuario_id=owner_id,
        especialidad_id=spec.id,
        nombre=f"Instalación Eléctrica {suffix}",
        descripcion="Servicio eléctrico residencial",
        tarifa_base=Decimal("500.00"),
        unidad_cobro="SERVICIO",
        activo=True,
    )
    db.add(srv)
    db.flush()
    return spec, srv


class FakeWebSocket:
    def __init__(self, *, headers: dict[str, str] | None = None, incoming: list[dict] | None = None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._incoming = deque(incoming or [])
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.sent: list[dict] = []

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, data: dict):
        if self.closed:
            raise RuntimeError("WebSocket already closed")
        self.sent.append(data)

    async def receive_json(self):
        if not self._incoming:
            raise WebSocketDisconnect(code=1000)
        return self._incoming.popleft()


class NotificationsHu47Tests(unittest.TestCase):
    def setUp(self):
        notification_manager.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            "os.environ",
            {
                "PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes",
                "PRIVATE_STORAGE_BACKEND": "local",
                "PRIVATE_STORAGE_DIR": self.tmp.name,
                "SUPABASE_URL": "",
                "SUPABASE_SERVICE_ROLE_KEY": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        notification_manager.reset()
        self.db.close()
        self.engine.dispose()

    def _user_notifications(self, user: User) -> list[UserNotification]:
        return list(
            self.db.scalars(
                select(UserNotification)
                .where(UserNotification.destinatario_id == user.id)
                .order_by(UserNotification.creada_en.asc(), UserNotification.id.asc())
            ).all()
        )

    def test_ca01_ca07_inbox_visibility_ownership_timezone_and_read_isolation(self):
        cli = _make_user(self.db, "cli-1", "CLIENTE", 1)
        other = _make_user(self.db, "cli-2", "CLIENTE", 2)
        self.db.commit()

        enqueue_notification_event(
            self.db,
            clave_evento="evt-tz-1",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            destinatarios=[(cli.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "sol-100", "modalidad": "DIRECTA"},
            now=datetime(2026, 11, 25, 18, 30, tzinfo=timezone.utc),
        )
        enqueue_notification_event(
            self.db,
            clave_evento="evt-tz-2",
            tipo_evento=EVENT_FINALIZACION_REPORTADA,
            destinatarios=[(other.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "sol-200", "modalidad": "DIRECTA"},
            now=datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc),
        )
        self.db.commit()

        # CA-01: cada usuario solo ve sus propios avisos con hora UTC y Tegucigalpa (-06:00)
        cli_inbox = list_notifications_endpoint(
            Response(),
            limit=30,
            offset=0,
            after_id=None,
            since=None,
            after=None,
            solo_no_leidas=False,
            last_event_id=None,
            claims={"uid": cli.firebase_uid},
            db=self.db,
        )
        self.assertEqual(cli_inbox["total"], 1)
        self.assertEqual(cli_inbox["no_leidas"], 1)
        self.assertEqual(cli_inbox["zona_horaria"], "America/Tegucigalpa")
        item = cli_inbox["notificaciones"][0]
        self.assertEqual(item["tipo_evento"], EVENT_SERVICIO_INICIADO)
        self.assertTrue(item["creada_en"].endswith("+00:00"))
        self.assertEqual(item["creada_en_local"], "2026-11-25T12:30:00-06:00")
        self.assertEqual(item["enlace_ruta"], "/mis-trabajos/sol-100")

        other_inbox = list_notifications_endpoint(
            Response(),
            limit=30,
            offset=0,
            after_id=None,
            since=None,
            after=None,
            solo_no_leidas=False,
            last_event_id=None,
            claims={"uid": other.firebase_uid},
            db=self.db,
        )
        self.assertEqual(other_inbox["total"], 1)
        other_notif_id = other_inbox["notificaciones"][0]["id"]

        # CA-07: nunca puede abrir ni marcar como leída una notificación ajena
        with self.assertRaises(HTTPException) as get_ctx:
            get_notification_detail_endpoint(other_notif_id, Response(), claims={"uid": cli.firebase_uid}, db=self.db)
        self.assertEqual(get_ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as patch_ctx:
            mark_notification_read_endpoint(other_notif_id, Response(), claims={"uid": cli.firebase_uid}, db=self.db)
        self.assertEqual(patch_ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as missing_ctx:
            get_notification_detail_endpoint(str(uuid.uuid4()), Response(), claims={"uid": cli.firebase_uid}, db=self.db)
        self.assertEqual(missing_ctx.exception.status_code, 404)

        # Marcar propia como leída actualiza leida/no_leidas; read-all no afecta al otro usuario
        marked = mark_notification_read_endpoint(item["id"], Response(), claims={"uid": cli.firebase_uid}, db=self.db)
        self.assertEqual(marked["no_leidas"], 0)
        self.assertTrue(marked["notificacion"]["leida"])
        other_after = list_notifications_endpoint(
            Response(),
            limit=30,
            offset=0,
            after_id=None,
            since=None,
            after=None,
            solo_no_leidas=False,
            last_event_id=None,
            claims={"uid": other.firebase_uid},
            db=self.db,
        )
        self.assertEqual(other_after["no_leidas"], 1)

        mark_all_res = mark_all_notifications_read_endpoint(Response(), claims={"uid": other.firebase_uid}, db=self.db)
        self.assertEqual(mark_all_res["actualizadas"], 1)
        self.assertEqual(mark_all_res["no_leidas"], 0)

    def test_ca02_direct_and_open_request_proposal_award_and_lifecycle_notifications(self):
        cli = _make_user(self.db, "cli-flow", "CLIENTE", 10)
        tech1 = _make_user(self.db, "tech-1", "TECNICO", 11)
        tech2 = _make_user(self.db, "tech-2", "TECNICO", 12)
        tech3 = _make_user(self.db, "tech-3", "TECNICO", 13)
        admin = _make_user(self.db, "adm-1", "ADMIN", 14)
        spec, srv = _make_catalog(self.db, tech1.id, suffix="CA02")
        for t in (tech1, tech2, tech3):
            self.db.add(TechnicianSpecialty(usuario_id=t.id, especialidad_id=spec.id))
            self.db.add(
                CoverageZone(
                    usuario_id=t.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Kennedy",
                )
            )
        self.db.commit()

        # 1. Nueva solicitud directa + decisión
        direct_req = DirectRequestRepository.create_request(
            self.db,
            {
                "id": str(uuid.uuid4()),
                "cliente_id": cli.firebase_uid,
                "tecnico_id": tech1.firebase_uid,
                "servicio_id": str(srv.id),
                "municipio": "Distrito Central",
                "sector": "Kennedy",
                "direccion_exacta": "Casa 99 Calle Secreta Privada",
                "fecha_hora": datetime.now(timezone.utc) + timedelta(days=1),
                "descripcion": "Reparar tablero",
                "cantidad": 1,
                "tarifa_aplicada": Decimal("500.00"),
                "unidad_cobro": "SERVICIO",
                "monto_base": Decimal("500.00"),
                "metodo_pago": "efectivo",
                "monto_ajuste": Decimal("0.00"),
                "monto_total": Decimal("500.00"),
                "estado": "PENDIENTE",
            },
        )
        self.assertIn(EVENT_NUEVA_SOLICITUD_DIRECTA, [n.tipo_evento for n in self._user_notifications(tech1)])

        respond_to_direct_request(direct_req.id, "ACEPTADA", {"uid": tech1.firebase_uid}, self.db)
        self.assertIn(EVENT_SOLICITUD_DIRECTA_ACEPTADA, [n.tipo_evento for n in self._user_notifications(cli)])

        start_request_service(self.db, direct_req, uid=tech1.firebase_uid)
        self.assertIn(EVENT_SERVICIO_INICIADO, [n.tipo_evento for n in self._user_notifications(cli)])

        # 2. Ajuste de presupuesto
        adj_repo = SqlAlchemyAdjustmentRepository()
        adj_policy = default_adjustment_policy()
        request_adjustment(
            self.db,
            direct_req.id,
            motivo="Se requiere breaker adicional",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("650.00"),
            claims={"uid": tech1.firebase_uid},
            policy=adj_policy,
            repository=adj_repo,
        )
        self.assertIn(EVENT_AJUSTE_PRESUPUESTO_SOLICITADO, [n.tipo_evento for n in self._user_notifications(cli)])

        respond_adjustment(
            self.db,
            direct_req.id,
            decision=AdjustmentDecision.APROBAR,
            comentario="De acuerdo",
            claims={"uid": cli.firebase_uid},
            repository=adj_repo,
        )
        self.assertIn(EVENT_AJUSTE_PRESUPUESTO_APROBADO, [n.tipo_evento for n in self._user_notifications(tech1)])

        # 3. Reporte de finalización + confirmación cliente
        report_service_completion(direct_req.id, claims={"uid": tech1.firebase_uid}, db=self.db, comentario="Listo")
        self.assertIn(EVENT_FINALIZACION_REPORTADA, [n.tipo_evento for n in self._user_notifications(cli)])

        confirm_service_reception_by_client(direct_req.id, {"uid": cli.firebase_uid}, self.db)
        self.assertIn(EVENT_RECEPCION_CONFIRMADA, [n.tipo_evento for n in self._user_notifications(tech1)])

        # 4. Solicitud directa rechazada
        rejected_req = DirectRequestRepository.create_request(
            self.db,
            {
                "id": str(uuid.uuid4()),
                "cliente_id": cli.firebase_uid,
                "tecnico_id": tech2.firebase_uid,
                "servicio_id": str(srv.id),
                "municipio": "Distrito Central",
                "sector": "Miraflores",
                "direccion_exacta": "Casa 10 Bloque B",
                "fecha_hora": datetime.now(timezone.utc) + timedelta(days=2),
                "descripcion": "Revisión toma",
                "cantidad": 1,
                "tarifa_aplicada": Decimal("300.00"),
                "unidad_cobro": "SERVICIO",
                "monto_base": Decimal("300.00"),
                "metodo_pago": "efectivo",
                "monto_ajuste": Decimal("0.00"),
                "monto_total": Decimal("300.00"),
                "estado": "PENDIENTE",
            },
        )
        respond_to_direct_request(rejected_req.id, "CANCELADA", {"uid": tech2.firebase_uid}, self.db)
        self.assertIn(EVENT_SOLICITUD_DIRECTA_RECHAZADA, [n.tipo_evento for n in self._user_notifications(cli)])

        # 5. Licitación abierta: oferta, contraoferta, rechazo, adjudicación y descarte
        open_req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            especialidad_id=str(spec.id),
            municipio="Distrito Central",
            sector="Kennedy",
            direccion_exacta="Casa Roja #12 frente a portón",
            descripcion="Instalación completa",
            fecha_hora=datetime.now(timezone.utc) + timedelta(days=3),
            presupuesto=Decimal("800.00"),
            monto_base=Decimal("800.00"),
            metodo_pago="efectivo",
            estado="Publicada",
            creada_en=datetime.now(timezone.utc),
        )
        self.db.add(open_req)
        self.db.commit()

        accept_published_budget_for_open_request(open_req.id, {"uid": tech1.firebase_uid}, self.db)
        p2 = submit_counter_offer_for_open_request(
            open_req.id,
            CounterOfferSchema(monto=Decimal("950.00"), justificacion="Material extra"),
            {"uid": tech2.firebase_uid},
            self.db,
        )["propuesta"]
        p3 = submit_counter_offer_for_open_request(
            open_req.id,
            CounterOfferSchema(monto=Decimal("900.00"), justificacion="Incluye garantía"),
            {"uid": tech3.firebase_uid},
            self.db,
        )["propuesta"]

        cli_types = [n.tipo_evento for n in self._user_notifications(cli)]
        self.assertIn(EVENT_NUEVA_OFERTA_ABIERTA, cli_types)
        self.assertIn(EVENT_NUEVA_CONTRAOFERTA_ABIERTA, cli_types)
        offer_notif = next(n for n in self._user_notifications(cli) if n.tipo_evento == EVENT_NUEVA_OFERTA_ABIERTA)
        self.assertTrue(offer_notif.metadatos_seguros.get("es_evento_propuesta_vivo"))

        reject_open_request_proposal_by_owner(
            open_req.id, p2["id"], RejectProposalSchema(motivo="Muy alto"), {"uid": cli.firebase_uid}, self.db
        )
        tech2_notifs = self._user_notifications(tech2)
        self.assertIn(EVENT_PROPUESTA_RECHAZADA, [n.tipo_evento for n in tech2_notifs])
        self.assertEqual(tech2_notifs[-1].enlace_ruta, "/tecnico/oportunidades")

        award_open_request_proposal_by_owner(
            open_req.id,
            p3["id"],
            AwardProposalSchema(metodo_pago="efectivo"),
            {"uid": cli.firebase_uid},
            self.db,
        )
        self.assertIn(EVENT_PROPUESTA_ADJUDICADA, [n.tipo_evento for n in self._user_notifications(tech3)])
        t1_discarded = [
            n for n in self._user_notifications(tech1) if n.tipo_evento == EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION
        ]
        self.assertEqual(len(t1_discarded), 1)
        self.assertEqual(t1_discarded[0].enlace_ruta, "/tecnico/oportunidades")

        # 6. Cierre automático 48h e incidencias / cancelaciones
        auto_req = DirectRequestRepository.create_request(
            self.db,
            {
                "id": str(uuid.uuid4()),
                "cliente_id": cli.firebase_uid,
                "tecnico_id": tech1.firebase_uid,
                "servicio_id": str(srv.id),
                "municipio": "Distrito Central",
                "sector": "Hato",
                "direccion_exacta": "Casa 50",
                "fecha_hora": datetime.now(timezone.utc) + timedelta(days=1),
                "descripcion": "Cierre auto",
                "cantidad": 1,
                "tarifa_aplicada": Decimal("400.00"),
                "unidad_cobro": "SERVICIO",
                "monto_base": Decimal("400.00"),
                "metodo_pago": "efectivo",
                "monto_ajuste": Decimal("0.00"),
                "monto_total": Decimal("400.00"),
                "estado": "ACEPTADA",
            },
        )
        start_request_service(self.db, auto_req, uid=tech1.firebase_uid)
        past = datetime.now(timezone.utc) - timedelta(hours=50)
        report_service_completion(auto_req.id, claims={"uid": tech1.firebase_uid}, db=self.db, now=past)
        run_scheduled_auto_completion(self.db, now=datetime.now(timezone.utc))
        self.assertIn(EVENT_CIERRE_AUTOMATICO_48H, [n.tipo_evento for n in self._user_notifications(cli)])
        self.assertIn(EVENT_CIERRE_AUTOMATICO_48H, [n.tipo_evento for n in self._user_notifications(tech1)])

        # Incidencia bilateral y resolución
        inc_req = DirectRequestRepository.create_request(
            self.db,
            {
                "id": str(uuid.uuid4()),
                "cliente_id": cli.firebase_uid,
                "tecnico_id": tech1.firebase_uid,
                "servicio_id": str(srv.id),
                "municipio": "Distrito Central",
                "sector": "Prados",
                "direccion_exacta": "Casa 77",
                "fecha_hora": datetime.now(timezone.utc) + timedelta(days=1),
                "descripcion": "Incidencia",
                "cantidad": 1,
                "tarifa_aplicada": Decimal("450.00"),
                "unidad_cobro": "SERVICIO",
                "monto_base": Decimal("450.00"),
                "metodo_pago": "efectivo",
                "monto_ajuste": Decimal("0.00"),
                "monto_total": Decimal("450.00"),
                "estado": "EN PROCESO",
            },
        )
        ticket = create_incident_ticket(
            CreateIncidentTicketSchema(
                solicitud_id=inc_req.id,
                tipo=TipoIncidenciaEnum.INCUMPLIMIENTO_SERVICIO,
                motivo="Demora injustificada de más de dos horas",
            ),
            {"uid": cli.firebase_uid},
            self.db,
        )
        self.assertIn(EVENT_INCIDENCIA_ABIERTA, [n.tipo_evento for n in self._user_notifications(tech1)])
        self.assertIn(EVENT_INCIDENCIA_ABIERTA, [n.tipo_evento for n in self._user_notifications(admin)])

        resolve_incident_ticket(
            ticket["ticket_id"],
            AdminResolveTicketSchema(
                decision=DecisionResolucionEnum.CANCELAR_SERVICIO,
                observaciones="Se cancela por incumplimiento confirmado.",
            ),
            {"uid": admin.firebase_uid},
            self.db,
        )
        self.assertIn(EVENT_INCIDENCIA_RESUELTA, [n.tipo_evento for n in self._user_notifications(cli)])
        self.assertIn(EVENT_INCIDENCIA_RESUELTA, [n.tipo_evento for n in self._user_notifications(tech1)])

        # Cancelación y rechazo de ajuste
        can_req = DirectRequestRepository.create_request(
            self.db,
            {
                "id": str(uuid.uuid4()),
                "cliente_id": cli.firebase_uid,
                "tecnico_id": tech2.firebase_uid,
                "servicio_id": str(srv.id),
                "municipio": "Distrito Central",
                "sector": "Centro",
                "direccion_exacta": "Calle Real",
                "fecha_hora": datetime.now(timezone.utc) + timedelta(days=1),
                "descripcion": "Ajuste y cancelación",
                "cantidad": 1,
                "tarifa_aplicada": Decimal("500.00"),
                "unidad_cobro": "SERVICIO",
                "monto_base": Decimal("500.00"),
                "metodo_pago": "efectivo",
                "monto_ajuste": Decimal("0.00"),
                "monto_total": Decimal("500.00"),
                "estado": "EN PROCESO",
            },
        )
        request_adjustment(
            self.db,
            can_req.id,
            motivo="Cambio necesario",
            monto_base=Decimal("500.00"),
            nuevo_total=Decimal("700.00"),
            claims={"uid": tech2.firebase_uid},
            policy=adj_policy,
            repository=adj_repo,
        )
        respond_adjustment(
            self.db,
            can_req.id,
            decision=AdjustmentDecision.RECHAZAR,
            comentario="No procede",
            claims={"uid": cli.firebase_uid},
            repository=adj_repo,
        )
        self.assertIn(EVENT_AJUSTE_PRESUPUESTO_RECHAZADO, [n.tipo_evento for n in self._user_notifications(tech2)])

        cancel_request(
            self.db,
            can_req.id,
            causa=CancellationCause.AJUSTE_RECHAZADO,
            motivo="No aceptaron el ajuste",
            evidencias=[],
            claims={"uid": tech2.firebase_uid},
            policy=default_cancellation_policy(),
            repository=SqlAlchemyCancellationRepository(),
            adjustments=adj_repo,
        )
        self.assertIn(EVENT_SERVICIO_CANCELADO, [n.tipo_evento for n in self._user_notifications(cli)])

    def test_ca03_verification_certifications_settlements_payouts_and_operational_restrictions(self):
        admin = _make_user(self.db, "adm-ca03", "ADMIN", 20)
        cli = _make_user(self.db, "cli-pay", "CLIENTE", 25)
        tech = _make_user(
            self.db,
            "tech-ca03",
            "TECNICO",
            21,
            verified=False,
            financially_restricted=True,
        )
        _, srv = _make_catalog(self.db, tech.id, suffix="PAY")
        tech_user = self.db.get(User, tech.id)
        tech_user.estado_identidad = "EN_REVISION"

        ver_req = IdentityVerificationRequest(id=uuid.uuid4(), usuario_id=tech.id, version=1)
        self.db.add(ver_req)
        self.db.flush()

        doc_types = [
            "DNI_FRONTAL",
            "DNI_REVERSO",
            "SELFIE",
            "ANTECEDENTES_POLICIALES",
            "ANTECEDENTES_PENALES",
        ]
        docs = []
        for idx, dtype in enumerate(doc_types):
            doc = IdentityDocument(
                id=uuid.uuid4(),
                usuario_id=tech.id,
                solicitud_id=ver_req.id,
                tipo=dtype,
                ruta_privada=f"private/verif/{tech.id}/{dtype}.jpg",
                content_type="image/jpeg",
                tamano_bytes=1200,
                sha256=f"{idx}" * 64,
                estado="PENDIENTE",
                fecha_emision_declarada=datetime.now(timezone.utc).date() - timedelta(days=5),
            )
            self.db.add(doc)
            docs.append(doc)
        cert_ok = TradeCertificate(
            id=uuid.uuid4(),
            usuario_id=tech.id,
            titulo="Técnico Electricista Certificado",
            ruta_privada="private/certs/ok.pdf",
            content_type="application/pdf",
            estado="PENDIENTE",
        )
        cert_bad = TradeCertificate(
            id=uuid.uuid4(),
            usuario_id=tech.id,
            titulo="Diploma borroso",
            ruta_privada="private/certs/bad.pdf",
            content_type="application/pdf",
            estado="PENDIENTE",
        )
        # Deuda por servicio en efectivo completado + saldo digital acreditado para desembolso
        cash_debt_job = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tech.firebase_uid,
            servicio_id=str(srv.id),
            municipio="Distrito Central",
            sector="Centro",
            direccion_exacta="Casa 2",
            fecha_hora=datetime.now(timezone.utc) - timedelta(days=2),
            descripcion="Servicio efectivo con comisión pendiente",
            cantidad=1,
            tarifa_aplicada=Decimal("2500.00"),
            unidad_cobro="SERVICIO",
            monto_base=Decimal("2500.00"),
            metodo_pago="efectivo",
            monto_ajuste=Decimal("0.00"),
            monto_total=Decimal("2500.00"),
            estado="COMPLETADA",
            seguimiento_recepcion={
                "completada": True,
                "efecto_financiero": {"deuda_comision_efectivo": 250.0},
            },
        )
        paid_job = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tech.firebase_uid,
            servicio_id=str(srv.id),
            municipio="Distrito Central",
            sector="Lomas",
            direccion_exacta="Casa 1",
            fecha_hora=datetime.now(timezone.utc),
            descripcion="Pago tarjeta",
            cantidad=1,
            tarifa_aplicada=Decimal("1000.00"),
            unidad_cobro="SERVICIO",
            monto_base=Decimal("1000.00"),
            metodo_pago="tarjeta",
            referencia_pasarela="PAY-REF-999",
            monto_ajuste=Decimal("0.00"),
            monto_total=Decimal("1000.00"),
            estado="COMPLETADA",
            seguimiento_recepcion={"completada": True, "efecto_financiero": {"credito_digital_acreditado": 900.0}},
        )
        self.db.add_all([cert_ok, cert_bad, cash_debt_job, paid_job])
        self.db.commit()

        save_my_bank_account(
            BankAccountInput(
                banco="BAC Honduras",
                tipo_cuenta="AHORRO",
                titular_nombre="Persona tech-ca03",
                numero_cuenta="99887766554433",
            ),
            {"uid": tech.firebase_uid},
            self.db,
        )

        # 1. Aprobación documental completa -> VERIFICACION_APROBADA
        review_payload = AdminVerificationReviewSchema(
            observaciones_verificacion="Todo en orden",
            documentos=[
                DocumentReviewItem(
                    documento_id=d.id,
                    estado="APROBADO",
                    legible=True,
                    identidad_coincide=True if idx < 3 else None,
                    fecha_vencimiento=datetime.now(timezone.utc).date() + timedelta(days=60),
                )
                for idx, d in enumerate(docs)
            ],
        )
        apply_technician_review(tech.id, review_payload, {"uid": admin.firebase_uid}, self.db)
        self.assertIn(EVENT_VERIFICACION_APROBADA, [n.tipo_evento for n in self._user_notifications(tech)])

        # 2. Certificaciones aprobada y rechazada
        review_certificate(cert_ok.id, CertificateDecision(estado="APROBADO"), {"uid": admin.firebase_uid}, self.db)
        review_certificate(
            cert_bad.id,
            CertificateDecision(estado="RECHAZADO", observaciones="Documento ilegible"),
            {"uid": admin.firebase_uid},
            self.db,
        )
        tech_types = [n.tipo_evento for n in self._user_notifications(tech)]
        self.assertIn(EVENT_CERTIFICACION_APROBADA, tech_types)
        self.assertIn(EVENT_CERTIFICACION_RECHAZADA, tech_types)

        # 3. Liquidaciones: envío notifica a admin; rechazo mantiene deuda -> avisa rechazo + restricción
        store = PrivateDocumentStorage()
        s1 = submit_settlement(
            importe="250.00",
            referencia="TX-BANCO-998877",
            comprobante=UploadedPart(content_type="image/jpeg", content=JPEG),
            claims={"uid": tech.firebase_uid},
            db=self.db,
            storage=store,
        )
        self.assertIn(EVENT_LIQUIDACION_RECIBIDA, [n.tipo_evento for n in self._user_notifications(admin)])

        decide_settlement(
            uuid.UUID(s1["id"]),
            SettlementDecisionSchema(decision="RECHAZAR", observacion="Comprobante ilegible"),
            {"uid": admin.firebase_uid},
            self.db,
        )
        tech_types = [n.tipo_evento for n in self._user_notifications(tech)]
        self.assertIn(EVENT_LIQUIDACION_RECHAZADA, tech_types)
        self.assertIn(EVENT_RESTRICCION_OPERATIVA, tech_types)

        # Liquidación aprobada desbloqueando restricción
        s2 = submit_settlement(
            importe="250.00",
            referencia="TX-BANCO-112233",
            comprobante=UploadedPart(content_type="image/jpeg", content=JPEG + b"\x00"),
            claims={"uid": tech.firebase_uid},
            db=self.db,
            storage=store,
        )
        decide_settlement(
            uuid.UUID(s2["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="250.00"),
            {"uid": admin.firebase_uid},
            self.db,
        )
        self.assertIn(EVENT_LIQUIDACION_APROBADA, [n.tipo_evento for n in self._user_notifications(tech)])

        # 4. Desembolsos: solicitud -> admin; confirmación y rechazo -> técnico
        p1_res = request_payout(PayoutRequestInput(importe=Decimal("300.00")), {"uid": tech.firebase_uid}, self.db)
        self.assertIn(EVENT_DESEMBOLSO_SOLICITADO, [n.tipo_evento for n in self._user_notifications(admin)])
        decide_payout(
            uuid.UUID(p1_res["orden"]["id"]),
            PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF998877001"),
            {"uid": admin.firebase_uid},
            self.db,
        )
        p2_res = request_payout(PayoutRequestInput(importe=Decimal("250.00")), {"uid": tech.firebase_uid}, self.db)
        decide_payout(
            uuid.UUID(p2_res["orden"]["id"]),
            PayoutDecisionInput(decision="RECHAZAR", motivo_rechazo="Cuenta temporalmente inactiva"),
            {"uid": admin.firebase_uid},
            self.db,
        )
        tech_types = [n.tipo_evento for n in self._user_notifications(tech)]
        self.assertIn(EVENT_DESEMBOLSO_PAGADO, tech_types)
        self.assertIn(EVENT_DESEMBOLSO_RECHAZADO, tech_types)

        # 5. Restricción automática por vencimiento documental
        docs[3].fecha_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=1)
        self.db.commit()
        run_scheduled_expiration_review(self.db)
        self.db.commit()
        exp_alerts = [
            n
            for n in self._user_notifications(tech)
            if n.tipo_evento == EVENT_RESTRICCION_OPERATIVA and n.enlace_ruta == "/verificacion"
        ]
        self.assertGreaterEqual(len(exp_alerts), 1)

    def test_ca04_strict_idempotency_db_constraints_and_outbox_retry_recovery(self):
        user = _make_user(self.db, "cli-idem", "CLIENTE", 30)
        self.db.commit()

        attempts = 0

        def flaky_hook(event, items):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Fallo temporal de entrega")

        evt = enqueue_notification_event(
            self.db,
            clave_evento="evento-unico-ca04:1",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            destinatarios=[(user.id, "CLIENTE"), (user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "sol-ca04", "modalidad": "DIRECTA"},
        )
        self.db.flush()
        self.assertIsNotNone(evt)
        # Re-encolar misma clave devuelve exactamente el mismo outbox event sin duplicar
        evt_dup = enqueue_notification_event(
            self.db,
            clave_evento="evento-unico-ca04:1",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            destinatarios=[(user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "sol-ca04", "modalidad": "DIRECTA"},
        )
        self.assertEqual(evt_dup.id, evt.id)
        self.db.info.pop("_pending_notification_event_ids", None)
        self.db.commit()

        # Primer intento falla -> outbox queda FALLIDO y sin filas en bandeja
        res1 = process_pending_notification_events(self.db, broadcaster=None, delivery_hook=flaky_hook)
        self.assertEqual(res1["fallidos"], 1)
        self.assertEqual(len(self._user_notifications(user)), 0)
        refreshed_evt = self.db.get(NotificationEvent, evt.id)
        self.assertEqual(refreshed_evt.estado, "FALLIDO")
        self.assertEqual(refreshed_evt.intentos, 1)

        # Segundo intento (reintento persistente) -> PROCESADO, 1 sola notificación
        res2 = process_pending_notification_events(self.db, broadcaster=None, delivery_hook=flaky_hook)
        self.assertEqual(res2["procesados"], 1)
        self.assertEqual(res2["notificaciones_creadas"], 1)
        self.assertEqual(len(self._user_notifications(user)), 1)

        # Tercer reintento no reprocesa ni duplica
        res3 = process_pending_notification_events(self.db, broadcaster=None, delivery_hook=flaky_hook)
        self.assertEqual(res3["evaluados"], 0)
        self.assertEqual(len(self._user_notifications(user)), 1)

        # Restricción UNIQUE real en base de datos
        dup_row = UserNotification(
            id=uuid.uuid4(),
            evento_id=evt.id,
            destinatario_id=user.id,
            clave_idempotencia="clave-distinta-mismo-evento",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            titulo="Dup",
            resumen="Dup",
            enlace_ruta="/mis-trabajos/sol-ca04",
            metadatos_seguros={},
        )
        self.db.add(dup_row)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_ca05_live_websocket_delivery_and_reconnect_recovery_with_cursors(self):
        user = _make_user(self.db, "cli-ws", "CLIENTE", 40)
        self.db.commit()

        t1 = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 25, 14, 5, tzinfo=timezone.utc)
        t3 = datetime(2026, 11, 25, 14, 10, tzinfo=timezone.utc)

        enqueue_notification_event(
            self.db,
            clave_evento="ws-evt-1",
            tipo_evento=EVENT_NUEVA_OFERTA_ABIERTA,
            destinatarios=[(user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "open-1", "monto": 500, "es_evento_propuesta_vivo": True},
            now=t1,
        )
        self.db.commit()
        first_id = str(self._user_notifications(user)[0].id)

        # 1. Token inválido cierra con 4001; usuario no registrado cierra con 4003
        ws_unauth = FakeWebSocket()
        _run_coro(
            notifications_websocket_endpoint(
                ws_unauth,
                token=None,
                after_id=None,
                since=None,
                after=None,
                db=self.db,
            )
        )
        self.assertEqual(ws_unauth.close_code, WS_CLOSE_UNAUTHORIZED)

        ws_unknown = FakeWebSocket()
        with patch("app.notifications_router.verify_notification_ws_token", return_value={"uid": "desconocido"}):
            _run_coro(
                notifications_websocket_endpoint(
                    ws_unknown,
                    token="tok",
                    after_id=None,
                    since=None,
                    after=None,
                    db=self.db,
                )
            )
        self.assertEqual(ws_unknown.close_code, WS_CLOSE_FORBIDDEN)

        # 2. Se emiten eventos mientras el usuario está desconectado
        enqueue_notification_event(
            self.db,
            clave_evento="ws-evt-2",
            tipo_evento=EVENT_NUEVA_CONTRAOFERTA_ABIERTA,
            destinatarios=[(user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "open-1", "monto": 650, "es_evento_propuesta_vivo": True},
            now=t2,
        )
        enqueue_notification_event(
            self.db,
            clave_evento="ws-evt-3",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            destinatarios=[(user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": "dir-1", "modalidad": "DIRECTA"},
            now=t3,
        )
        self.db.commit()

        # 3. Recuperación REST con after_id y Last-Event-ID sin perder avisos
        recovered_rest = list_notifications_endpoint(
            Response(),
            limit=30,
            offset=0,
            after_id=None,
            since=None,
            after=None,
            solo_no_leidas=False,
            last_event_id=first_id,
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(len(recovered_rest["notificaciones"]), 2)
        self.assertEqual(
            [n["tipo_evento"] for n in recovered_rest["notificaciones"]],
            [EVENT_NUEVA_CONTRAOFERTA_ABIERTA, EVENT_SERVICIO_INICIADO],
        )

        # 4. Recuperación vía WebSocket al reconectar (connected + sync + ping/pong + sync)
        ws_reconnect = FakeWebSocket(incoming=[{"type": "ping"}, {"type": "sync", "after_id": first_id}])
        with patch("app.notifications_router.verify_notification_ws_token", return_value={"uid": user.firebase_uid}):
            _run_coro(
                notifications_websocket_endpoint(
                    ws_reconnect,
                    token="valid",
                    after_id=first_id,
                    since=None,
                    after=None,
                    db=self.db,
                )
            )
        types_sent = [frame["type"] for frame in ws_reconnect.sent]
        self.assertEqual(types_sent, ["connected", "sync", "pong", "sync"])
        self.assertEqual(len(ws_reconnect.sent[1]["notificaciones"]), 2)
        self.assertEqual(ws_reconnect.sent[2]["no_leidas"], 3)

        # 5. Difusión en vivo sobre socket registrado
        live_ws = FakeWebSocket()

        async def test_live_push():
            await notification_manager.register(str(user.id), live_ws)
            await notification_manager.broadcast_notifications([recovered_rest["notificaciones"][0]])
            notification_manager.disconnect(str(user.id), live_ws)

        _run_coro(test_live_push())
        self.assertEqual(len(live_ws.sent), 1)
        self.assertEqual(live_ws.sent[0]["type"], "notification")
        self.assertEqual(live_ws.sent[0]["notificacion"]["tipo_evento"], EVENT_NUEVA_CONTRAOFERTA_ABIERTA)

    def test_ca06_strict_privacy_sanitizer_blocks_dni_accounts_paths_receipts_and_addresses(self):
        secret_user = _make_user(
            self.db,
            "cli-priv",
            "CLIENTE",
            99,
            dni="0801-1992-54321",
            phone="+50499887766",
        )
        self.db.commit()

        enqueue_notification_event(
            self.db,
            clave_evento="priv-test-1",
            tipo_evento=EVENT_CERTIFICACION_RECHAZADA,
            destinatarios=[(secret_user.id, "TECNICO")],
            payload_seguro={
                "certificacion_id": "cert-1",
                "titulo_certificado": (
                    "DNI 0801-1992-54321 cuenta 20001234567890 https://bucket.s3.amazonaws.com/dni.jpg "
                    "Colonia Palmira Casa 14 tel +50499887766"
                ),
                "dni": "0801199254321",
                "numero_cuenta": "20001234567890",
                "cuenta_enmascarada": "****7890",
                "ruta_privada": "private/secret/dni.pdf",
                "referencia_transferencia": "TRF-99887766",
                "direccion_exacta": "Residencial Las Uvas Casa 88 portón azul",
                "enlace_ruta": "https://evil.example/phish",
            },
        )
        self.db.commit()

        notif = self._user_notifications(secret_user)[0]
        serialized = json.dumps(
            {
                "titulo": notif.titulo,
                "resumen": notif.resumen,
                "enlace_ruta": notif.enlace_ruta,
                "metadatos_seguros": notif.metadatos_seguros,
            },
            ensure_ascii=False,
        )
        for forbidden in (
            "0801-1992-54321",
            "0801199254321",
            "20001234567890",
            "****7890",
            "private/secret/dni.pdf",
            "https://bucket.s3.amazonaws.com",
            "TRF-99887766",
            "Residencial Las Uvas",
            "+50499887766",
            "evil.example",
        ):
            self.assertNotIn(forbidden, serialized)

        # Validación de sanitización de rutas externas / rutas administrativas privadas
        self.assertEqual(NotificationPrivacySanitizer.validate_internal_route("//evil.com"), "/mi-cuenta")
        self.assertEqual(NotificationPrivacySanitizer.validate_internal_route("https://phish.test"), "/mi-cuenta")
        self.assertEqual(
            NotificationPrivacySanitizer.validate_internal_route("/admin/dossiers/123/content"),
            "/mi-cuenta",
        )
        self.assertEqual(
            NotificationPrivacySanitizer.validate_internal_route("/admin?tab=settlements"),
            "/admin?tab=settlements",
        )


if __name__ == "__main__":
    unittest.main()
