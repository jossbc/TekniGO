"""HU-48 ST-04 (CA-05): cierre de acceso y conexiones abiertas al pasar a estado final (Completada / Cancelada)."""
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException, Response, WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.chat_realtime import WS_CLOSE_STATE_DISABLED, chat_manager
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
        dni=f"08012000002{n:02d}",
        telefono=f"+5047{n:07d}",
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


class ChatFinalStateClosureTests(unittest.TestCase):
    def setUp(self):
        chat_manager.reset()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client_user = _make_user(self.db, "cli-st04", "CLIENTE", 1)
        self.tech_user = _make_user(self.db, "tech-st04", "TECNICO", 2)
        self.admin_user = _make_user(self.db, "adm-st04", "ADMIN", 3)

        self.specialty = Specialty(id=uuid.uuid4(), nombre="Aire acondicionado", activa=True)
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            id=uuid.uuid4(),
            usuario_id=self.tech_user.id,
            especialidad_id=self.specialty.id,
            nombre="Mantenimiento AC",
            descripcion="Limpieza general",
            tarifa_base=Decimal("400.00"),
            unidad_cobro="POR_HORA",
        )
        self.db.add(self.service)
        self.db.flush()

        self.direct_req = self._new_direct("EN PROCESO")
        self.open_req = self._new_open("EN PROCESO")
        self.db.commit()

    def tearDown(self):
        chat_manager.reset()
        self.db.close()
        self.engine.dispose()

    def _new_direct(self, estado: str) -> DirectRequest:
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Col. Humuya",
            direccion_exacta="Casa 8",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=1),
            descripcion="Revisión unidad",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("400.00"),
            unidad_cobro="POR_HORA",
            monto_base=Decimal("400.00"),
            metodo_pago="efectivo",
            monto_total=Decimal("400.00"),
            estado=estado,
        )
        self.db.add(req)
        self.db.flush()
        return req

    def _new_open(self, estado: str) -> OpenRequest:
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            especialidad_id=self.specialty.id,
            municipio="Tegucigalpa",
            sector="Col. América",
            direccion_exacta="Casa 22",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=2),
            descripcion="Instalación minisplit",
            presupuesto=Decimal("900.00"),
            monto_base=Decimal("900.00"),
            metodo_pago="tarjeta",
            monto_total=Decimal("900.00"),
            estado=estado,
        )
        self.db.add(req)
        self.db.flush()
        return req

    def _assert_all_channels_blocked(self, modalidad: str, solicitud_id: str):
        for participant in (self.client_user, self.tech_user):
            with self.assertRaises(HTTPException) as ctx_get:
                get_conversation_messages(
                    modalidad=modalidad,
                    solicitud_id=solicitud_id,
                    response=Response(),
                    after=None,
                    limit=50,
                    claims={"uid": participant.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(ctx_get.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx_post:
                post_conversation_message(
                    modalidad=modalidad,
                    solicitud_id=solicitud_id,
                    payload=ChatMessageCreateInput(contenido="Intento tras cierre"),
                    response=Response(),
                    claims={"uid": participant.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(ctx_post.exception.status_code, 409)

            ws_reconnect = FakeWebSocket()
            with patch("app.chat.verify_websocket_token", return_value={"uid": participant.firebase_uid}):
                _run_coro(
                    chat_websocket_endpoint(
                        ws_reconnect, modalidad, solicitud_id, token="tok", after=None, db=self.db
                    )
                )
            self.assertFalse(ws_reconnect.accepted)
            self.assertEqual(ws_reconnect.close_code, WS_CLOSE_STATE_DISABLED)

    def test_client_cancellation_closes_both_open_sessions_only_after_commit(self):
        client_ws = FakeWebSocket()
        tech_ws = FakeWebSocket()
        client_ws.accepted = tech_ws.accepted = True
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, client_ws))
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, tech_ws))

        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            transition_request(
                self.db,
                self.direct_req,
                "CANCELADA",
                role="CLIENTE",
                uid=self.client_user.firebase_uid,
            )
            # Antes del commit las conexiones aún no se cierran (seguridad transaccional)
            self.assertFalse(client_ws.closed)
            self.assertFalse(tech_ws.closed)

            self.db.commit()

        for ws in (client_ws, tech_ws):
            self.assertTrue(ws.closed)
            self.assertEqual(ws.close_code, WS_CLOSE_STATE_DISABLED)
            self.assertEqual(ws.sent[-1]["type"], "chat_closed")
            self.assertEqual(ws.sent[-1]["estado"], "CANCELADA")
        self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 0)
        self._assert_all_channels_blocked("DIRECTA", self.direct_req.id)

    def test_rollback_does_not_close_active_chat_connections(self):
        open_ws = FakeWebSocket()
        open_ws.accepted = True
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, open_ws))

        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            transition_request(
                self.db,
                self.direct_req,
                "CANCELADA",
                role="CLIENTE",
                uid=self.client_user.firebase_uid,
            )
            self.db.rollback()

        self.assertFalse(open_ws.closed)
        self.assertEqual(chat_manager.active_count("DIRECTA", self.direct_req.id), 1)
        # El chat sigue operativo tras rollback
        res = post_conversation_message(
            modalidad="DIRECTA",
            solicitud_id=self.direct_req.id,
            payload=ChatMessageCreateInput(contenido="Seguimos coordinando"),
            response=Response(),
            claims={"uid": self.client_user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(res["mensaje"]["contenido"], "Seguimos coordinando")

    def test_system_auto_completion_and_admin_closure_close_open_sessions(self):
        # 1) Cierre automático 48h (SISTEMA) en solicitud DIRECTA
        ws_sys = FakeWebSocket()
        ws_sys.accepted = True
        _run_coro(chat_manager.register("DIRECTA", self.direct_req.id, ws_sys))

        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            transition_request(self.db, self.direct_req, "COMPLETADA", role="SISTEMA")
            self.db.commit()

        self.assertTrue(ws_sys.closed)
        self.assertEqual(ws_sys.close_code, WS_CLOSE_STATE_DISABLED)
        self.assertEqual(ws_sys.sent[-1]["estado"], "COMPLETADA")
        self._assert_all_channels_blocked("DIRECTA", self.direct_req.id)

        # 2) Cierre administrativo (ADMIN) en solicitud ABIERTA
        ws_admin = FakeWebSocket()
        ws_admin.accepted = True
        _run_coro(chat_manager.register("ABIERTA", self.open_req.id, ws_admin))

        with patch.object(chat_manager, "_schedule_coro", side_effect=_run_coro):
            transition_request(
                self.db,
                self.open_req,
                "CANCELADA",
                role="ADMIN",
                uid=self.admin_user.firebase_uid,
            )
            self.db.commit()

        self.assertTrue(ws_admin.closed)
        self.assertEqual(ws_admin.close_code, WS_CLOSE_STATE_DISABLED)
        self.assertEqual(ws_admin.sent[-1]["estado"], "CANCELADA")
        self._assert_all_channels_blocked("ABIERTA", self.open_req.id)

    def test_ping_and_sync_on_already_open_ws_close_immediately_when_completed_externally(self):
        # Persistir un mensaje previo para confirmar que sync no lo filtra al cerrarse
        self.db.add(
            ContractChatMessage(
                modalidad="ABIERTA",
                solicitud_id=self.open_req.id,
                autor_id=self.client_user.id,
                autor_uid=self.client_user.firebase_uid,
                rol_autor="CLIENTE",
                contenido="Mensaje previo privado",
            )
        )
        self.db.commit()

        for frame in ({"type": "ping"}, {"type": "sync", "after": None}):
            self.open_req.estado = "EN PROCESO"
            self.db.commit()

            ws = FakeWebSocket(headers={"Authorization": "Bearer tok-tech"})

            async def receive_after_external_completion():
                self.open_req.estado = "COMPLETADA"
                self.db.commit()
                return frame

            ws.receive_json = receive_after_external_completion
            with patch("app.chat.verify_websocket_token", return_value={"uid": self.tech_user.firebase_uid}):
                _run_coro(
                    chat_websocket_endpoint(
                        ws, "ABIERTA", self.open_req.id, token=None, after=None, db=self.db
                    )
                )

            self.assertTrue(ws.accepted)
            self.assertTrue(ws.closed)
            self.assertEqual(ws.close_code, WS_CLOSE_STATE_DISABLED)
            self.assertEqual(ws.sent[-1]["type"], "chat_closed")
            self.assertNotIn("pong", [item.get("type") for item in ws.sent])
            self.assertNotIn("sync", [item.get("type") for item in ws.sent])


if __name__ == "__main__":
    unittest.main()
