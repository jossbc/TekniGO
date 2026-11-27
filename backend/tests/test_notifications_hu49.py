"""HU-49 ST-05: Pruebas de estado de lectura, persistencia, múltiples sesiones y acceso ajeno (CA-01 a CA-06)."""
from collections import deque
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException, Response, WebSocketDisconnect
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NotificationEvent, User, UserNotification, UserRole
from app.notification_read_state import NotificationReadStatus
from app.notifications_consumer import process_notification_event, serialize_notification
from app.notifications_events import enqueue_notification_event
from app.notifications_inbox_service import (
    NotificationInboxService,
    SqlAlchemyNotificationInboxRepository,
)
from app.notifications_realtime import notification_manager
from app.notifications_router import (
    get_notification_detail_endpoint,
    get_unread_notifications_count_endpoint,
    list_notifications_endpoint,
    list_pending_notifications_endpoint,
    mark_all_notifications_read_endpoint,
    mark_notification_read_endpoint,
    notifications_websocket_endpoint,
)
from app.notifications_templates import (
    EVENT_FINALIZACION_REPORTADA,
    EVENT_NUEVA_OFERTA_ABIERTA,
    EVENT_SERVICIO_INICIADO,
)


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
        correo=f"{uid}@teknigo.test",
        dni=f"08011999{n:05d}",
        telefono=f"+5049{n:07d}",
        telefono_verificado=True,
        estado_identidad="VERIFICADO",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(usuario_id=user.id, rol=role))
    db.flush()
    return user


