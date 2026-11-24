"""HU-48 ST-02: endpoints de envío REST, idempotencia y canal WebSocket en vivo con validación por estado."""
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException, Response, WebSocketDisconnect
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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
    """Ejecuta corrutinas puras sin abrir sockets locales del event loop."""
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
        dni=f"08012000001{n:02d}",
        telefono=f"+5048{n:07d}",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(usuario_id=user.id, rol=role))
    if role == "TECNICO":
        db.add(TechnicianProfile(usuario_id=user.id, disponible=True, estado_verificacion="VERIFICADO"))
    db.flush()
    return user


class FakeWebSocket:
    """Simulador ligero de WebSocket ASGI para pruebas deterministas y rápidas."""

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


class ChatEndpointsAndLiveChannelTests(unittest.TestCase):
    def setUp(self):
        chat_manager.reset()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.owner_client = _make_user(self.db, "cli-chat-1", "CLIENTE", 1)
        self.selected_tech = _make_user(self.db, "tech-chat-1", "TECNICO", 2)
        self.rejected_tech = _make_user(self.db, "tech-chat-2", "TECNICO", 3)
        self.other_user = _make_user(self.db, "cli-chat-2", "CLIENTE", 4)
        self.admin_user = _make_user(self.db, "adm-chat-1", "ADMIN", 5)

        self.specialty = Specialty(id=uuid.uuid4(), nombre="Plomería", activa=True)
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            id=uuid.uuid4(),
            usuario_id=self.selected_tech.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación de tubería",
            descripcion="Servicio de plomería",
            tarifa_base=Decimal("350.00"),
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
            sector="Col. Miramontes",
            direccion_exacta="Casa 45",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=2),
            descripcion="Fuga en lavamanos",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("350.00"),
            unidad_cobro="POR_HORA",
            monto_base=Decimal("350.00"),
            metodo_pago="efectivo",
            monto_total=Decimal("350.00"),
            estado="EN PROCESO",
        )
        self.open_req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.owner_client.firebase_uid,
            tecnico_id=self.selected_tech.firebase_uid,
            especialidad_id=self.specialty.id,
            municipio="Tegucigalpa",
            sector="Col. Lara",
            direccion_exacta="Bloque C",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=4),
            descripcion="Instalar bomba",
            presupuesto=Decimal("600.00"),
            monto_base=Decimal("600.00"),
            metodo_pago="tarjeta",
            monto_total=Decimal("600.00"),
            estado="ACEPTADA",
        )
        self.db.add_all([self.direct_req, self.open_req])
        self.db.flush()
        self.db.add(
            OpenRequestProposal(
                solicitud_id=self.open_req.id,
                usuario_id=self.rejected_tech.id,
                tecnico_id=self.rejected_tech.firebase_uid,
                monto=Decimal("580.00"),
                estado="RECHAZADA",
                activa=False,
            )
        )
        self.db.commit()

    def tearDown(self):
        chat_manager.reset()
        self.db.close()
        self.engine.dispose()

    def _message_count(self, modalidad: str, solicitud_id: str) -> int:
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

    # CA-01, CA-03, CA-04, CA-06: envío REST, orden, idempotencia y caché privada
    def test_rest_post_persists_ordered_messages_and_deduplicates_client_id(self):
        res1 = Response()
        body1 = post_conversation_message(
            modalidad="DIRECTA",
            solicitud_id=self.direct_req.id,
            payload=ChatMessageCreateInput(
                contenido="  Hola, ya estoy en casa  ",
                client_message_id="cid-001",
            ),
            response=res1,
            claims={"uid": self.owner_client.firebase_uid},
            db=self.db,
        )
        self.assertEqual(res1.status_code, 201)
        self.assertEqual(res1.headers.get("cache-control"), "no-store, private")
        self.assertFalse(body1["duplicado"])
        self.assertEqual(body1["mensaje"]["contenido"], "Hola, ya estoy en casa")
        self.assertEqual(body1["mensaje"]["rol_autor"], "CLIENTE")

        # Reintento con mismo client_message_id no duplica (CA-04)
        res_dup = Response()
        body_dup = post_conversation_message(
            modalidad="DIRECTA",
            solicitud_id=self.direct_req.id,
            payload=ChatMessageCreateInput(
                contenido="Hola, ya estoy en casa",
                client_message_id="cid-001",
            ),
            response=res_dup,
            claims={"uid": self.owner_client.firebase_uid},
            db=self.db,
        )
        self.assertEqual(res_dup.status_code, 200)
        self.assertTrue(body_dup["duplicado"])
        self.assertEqual(body_dup["mensaje"]["id"], body1["mensaje"]["id"])
        self.assertEqual(self._message_count("DIRECTA", self.direct_req.id), 1)

        # Respuesta del técnico seleccionado
        res2 = Response()
        body2 = post_conversation_message(
            modalidad="DIRECTA",
            solicitud_id=self.direct_req.id,
            payload=ChatMessageCreateInput(contenido="Llego en 10 minutos"),
            response=res2,
            claims={"uid": self.selected_tech.firebase_uid},
            db=self.db,
        )
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(body2["mensaje"]["rol_autor"], "TECNICO")

        # Ambos ven los mismos mensajes en orden
        get_res = Response()
        history = get_conversation_messages(
            modalidad="DIRECTA",
            solicitud_id=self.direct_req.id,
            response=get_res,
            after=None,
            limit=200,
            claims={"uid": self.selected_tech.firebase_uid},
            db=self.db,
        )
        self.assertEqual(
            [m["contenido"] for m in history["mensajes"]],
            ["Hola, ya estoy en casa", "Llego en 10 minutos"],
        )

    # CA-02, CA-03, CA-05: bloqueo de envío vacío, no autorizados y estados cerrados
    def test_rest_post_rejects_empty_unauthorized_and_closed_requests(self):
        with self.assertRaises(HTTPException) as ctx_empty:
            post_conversation_message(
                modalidad="DIRECTA",
                solicitud_id=self.direct_req.id,
                payload=ChatMessageCreateInput(contenido="   "),
                response=Response(),
                claims={"uid": self.owner_client.firebase_uid},
                db=self.db,
            )
        self.assertEqual(ctx_empty.exception.status_code, 400)

        for forbidden in (self.rejected_tech, self.other_user, self.admin_user):
            with self.assertRaises(HTTPException) as ctx_forb:
                post_conversation_message(
                    modalidad="ABIERTA",
                    solicitud_id=self.open_req.id,
                    payload=ChatMessageCreateInput(contenido="Intento intruso"),
                    response=Response(),
                    claims={"uid": forbidden.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(ctx_forb.exception.status_code, 403)

        self.direct_req.estado = "COMPLETADA"
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx_closed:
            post_conversation_message(
                modalidad="DIRECTA",
                solicitud_id=self.direct_req.id,
                payload=ChatMessageCreateInput(contenido="Mensaje tardío"),
                response=Response(),
                claims={"uid": self.owner_client.firebase_uid},
                db=self.db,
            )
        self.assertEqual(ctx_closed.exception.status_code, 409)
        self.assertEqual(self._message_count("DIRECTA", self.direct_req.id), 0)

    # CA-01, CA-02, CA-05: handshake WebSocket bloquea visitante, no autorizado y estado cerrado
    def test_ws_handshake_rejects_unauthenticated_unauthorized_and_closed(self):
        async def run():
            # Visitante sin token -> 4001
            ws_no_token = FakeWebSocket()
            await chat_websocket_endpoint(ws_no_token, "ABIERTA", self.open_req.id, token=None, after=None, db=self.db)
            self.assertFalse(ws_no_token.accepted)
            self.assertEqual(ws_no_token.close_code, WS_CLOSE_UNAUTHORIZED)

            # Postulante no elegido -> 4003
            ws_rejected = FakeWebSocket()
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.rejected_tech.firebase_uid}):
                await chat_websocket_endpoint(
                    ws_rejected, "ABIERTA", self.open_req.id, token="tok-rej", after=None, db=self.db
                )
            self.assertFalse(ws_rejected.accepted)
            self.assertEqual(ws_rejected.close_code, WS_CLOSE_FORBIDDEN)

            # Solicitud Cancelada -> 4009
            self.open_req.estado = "CANCELADA"
            self.db.commit()
            ws_closed_state = FakeWebSocket()
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.owner_client.firebase_uid}):
                await chat_websocket_endpoint(
                    ws_closed_state, "ABIERTA", self.open_req.id, token="tok-cli", after=None, db=self.db
                )
            self.assertFalse(ws_closed_state.accepted)
            self.assertEqual(ws_closed_state.close_code, WS_CLOSE_STATE_DISABLED)

        _run_coro(run())

    # CA-03, CA-04, CA-06: entrega en vivo a ambas partes, rechazo de vacío y reconexión sin duplicados
    def test_ws_live_delivery_validation_and_reconnect_sync(self):
        async def run():
            # Peer técnico ya conectado a la conversación
            peer_tech_ws = FakeWebSocket()
            peer_tech_ws.accepted = True
            await chat_manager.register("DIRECTA", self.direct_req.id, peer_tech_ws)

            # Cliente conecta y envía un mensaje vacío (error) + uno válido + duplicado del mismo client_message_id
            client_ws = FakeWebSocket(
                headers={"Authorization": "Bearer tok-cli"},
                incoming=[
                    {"type": "send", "contenido": "   ", "client_message_id": "cid-empty"},
                    {"type": "send", "contenido": "Confirmo dirección", "client_message_id": "cid-live-1"},
                    {"type": "send", "contenido": "Confirmo dirección", "client_message_id": "cid-live-1"},
                ],
            )
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.owner_client.firebase_uid}):
                await chat_websocket_endpoint(
                    client_ws, "DIRECTA", self.direct_req.id, token=None, after=None, db=self.db
                )

            self.assertTrue(client_ws.accepted)
            self.assertEqual(client_ws.sent[0]["type"], "connected")
            # El mensaje vacío generó validation_error sin persistir
            self.assertEqual(client_ws.sent[1]["type"], "error")
            self.assertEqual(client_ws.sent[1]["code"], "validation_error")
            # El mensaje válido se entregó en vivo a cliente y técnico
            self.assertEqual(client_ws.sent[2]["type"], "message")
            self.assertEqual(client_ws.sent[2]["mensaje"]["contenido"], "Confirmo dirección")
            self.assertEqual(len(peer_tech_ws.sent), 1)
            self.assertEqual(peer_tech_ws.sent[0]["type"], "message")
            self.assertEqual(peer_tech_ws.sent[0]["mensaje"]["contenido"], "Confirmo dirección")
            # El reintento devolvió el mismo ID sin duplicar en BD ni re-difundir al peer
            self.assertEqual(client_ws.sent[3]["duplicado"], True)
            self.assertEqual(client_ws.sent[3]["mensaje"]["id"], client_ws.sent[2]["mensaje"]["id"])
            self.assertEqual(self._message_count("DIRECTA", self.direct_req.id), 1)

            # Envío por REST también difunde en vivo al peer conectado
            with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
                post_conversation_message(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    payload=ChatMessageCreateInput(contenido="Ya llegué a la entrada"),
                    response=Response(),
                    claims={"uid": self.selected_tech.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(len(peer_tech_ws.sent), 2)
            self.assertEqual(peer_tech_ws.sent[1]["mensaje"]["contenido"], "Ya llegué a la entrada")

            # Reconexión del cliente con after=primer mensaje recupera solo el segundo sin duplicar
            first_ts = client_ws.sent[2]["mensaje"]["creado_en"]
            reconnect_ws = FakeWebSocket(headers={"Authorization": "Bearer tok-cli"})
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.owner_client.firebase_uid}):
                await chat_websocket_endpoint(
                    reconnect_ws, "DIRECTA", self.direct_req.id, token=None, after=first_ts, db=self.db
                )
            self.assertEqual(reconnect_ws.sent[0]["type"], "connected")
            self.assertEqual(reconnect_ws.sent[1]["type"], "sync")
            self.assertEqual(
                [m["contenido"] for m in reconnect_ws.sent[1]["mensajes"]],
                ["Ya llegué a la entrada"],
            )

        _run_coro(run())

    # CA-05: transición a COMPLETADA o CANCELADA cierra conexión previamente abierta
    def test_state_transition_closes_existing_ws_immediately(self):
        async def run():
            open_ws = FakeWebSocket()
            open_ws.accepted = True
            await chat_manager.register("DIRECTA", self.direct_req.id, open_ws)
            self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 1)

            with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
                transition_request(
                    self.db,
                    self.direct_req,
                    "COMPLETADA",
                    role="CLIENTE",
                    uid=self.owner_client.firebase_uid,
                )
                self.db.commit()

            self.assertTrue(open_ws.closed)
            self.assertEqual(open_ws.close_code, WS_CLOSE_STATE_DISABLED)
            self.assertEqual(open_ws.sent[-1]["type"], "chat_closed")
            self.assertEqual(open_ws.sent[-1]["estado"], "COMPLETADA")
            self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 0)

        _run_coro(run())

    # CA-05: revalidación de estado en cada envío sobre una conexión ya abierta
    def test_ws_send_revalidates_state_if_request_becomes_canceled_mid_session(self):
        async def run():
            ws = FakeWebSocket(headers={"Authorization": "Bearer tok-cli"})

            async def receive_after_cancel():
                self.open_req.estado = "CANCELADA"
                self.db.commit()
                return {"type": "send", "contenido": "No debería guardarse"}

            ws.receive_json = receive_after_cancel
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.owner_client.firebase_uid}):
                await chat_websocket_endpoint(
                    ws, "ABIERTA", self.open_req.id, token=None, after=None, db=self.db
                )

            self.assertTrue(ws.accepted)
            self.assertTrue(ws.closed)
            self.assertEqual(ws.close_code, WS_CLOSE_STATE_DISABLED)
            self.assertEqual(ws.sent[-1]["type"], "chat_closed")
            self.assertEqual(self._message_count("ABIERTA", self.open_req.id), 0)

        _run_coro(run())


if __name__ == "__main__":
    unittest.main()
