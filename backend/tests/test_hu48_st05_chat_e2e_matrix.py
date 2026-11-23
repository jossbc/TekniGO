"""HU-48 ST-05: matriz integral de usuarios ajenos, orden, duplicados/reconexión, errores de persistencia y cambio de estado en línea."""
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException, Response, WebSocketDisconnect
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.chat import ChatUnauthorizedError
from app.chat_realtime import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_STATE_DISABLED,
    WS_CLOSE_UNAUTHORIZED,
    chat_manager,
)
from app.chat_router import (
    ChatMessageCreateInput,
    chat_websocket_endpoint,
    get_conversation_messages,
    post_conversation_message,
)
from app.database import Base
from app.models import (
    ContractChatMessage,
    DirectRequest,
    OpenRequest,
    OpenRequestProposal,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.request_states import transition_request


def _run_coro(coro):
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def _make_user(db: Session, uid: str, role: str, n: int) -> User:
    user = User(
        id=uuid.uuid4(),
        firebase_uid=uid,
        nombre_completo=f"Usuario {uid}",
        correo=f"{uid}@example.com",
        dni=f"08012000003{n:02d}",
        telefono=f"+5046{n:07d}",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(usuario_id=user.id, rol=role))
    if role == "TECNICO":
        db.add(TechnicianProfile(usuario_id=user.id, disponible=True, estado_verificacion="VERIFICADO"))
    db.flush()
    return user


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


class HU48ST05ChatE2EMatrixTests(unittest.TestCase):
    def setUp(self):
        chat_manager.reset()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.owner_client = _make_user(self.db, "cli-st05-owner", "CLIENTE", 1)
        self.selected_tech = _make_user(self.db, "tech-st05-selected", "TECNICO", 2)
        self.rejected_applicant = _make_user(self.db, "tech-st05-rejected", "TECNICO", 3)
        self.other_client = _make_user(self.db, "cli-st05-other", "CLIENTE", 4)
        self.other_tech = _make_user(self.db, "tech-st05-other", "TECNICO", 5)
        self.admin_user = _make_user(self.db, "adm-st05", "ADMIN", 6)

        self.specialty = Specialty(id=uuid.uuid4(), nombre="Carpintería", activa=True)
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            id=uuid.uuid4(),
            usuario_id=self.selected_tech.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación de puertas",
            descripcion="Ajuste e instalación",
            tarifa_base=Decimal("420.00"),
            unidad_cobro="POR_HORA",
        )
        self.db.add(self.service)
        self.db.flush()

        self.direct_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.owner_client.firebase_uid,
            tecnico_id=self.selected_tech.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Col. Lomas del Guijarro",
            direccion_exacta="Casa 10",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=2),
            descripcion="Puerta principal desajustada",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("420.00"),
            unidad_cobro="POR_HORA",
            monto_base=Decimal("420.00"),
            metodo_pago="efectivo",
            monto_total=Decimal("420.00"),
            estado="ACEPTADA",
        )
        self.open_req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.owner_client.firebase_uid,
            tecnico_id=self.selected_tech.firebase_uid,
            especialidad_id=self.specialty.id,
            municipio="Tegucigalpa",
            sector="Col. Florencia",
            direccion_exacta="Casa 25",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=3),
            descripcion="Mueble de cocina",
            presupuesto=Decimal("800.00"),
            monto_base=Decimal("800.00"),
            metodo_pago="tarjeta",
            monto_total=Decimal("800.00"),
            estado="ACEPTADA",
        )
        self.other_direct_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.other_client.firebase_uid,
            tecnico_id=self.other_tech.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Col. Kennedy",
            direccion_exacta="Bloque A",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=2),
            descripcion="Otra contratación independiente",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("420.00"),
            unidad_cobro="POR_HORA",
            monto_base=Decimal("420.00"),
            metodo_pago="efectivo",
            monto_total=Decimal("420.00"),
            estado="EN PROCESO",
        )
        self.db.add_all([self.direct_req, self.open_req, self.other_direct_req])
        self.db.flush()
        self.db.add(
            OpenRequestProposal(
                solicitud_id=self.open_req.id,
                usuario_id=self.rejected_applicant.id,
                tecnico_id=self.rejected_applicant.firebase_uid,
                monto=Decimal("790.00"),
                estado="RECHAZADA",
                activa=False,
            )
        )
        self.db.commit()

    def tearDown(self):
        chat_manager.reset()
        self.db.close()
        self.engine.dispose()

    def _count_messages(self, modalidad: str, solicitud_id: str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(ContractChatMessage)
                .where(
                    ContractChatMessage.modalidad == modalidad,
                    ContractChatMessage.solicitud_id == solicitud_id,
                )
            )
            or 0
        )

    # CA-01, CA-02, CA-06: matriz de usuarios ajenos en REST GET/POST y WebSocket para DIRECTA y ABIERTA
    def test_outsiders_matrix_blocked_across_rest_and_websocket(self):
        # Mensaje privado en otra solicitud no debe filtrarse ni ser accesible
        post_conversation_message(
            modalidad="DIRECTA",
            solicitud_id=self.other_direct_req.id,
            payload=ChatMessageCreateInput(contenido="Secreto de otra conversación"),
            response=Response(),
            claims={"uid": self.other_client.firebase_uid},
            db=self.db,
        )

        targets = [
            ("DIRECTA", self.direct_req.id),
            ("ABIERTA", self.open_req.id),
        ]
        outsiders = [
            ("postulante_no_elegido", self.rejected_applicant),
            ("otro_cliente", self.other_client),
            ("otro_tecnico", self.other_tech),
            ("admin", self.admin_user),
        ]

        for modalidad, solicitud_id in targets:
            # 1) Visitante sin token y con token revocado/inválido en WS -> 4001
            ws_missing = FakeWebSocket()
            _run_coro(chat_websocket_endpoint(ws_missing, modalidad, solicitud_id, token=None, after=None, db=self.db))
            self.assertFalse(ws_missing.accepted)
            self.assertEqual(ws_missing.close_code, WS_CLOSE_UNAUTHORIZED)

            ws_invalid = FakeWebSocket()
            with patch("app.chat.verify_websocket_token", side_effect=ChatUnauthorizedError("Token revocado")):
                _run_coro(
                    chat_websocket_endpoint(ws_invalid, modalidad, solicitud_id, token="bad-tok", after=None, db=self.db)
                )
            self.assertFalse(ws_invalid.accepted)
            self.assertEqual(ws_invalid.close_code, WS_CLOSE_UNAUTHORIZED)

            # 2) Todos los usuarios ajenos -> 403 en GET/POST y 4003 en WS
            for label, outsider in outsiders:
                with self.assertRaises(HTTPException, msg=f"{label} GET {modalidad}") as ctx_get:
                    get_conversation_messages(
                        modalidad=modalidad,
                        solicitud_id=solicitud_id,
                        response=Response(),
                        after=None,
                        limit=50,
                        claims={"uid": outsider.firebase_uid},
                        db=self.db,
                    )
                self.assertEqual(ctx_get.exception.status_code, 403)

                with self.assertRaises(HTTPException, msg=f"{label} POST {modalidad}") as ctx_post:
                    post_conversation_message(
                        modalidad=modalidad,
                        solicitud_id=solicitud_id,
                        payload=ChatMessageCreateInput(contenido=f"Intrusión de {label}"),
                        response=Response(),
                        claims={"uid": outsider.firebase_uid},
                        db=self.db,
                    )
                self.assertEqual(ctx_post.exception.status_code, 403)

                ws_out = FakeWebSocket()
                with patch("app.chat.verify_websocket_token", return_value={"uid": outsider.firebase_uid}):
                    _run_coro(
                        chat_websocket_endpoint(
                            ws_out, modalidad, solicitud_id, token="tok", after=None, db=self.db
                        )
                    )
                self.assertFalse(ws_out.accepted, msg=f"{label} WS {modalidad} no debe aceptarse")
                self.assertEqual(ws_out.close_code, WS_CLOSE_FORBIDDEN)

            self.assertEqual(self._count_messages(modalidad, solicitud_id), 0)

    # CA-03: orden idéntico en ambas sesiones incluyendo empate de timestamp
    def test_deterministic_ordering_identical_for_both_sessions_including_timestamp_ties(self):
        same_ts = datetime(2026, 11, 23, 19, 0, 0, tzinfo=timezone.utc)
        id_a = uuid.UUID("00000000-0000-0000-0000-000000000001")
        id_b = uuid.UUID("00000000-0000-0000-0000-000000000002")
        id_c = uuid.UUID("00000000-0000-0000-0000-000000000003")

        # Insertados en orden inverso y con empate de timestamp entre id_a e id_b
        self.db.add_all(
            [
                ContractChatMessage(
                    id=id_c,
                    modalidad="ABIERTA",
                    solicitud_id=self.open_req.id,
                    autor_id=self.owner_client.id,
                    autor_uid=self.owner_client.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Tercero",
                    creado_en=same_ts + timedelta(seconds=5),
                ),
                ContractChatMessage(
                    id=id_b,
                    modalidad="ABIERTA",
                    solicitud_id=self.open_req.id,
                    autor_id=self.selected_tech.id,
                    autor_uid=self.selected_tech.firebase_uid,
                    rol_autor="TECNICO",
                    contenido="Segundo (empate ts)",
                    creado_en=same_ts,
                ),
                ContractChatMessage(
                    id=id_a,
                    modalidad="ABIERTA",
                    solicitud_id=self.open_req.id,
                    autor_id=self.owner_client.id,
                    autor_uid=self.owner_client.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Primero (empate ts)",
                    creado_en=same_ts,
                ),
            ]
        )
        self.db.commit()

        client_view = get_conversation_messages(
            modalidad="ABIERTA",
            solicitud_id=self.open_req.id,
            response=Response(),
            after=None,
            limit=50,
            claims={"uid": self.owner_client.firebase_uid},
            db=self.db,
        )
        tech_view = get_conversation_messages(
            modalidad="ABIERTA",
            solicitud_id=self.open_req.id,
            response=Response(),
            after=None,
            limit=50,
            claims={"uid": self.selected_tech.firebase_uid},
            db=self.db,
        )

        expected = ["Primero (empate ts)", "Segundo (empate ts)", "Tercero"]
        self.assertEqual([m["contenido"] for m in client_view["mensajes"]], expected)
        self.assertEqual([m["contenido"] for m in tech_view["mensajes"]], expected)
        self.assertEqual(client_view["mensajes"], tech_view["mensajes"])

    # CA-04 & CA-06: deduplicación cruzada REST/WS y sin entrega en vivo ante fallo de persistencia
    def test_cross_channel_deduplication_and_no_live_delivery_on_persistence_failure(self):
        peer_ws = FakeWebSocket()
        peer_ws.accepted = True
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, peer_ws))

        # 1) Fallo de persistencia en BD -> NO se difunde en vivo al peer (CA-06)
        with patch.object(self.db, "commit", side_effect=OperationalError("INSERT", {}, Exception("db down"))):
            with self.assertRaises(OperationalError):
                post_conversation_message(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    payload=ChatMessageCreateInput(
                        contenido="Mensaje durante caída de BD",
                        client_message_id="cid-retry-1",
                    ),
                    response=Response(),
                    claims={"uid": self.owner_client.firebase_uid},
                    db=self.db,
                )
        self.db.rollback()
        self.assertEqual(len(peer_ws.sent), 0)
        self.assertEqual(self._count_messages("DIRECTA", self.direct_req.id), 0)

        # 2) Reintento posterior por REST con el mismo client_message_id persiste y entrega 1 vez
        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            res_ok = Response()
            sent_rest = post_conversation_message(
                modalidad="DIRECTA",
                solicitud_id=self.direct_req.id,
                payload=ChatMessageCreateInput(
                    contenido="Mensaje durante caída de BD",
                    client_message_id="cid-retry-1",
                ),
                response=res_ok,
                claims={"uid": self.owner_client.firebase_uid},
                db=self.db,
            )
        self.assertEqual(res_ok.status_code, 201)
        self.assertEqual(len(peer_ws.sent), 1)
        self.assertEqual(self._count_messages("DIRECTA", self.direct_req.id), 1)

        # 3) Reintento del mismo client_message_id por WebSocket tras reconectar no duplica en BD ni al peer
        retry_ws = FakeWebSocket(
            headers={"Authorization": "Bearer tok-cli"},
            incoming=[
                {
                    "type": "send",
                    "contenido": "Mensaje durante caída de BD",
                    "client_message_id": "cid-retry-1",
                }
            ],
        )
        with patch("app.chat.verify_websocket_token", return_value={"uid": self.owner_client.firebase_uid}):
            _run_coro(
                chat_websocket_endpoint(
                    retry_ws, "DIRECTA", self.direct_req.id, token=None, after=None, db=self.db
                )
            )
        self.assertEqual(retry_ws.sent[1]["duplicado"], True)
        self.assertEqual(retry_ws.sent[1]["mensaje"]["id"], sent_rest["mensaje"]["id"])
        self.assertEqual(len(peer_ws.sent), 1)
        self.assertEqual(self._count_messages("DIRECTA", self.direct_req.id), 1)

    # CA-01 & CA-05: cambio de estado en línea (ACEPTADA -> EN PROCESO sigue vivo; -> COMPLETADA/CANCELADA cierra ambas sesiones)
    def test_online_state_transitions_keep_active_then_close_both_live_sessions(self):
        client_ws = FakeWebSocket()
        tech_ws = FakeWebSocket()
        client_ws.accepted = tech_ws.accepted = True
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, client_ws))
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, tech_ws))

        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            # ACEPTADA -> EN PROCESO mantiene ambas sesiones abiertas y operativas
            transition_request(
                self.db,
                self.direct_req,
                "EN PROCESO",
                role="TECNICO",
                uid=self.selected_tech.firebase_uid,
            )
            self.db.commit()

            self.assertFalse(client_ws.closed)
            self.assertFalse(tech_ws.closed)
            self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 2)

            post_conversation_message(
                modalidad="DIRECTA",
                solicitud_id=self.direct_req.id,
                payload=ChatMessageCreateInput(contenido="Ya inicié el trabajo"),
                response=Response(),
                claims={"uid": self.selected_tech.firebase_uid},
                db=self.db,
            )
            self.assertEqual(len(client_ws.sent), 1)
            self.assertEqual(len(tech_ws.sent), 1)

            # EN PROCESO -> COMPLETADA cierra inmediatamente ambas sesiones abiertas
            transition_request(
                self.db,
                self.direct_req,
                "COMPLETADA",
                role="CLIENTE",
                uid=self.owner_client.firebase_uid,
            )
            self.db.commit()

        for ws in (client_ws, tech_ws):
            self.assertTrue(ws.closed)
            self.assertEqual(ws.close_code, WS_CLOSE_STATE_DISABLED)
            self.assertEqual(ws.sent[-1]["type"], "chat_closed")
            self.assertEqual(ws.sent[-1]["estado"], "COMPLETADA")
        self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 0)

        # Ninguna de las dos partes puede leer ni enviar tras el cierre en línea
        for participant in (self.owner_client, self.selected_tech):
            with self.assertRaises(HTTPException) as ctx_r:
                get_conversation_messages(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    response=Response(),
                    after=None,
                    limit=50,
                    claims={"uid": participant.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(ctx_r.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx_w:
                post_conversation_message(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    payload=ChatMessageCreateInput(contenido="Ya no debe entrar"),
                    response=Response(),
                    claims={"uid": participant.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(ctx_w.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