class FakeWebSocket:
    def __init__(self, *, headers: dict[str, str] | None = None, incoming: list[dict] | None = None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._incoming = deque(incoming or [])
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.sent: list[dict] = []

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed = True
        self.close_code = code

    async def send_json(self, data: dict):
        if self.closed:
            raise RuntimeError("WebSocket already closed")
        self.sent.append(data)

    async def receive_json(self):
        if not self._incoming:
            raise WebSocketDisconnect(code=1000)
        return self._incoming.popleft()


class SpyBroadcaster:
    """Broadcaster de prueba que registra los avisos y contadores autoritativos emitidos."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], dict[str, int] | None]] = []

    def publish_notifications_sync(
        self,
        notifications: list[dict],
        unread_counts: dict[str, int] | None = None,
    ) -> None:
        self.calls.append((notifications, unread_counts))


class NotificationsHu49Tests(unittest.TestCase):
    def setUp(self):
        notification_manager.reset()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        notification_manager.reset()
        self.db.close()
        self.engine.dispose()

    def _enqueue_for(
        self,
        db: Session,
        user: User,
        *,
        key: str,
        event_type: str = EVENT_SERVICIO_INICIADO,
        sol_id: str = "sol-1",
        now: datetime | None = None,
    ) -> None:
        enqueue_notification_event(
            db,
            clave_evento=key,
            tipo_evento=event_type,
            destinatarios=[(user.id, "CLIENTE")],
            payload_seguro={"solicitud_id": sol_id, "modalidad": "DIRECTA"},
            now=now or datetime.now(timezone.utc),
        )
        db.commit()

    def test_st01_read_state_defaults_transitions_and_db_constraints(self):
        user = _make_user(self.db, "hu49-st01", "CLIENTE", 1)
        evt = NotificationEvent(
            id=uuid.uuid4(),
            clave_evento="st01-evt-1",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            payload_seguro={},
        )
        self.db.add(evt)
        self.db.commit()

        row = UserNotification(
            id=uuid.uuid4(),
            evento_id=evt.id,
            destinatario_id=user.id,
            clave_idempotencia="st01-evt-1:u",
            tipo_evento=EVENT_SERVICIO_INICIADO,
            titulo="Servicio iniciado",
            resumen="El técnico inició el servicio.",
            enlace_ruta="/mis-trabajos/sol-1",
            metadatos_seguros={},
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        self.assertEqual(row.estado_lectura, NotificationReadStatus.PENDIENTE.value)
        self.assertFalse(row.leida)
        self.assertIsNone(row.leida_en)

        t1 = datetime(2026, 11, 27, 14, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(minutes=10)

        self.assertTrue(row.marcar_como_leida(t1))
        self.db.commit()
        self.db.refresh(row)
        self.assertEqual(row.estado_lectura, NotificationReadStatus.LEIDA.value)
        self.assertTrue(row.leida)
        self.assertEqual(row.leida_en.replace(tzinfo=timezone.utc), t1)

        # Segunda llamada es idempotente y conserva la fecha original
        self.assertFalse(row.marcar_como_leida(t2))
        self.assertEqual(row.leida_en.replace(tzinfo=timezone.utc), t1)
        serialized = serialize_notification(row)
        self.assertEqual(serialized["estado_lectura"], "LEIDA")
        self.assertTrue(serialized["leida"])
        self.assertEqual(serialized["leida_en"], t1.isoformat())
        self.assertIsNotNone(serialized["leida_en_local"])

        # Constraints de BD rechazan combinaciones inconsistentes
        invalid_cases = [
            dict(estado_lectura="LEIDA", leida=True, leida_en=None),
            dict(estado_lectura="PENDIENTE", leida=False, leida_en=t1),
            dict(estado_lectura="LEIDA", leida=False, leida_en=t1),
            dict(estado_lectura="ARCHIVADA", leida=False, leida_en=None),
        ]
        for idx, kwargs in enumerate(invalid_cases):
            bad_evt = NotificationEvent(
                id=uuid.uuid4(),
                clave_evento=f"st01-bad-{idx}",
                tipo_evento=EVENT_SERVICIO_INICIADO,
                payload_seguro={},
            )
            self.db.add(bad_evt)
            self.db.commit()
            bad_row = UserNotification(
                id=uuid.uuid4(),
                evento_id=bad_evt.id,
                destinatario_id=user.id,
                clave_idempotencia=f"st01-bad-{idx}",
                tipo_evento=EVENT_SERVICIO_INICIADO,
                titulo="Inválido",
                resumen="Estado inconsistente",
                enlace_ruta="/mis-trabajos/sol-bad",
                metadatos_seguros={},
                **kwargs,
            )
            self.db.add(bad_row)
            with self.assertRaises(IntegrityError):
                self.db.commit()
            self.db.rollback()

    def test_ca01_inbox_distinguishes_read_and_pending_with_exact_counter(self):
        user = _make_user(self.db, "hu49-ca01", "CLIENTE", 2)
        self.db.commit()

        base = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        for idx in range(3):
            self._enqueue_for(
                self.db,
                user,
                key=f"ca01-evt-{idx}",
                sol_id=f"sol-{idx}",
                now=base + timedelta(minutes=idx),
            )

        initial = list_notifications_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(initial["total"], 3)
        self.assertEqual(initial["no_leidas"], 3)
        self.assertEqual(initial["pendientes"], 3)
        self.assertTrue(all(n["estado_lectura"] == "PENDIENTE" and not n["leida"] for n in initial["notificaciones"]))

        target_id = initial["notificaciones"][0]["id"]
        mark_notification_read_endpoint(target_id, Response(), claims={"uid": user.firebase_uid}, db=self.db)

        after = list_notifications_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(after["total"], 3)
        self.assertEqual(after["no_leidas"], 2)
        self.assertEqual(after["pendientes"], 2)

        by_id = {n["id"]: n for n in after["notificaciones"]}
        self.assertEqual(by_id[target_id]["estado_lectura"], "LEIDA")
        self.assertTrue(by_id[target_id]["leida"])
        self.assertIsNotNone(by_id[target_id]["leida_en"])

        pending_items = [n for n in after["notificaciones"] if n["id"] != target_id]
        self.assertEqual(len(pending_items), 2)
        self.assertTrue(all(n["estado_lectura"] == "PENDIENTE" and n["leida_en"] is None for n in pending_items))

        counter = get_unread_notifications_count_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(counter, {"no_leidas": 2, "pendientes": 2})

    def test_ca02_mark_read_persists_across_logout_and_new_session(self):
        user = _make_user(self.db, "hu49-ca02", "CLIENTE", 3)
        self.db.commit()
        uid = user.firebase_uid

        self._enqueue_for(self.db, user, key="ca02-evt-1", sol_id="sol-persist-1")
        self._enqueue_for(self.db, user, key="ca02-evt-2", sol_id="sol-persist-2")

        # Sesión 1: marca una notificación como leída y cierra sesión
        with Session(self.engine) as session_1:
            inbox_1 = list_notifications_endpoint(Response(), claims={"uid": uid}, db=session_1)
            target_id = inbox_1["notificaciones"][0]["id"]
            read_res = mark_notification_read_endpoint(target_id, Response(), claims={"uid": uid}, db=session_1)
            self.assertEqual(read_res["no_leidas"], 1)
            self.assertTrue(read_res["actualizada"])
            self.assertFalse(read_res["ya_leida"])
            persisted_read_at = read_res["notificacion"]["leida_en"]
            self.assertIsNotNone(persisted_read_at)

        # Sesión 2 (nuevo inicio de sesión): verifica persistencia exacta en BD
        with Session(self.engine) as session_2:
            detail = get_notification_detail_endpoint(target_id, Response(), claims={"uid": uid}, db=session_2)
            self.assertEqual(detail["estado_lectura"], "LEIDA")
            self.assertTrue(detail["leida"])
            self.assertEqual(detail["leida_en"], persisted_read_at)

            counter = get_unread_notifications_count_endpoint(Response(), claims={"uid": uid}, db=session_2)
            self.assertEqual(counter, {"no_leidas": 1, "pendientes": 1})

            pending = list_pending_notifications_endpoint(Response(), claims={"uid": uid}, db=session_2)
            self.assertEqual(pending["total"], 1)
            self.assertEqual(pending["no_leidas"], 1)
            self.assertNotIn(target_id, [n["id"] for n in pending["notificaciones"]])

    def test_ca03_repeated_mark_read_is_idempotent_and_never_negative(self):
        user = _make_user(self.db, "hu49-ca03", "CLIENTE", 4)
        self.db.commit()
        self._enqueue_for(self.db, user, key="ca03-evt-1", sol_id="sol-idem")

        inbox = list_notifications_endpoint(Response(), claims={"uid": user.firebase_uid}, db=self.db)
        notif_id = inbox["notificaciones"][0]["id"]

        emitted_events: list[tuple[str, dict]] = []
        service = NotificationInboxService(
            SqlAlchemyNotificationInboxRepository(self.db),
            publish_read_sync=lambda uid_str, payload: emitted_events.append((uid_str, payload)),
        )

        # Primera lectura
        first = service.mark_as_read(user, notif_id)
        self.assertTrue(first["actualizada"])
        self.assertFalse(first["ya_leida"])
        self.assertEqual(first["no_leidas"], 0)
        self.assertEqual(first["pendientes"], 0)
        first_leida_en = first["notificacion"]["leida_en"]
        self.assertEqual(len(emitted_events), 1)

        # Segunda y tercera lectura sobre la misma notificación ya leída
        second = service.mark_as_read(user, notif_id)
        third = service.mark_as_read(user, notif_id)

        for repeat in (second, third):
            self.assertFalse(repeat["actualizada"])
            self.assertTrue(repeat["ya_leida"])
            self.assertEqual(repeat["no_leidas"], 0)
            self.assertEqual(repeat["pendientes"], 0)
            self.assertGreaterEqual(repeat["no_leidas"], 0)
            self.assertEqual(repeat["notificacion"]["leida_en"], first_leida_en)

        # No volvió a emitir eventos read_sync innecesarios
        self.assertEqual(len(emitted_events), 1)

        # mark-all sobre bandeja con 0 pendientes también mantiene 0 sin negativos
        read_all_zero = mark_all_notifications_read_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(read_all_zero, {"actualizadas": 0, "no_leidas": 0, "pendientes": 0})

    def test_ca04_filter_by_pending_read_and_empty_pending_state(self):
        user = _make_user(self.db, "hu49-ca04", "CLIENTE", 5)
        self.db.commit()

        base = datetime(2026, 11, 27, 13, 0, tzinfo=timezone.utc)
        for idx in range(3):
            self._enqueue_for(
                self.db,
                user,
                key=f"ca04-evt-{idx}",
                sol_id=f"sol-f-{idx}",
                now=base + timedelta(minutes=idx),
            )

        all_items = list_notifications_endpoint(Response(), claims={"uid": user.firebase_uid}, db=self.db)
        read_id = all_items["notificaciones"][0]["id"]
        mark_notification_read_endpoint(read_id, Response(), claims={"uid": user.firebase_uid}, db=self.db)

        pending_by_estado = list_notifications_endpoint(
            Response(),
            estado="PENDIENTE",
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(pending_by_estado["filtro_estado"], "PENDIENTE")
        self.assertEqual(pending_by_estado["total"], 2)
        self.assertEqual(pending_by_estado["no_leidas"], 2)
        self.assertTrue(all(n["estado_lectura"] == "PENDIENTE" for n in pending_by_estado["notificaciones"]))

        pending_endpoint = list_pending_notifications_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(pending_endpoint["total"], 2)
        self.assertEqual(
            [n["id"] for n in pending_endpoint["notificaciones"]],
            [n["id"] for n in pending_by_estado["notificaciones"]],
        )

        read_only = list_notifications_endpoint(
            Response(),
            estado="LEIDA",
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(read_only["filtro_estado"], "LEIDA")
        self.assertEqual(read_only["total"], 1)
        self.assertEqual(read_only["no_leidas"], 2)
        self.assertEqual(read_only["notificaciones"][0]["id"], read_id)

        # Validaciones de filtro inválido o contradictorio
        with self.assertRaises(HTTPException) as bad_filter:
            list_notifications_endpoint(
                Response(),
                estado="INVALIDO",
                claims={"uid": user.firebase_uid},
                db=self.db,
            )
        self.assertEqual(bad_filter.exception.status_code, 400)

        with self.assertRaises(HTTPException) as conflict_filter:
            list_notifications_endpoint(
                Response(),
                estado="LEIDA",
                solo_no_leidas=True,
                claims={"uid": user.firebase_uid},
                db=self.db,
            )
        self.assertEqual(conflict_filter.exception.status_code, 400)

        # Al marcar todas como leídas, la consulta de pendientes queda vacía
        mark_all_notifications_read_endpoint(Response(), claims={"uid": user.firebase_uid}, db=self.db)
        empty_pending = list_pending_notifications_endpoint(
            Response(),
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(empty_pending["total"], 0)
        self.assertEqual(empty_pending["no_leidas"], 0)
        self.assertEqual(empty_pending["pendientes"], 0)
        self.assertEqual(empty_pending["notificaciones"], [])

    def test_ca05_new_notification_increments_counter_and_read_syncs_across_multiple_sessions(self):
        user = _make_user(self.db, "hu49-ca05", "CLIENTE", 6)
        self.db.commit()
        user_id_str = str(user.id)

        ws_session_a = FakeWebSocket()
        ws_session_b = FakeWebSocket()
        _run_coro(notification_manager.register(user_id_str, ws_session_a))
        _run_coro(notification_manager.register(user_id_str, ws_session_b))

        # 1. Dos avisos nuevos incrementan el contador autoritativo y se difunden a ambas sesiones
        spy = SpyBroadcaster()
        for idx, evt_type in enumerate((EVENT_NUEVA_OFERTA_ABIERTA, EVENT_FINALIZACION_REPORTADA), start=1):
            evt = NotificationEvent(
                id=uuid.uuid4(),
                clave_evento=f"ca05-live-{idx}",
                tipo_evento=evt_type,
                payload_seguro={
                    "destinatarios": [{"usuario_id": user_id_str, "rol": "CLIENTE"}],
                    "datos": {"solicitud_id": f"sol-live-{idx}", "modalidad": "DIRECTA"},
                },
            )
            self.db.add(evt)
            self.db.commit()
            res = process_notification_event(self.db, evt.id, broadcaster=spy)
            self.assertTrue(res["procesado"])
            self.assertEqual(res["notificaciones_creadas"], 1)

            items_pushed, unread_map = spy.calls[-1]
            self.assertEqual(unread_map, {user_id_str: idx})
            _run_coro(notification_manager.broadcast_notifications(items_pushed, unread_counts=unread_map))

        self.assertEqual(len(ws_session_a.sent), 2)
        self.assertEqual(len(ws_session_b.sent), 2)
        self.assertEqual(ws_session_b.sent[0]["type"], "notification")
        self.assertEqual(ws_session_b.sent[0]["no_leidas"], 1)
        self.assertEqual(ws_session_b.sent[1]["no_leidas"], 2)
        self.assertEqual(ws_session_b.sent[1]["pendientes"], 2)

        first_notif_id = ws_session_a.sent[0]["notificacion"]["id"]

        # 2. Sesión A marca un aviso como leído -> emite read_sync completo hacia Sesión B
        captured_syncs: list[dict] = []
        with patch.object(
            notification_manager,
            "publish_read_sync",
            side_effect=lambda uid_key, frame: (
                captured_syncs.append(frame),
                _run_coro(notification_manager.send_event(uid_key, frame)),
            ),
        ):
            with Session(self.engine) as session_a_db:
                read_result = mark_notification_read_endpoint(
                    first_notif_id,
                    Response(),
                    claims={"uid": user.firebase_uid},
                    db=session_a_db,
                )
                self.assertEqual(read_result["no_leidas"], 1)

        self.assertEqual(len(captured_syncs), 1)
        sync_frame = captured_syncs[0]
        self.assertEqual(sync_frame["type"], "read_sync")
        self.assertEqual(sync_frame["notification_id"], first_notif_id)
        self.assertEqual(sync_frame["estado_lectura"], "LEIDA")
        self.assertTrue(sync_frame["leida"])
        self.assertIsNotNone(sync_frame["leida_en"])
        self.assertEqual(sync_frame["no_leidas"], 1)
        self.assertEqual(sync_frame["pendientes"], 1)
        self.assertEqual(ws_session_b.sent[-1], sync_frame)

        # 3. Sesión B vuelve a consultar por REST y WS (acción unread_count) y ve estado sincronizado
        with Session(self.engine) as session_b_db:
            inbox_b = list_notifications_endpoint(Response(), claims={"uid": user.firebase_uid}, db=session_b_db)
            self.assertEqual(inbox_b["no_leidas"], 1)
            self.assertEqual(inbox_b["pendientes"], 1)
            by_id_b = {n["id"]: n for n in inbox_b["notificaciones"]}
            self.assertEqual(by_id_b[first_notif_id]["estado_lectura"], "LEIDA")
            self.assertTrue(by_id_b[first_notif_id]["leida"])

            ws_check = FakeWebSocket(incoming=[{"type": "unread_count"}])
            with patch("app.notifications_router.verify_notification_ws_token", return_value={"uid": user.firebase_uid}):
                _run_coro(
                    notifications_websocket_endpoint(
                        ws_check,
                        token="valid-token",
                        after_id=None,
                        since=None,
                        after=None,
                        db=session_b_db,
                    )
                )
            self.assertEqual(ws_check.sent[0]["type"], "connected")
            self.assertEqual(ws_check.sent[0]["no_leidas"], 1)
            self.assertEqual(ws_check.sent[0]["pendientes"], 1)
            self.assertEqual(ws_check.sent[1], {"type": "unread_count", "no_leidas": 1, "pendientes": 1})

        # 4. Sesión A ejecuta read-all -> Sesión B recibe read_all_sync con contador en 0
        with patch.object(
            notification_manager,
            "publish_read_sync",
            side_effect=lambda uid_key, frame: _run_coro(notification_manager.send_event(uid_key, frame)),
        ):
            with Session(self.engine) as session_a_db:
                res_all = mark_all_notifications_read_endpoint(
                    Response(),
                    claims={"uid": user.firebase_uid},
                    db=session_a_db,
                )
                self.assertEqual(res_all["no_leidas"], 0)
        self.assertEqual(ws_session_b.sent[-1]["type"], "read_all_sync")
        self.assertEqual(ws_session_b.sent[-1]["no_leidas"], 0)
        self.assertEqual(ws_session_b.sent[-1]["pendientes"], 0)

    def test_ca06_cannot_read_or_modify_another_accounts_notification_by_id(self):
        owner = _make_user(self.db, "hu49-owner", "CLIENTE", 7)
        intruder = _make_user(self.db, "hu49-intruder", "CLIENTE", 8)
        self.db.commit()

        self._enqueue_for(self.db, owner, key="ca06-owner-evt", sol_id="sol-private")
        owner_inbox = list_notifications_endpoint(Response(), claims={"uid": owner.firebase_uid}, db=self.db)
        owner_notif_id = owner_inbox["notificaciones"][0]["id"]

        owner_ws = FakeWebSocket()
        _run_coro(notification_manager.register(str(owner.id), owner_ws))

        with patch.object(
            notification_manager,
            "publish_read_sync",
            side_effect=lambda uid_key, frame: _run_coro(notification_manager.send_event(uid_key, frame)),
        ):
            with self.assertRaises(HTTPException) as patch_err:
                mark_notification_read_endpoint(
                    owner_notif_id,
                    Response(),
                    claims={"uid": intruder.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(patch_err.exception.status_code, 403)

            with self.assertRaises(HTTPException) as get_err:
                get_notification_detail_endpoint(
                    owner_notif_id,
                    Response(),
                    claims={"uid": intruder.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(get_err.exception.status_code, 403)

            # read-all de otra cuenta tampoco afecta al dueño
            intruder_all = mark_all_notifications_read_endpoint(
                Response(),
                claims={"uid": intruder.firebase_uid},
                db=self.db,
            )
            self.assertEqual(intruder_all["actualizadas"], 0)

        # IDs inexistentes o malformados devuelven 404
        for bad_id in (str(uuid.uuid4()), "no-es-un-uuid"):
            with self.assertRaises(HTTPException) as not_found_err:
                mark_notification_read_endpoint(
                    bad_id,
                    Response(),
                    claims={"uid": owner.firebase_uid},
                    db=self.db,
                )
            self.assertEqual(not_found_err.exception.status_code, 404)

        # La notificación del dueño permanece intacta en BD y sin eventos espurios
        self.assertEqual(owner_ws.sent, [])
        with Session(self.engine) as verify_db:
            persisted = verify_db.scalar(
                select(UserNotification).where(UserNotification.id == uuid.UUID(owner_notif_id))
            )
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.estado_lectura, "PENDIENTE")
            self.assertFalse(persisted.leida)
            self.assertIsNone(persisted.leida_en)

            owner_counter = get_unread_notifications_count_endpoint(
                Response(),
                claims={"uid": owner.firebase_uid},
                db=verify_db,
            )
            self.assertEqual(owner_counter, {"no_leidas": 1, "pendientes": 1})


if __name__ == "__main__":
    unittest.main()