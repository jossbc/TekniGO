"""HU-48 ST-01: modelo de mensajes de chat de contratación y consulta por conversación autorizada."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import chat as chat_rules
from app.chat_router import get_conversation_messages, router as chat_router
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
from app.security import get_current_user


def _make_user(db: Session, uid: str, role: str, n: int) -> User:
    user = User(
        id=uuid.uuid4(),
        firebase_uid=uid,
        nombre_completo=f"Usuario {uid}",
        correo=f"{uid}@example.com",
        dni=f"08012000000{n:02d}",
        telefono=f"+5049{n:07d}",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(usuario_id=user.id, rol=role))
    if role == "TECNICO":
        db.add(TechnicianProfile(usuario_id=user.id, disponible=True, estado_verificacion="VERIFICADO"))
    db.flush()
    return user


class ChatModelConstraintTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.client_user = _make_user(self.db, "cli-model", "CLIENTE", 1)
        self.tech_user = _make_user(self.db, "tech-model", "TECNICO", 2)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_empty_or_whitespace_content_is_rejected_by_db(self):
        self.db.add(
            ContractChatMessage(
                modalidad="DIRECTA",
                solicitud_id="req-1",
                autor_id=self.client_user.id,
                autor_uid=self.client_user.firebase_uid,
                rol_autor="CLIENTE",
                contenido="   ",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_invalid_modality_and_role_are_rejected_by_db(self):
        self.db.add(
            ContractChatMessage(
                modalidad="OTRA",
                solicitud_id="req-1",
                autor_id=self.client_user.id,
                autor_uid=self.client_user.firebase_uid,
                rol_autor="CLIENTE",
                contenido="Hola",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        self.db.add(
            ContractChatMessage(
                modalidad="DIRECTA",
                solicitud_id="req-1",
                autor_id=self.client_user.id,
                autor_uid=self.client_user.firebase_uid,
                rol_autor="ADMIN",
                contenido="Hola",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_client_message_id_is_unique_per_conversation_and_author(self):
        self.db.add_all(
            [
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id="req-1",
                    autor_id=self.client_user.id,
                    autor_uid=self.client_user.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Primer envío",
                    client_message_id="msg-uuid-1",
                ),
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id="req-1",
                    autor_id=self.client_user.id,
                    autor_uid=self.client_user.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Sin id cliente 1",
                    client_message_id=None,
                ),
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id="req-1",
                    autor_id=self.client_user.id,
                    autor_uid=self.client_user.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Sin id cliente 2",
                    client_message_id=None,
                ),
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id="req-1",
                    autor_id=self.tech_user.id,
                    autor_uid=self.tech_user.firebase_uid,
                    rol_autor="TECNICO",
                    contenido="Respuesta técnico",
                    client_message_id="msg-uuid-1",
                ),
            ]
        )
        self.db.commit()

        self.db.add(
            ContractChatMessage(
                modalidad="DIRECTA",
                solicitud_id="req-1",
                autor_id=self.client_user.id,
                autor_uid=self.client_user.firebase_uid,
                rol_autor="CLIENTE",
                contenido="Duplicado",
                client_message_id="msg-uuid-1",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()


class ChatRulesUnitTests(unittest.TestCase):
    def test_validation_helpers(self):
        self.assertEqual(chat_rules.normalize_modality(" directa "), "DIRECTA")
        self.assertEqual(chat_rules.normalize_modality("abierta"), "ABIERTA")
        with self.assertRaises(chat_rules.ChatValidationError):
            chat_rules.normalize_modality("PRIVADA")

        self.assertEqual(chat_rules.validate_message_content("  Llego a las 3pm  "), "Llego a las 3pm")
        with self.assertRaises(chat_rules.ChatValidationError):
            chat_rules.validate_message_content("   \n\t ")
        with self.assertRaises(chat_rules.ChatValidationError):
            chat_rules.validate_message_content("x" * (chat_rules.MAX_MESSAGE_LENGTH + 1))

        self.assertIsNone(chat_rules.normalize_client_message_id(None))
        self.assertEqual(chat_rules.normalize_client_message_id(" cid-1 "), "cid-1")
        with self.assertRaises(chat_rules.ChatValidationError):
            chat_rules.normalize_client_message_id("   ")


class ChatAuthorizedQueryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.owner_client = _make_user(self.db, "cli-owner", "CLIENTE", 10)
        self.selected_tech = _make_user(self.db, "tech-selected", "TECNICO", 11)
        self.rejected_tech = _make_user(self.db, "tech-rejected", "TECNICO", 12)
        self.other_user = _make_user(self.db, "cli-other", "CLIENTE", 13)
        self.admin_user = _make_user(self.db, "adm-1", "ADMIN", 14)

        self.specialty = Specialty(id=uuid.uuid4(), nombre="Electricidad", activa=True)
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            id=uuid.uuid4(),
            usuario_id=self.selected_tech.id,
            especialidad_id=self.specialty.id,
            nombre="Reparación eléctrica",
            descripcion="Instalación y reparación",
            tarifa_base=Decimal("300.00"),
            unidad_cobro="POR_HORA",
        )
        self.db.add(self.service)
        self.db.flush()

        self.direct_req = self._create_direct_request("ACEPTADA")
        self.open_req = self._create_open_request("EN PROCESO", tecnico_id=self.selected_tech.firebase_uid)
        self.db.add(
            OpenRequestProposal(
                solicitud_id=self.open_req.id,
                usuario_id=self.rejected_tech.id,
                tecnico_id=self.rejected_tech.firebase_uid,
                monto=Decimal("450.00"),
                estado="RECHAZADA",
                activa=False,
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_direct_request(self, estado: str) -> DirectRequest:
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.owner_client.firebase_uid,
            tecnico_id=self.selected_tech.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Col. Palmira",
            direccion_exacta="Casa 12",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=2),
            descripcion="Revisar tablero",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("300.00"),
            unidad_cobro="POR_HORA",
            monto_base=Decimal("300.00"),
            metodo_pago="efectivo",
            monto_total=Decimal("300.00"),
            estado=estado,
        )
        self.db.add(req)
        self.db.flush()
        return req

    def _create_open_request(self, estado: str, tecnico_id: str | None = None) -> OpenRequest:
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.owner_client.firebase_uid,
            tecnico_id=tecnico_id,
            especialidad_id=self.specialty.id,
            municipio="Tegucigalpa",
            sector="Col. Kennedy",
            direccion_exacta="Bloque B",
            fecha_hora=datetime.now(timezone.utc) + timedelta(hours=3),
            descripcion="Cambiar interruptor",
            presupuesto=Decimal("500.00"),
            monto_base=Decimal("500.00"),
            metodo_pago="tarjeta",
            monto_total=Decimal("500.00"),
            estado=estado,
        )
        self.db.add(req)
        self.db.flush()
        return req

    def _call_get(
        self,
        user: User,
        modalidad: str,
        solicitud_id: str,
        *,
        after: datetime | None = None,
        limit: int = chat_rules.DEFAULT_MESSAGE_LIMIT,
    ) -> tuple[Response, dict]:
        response = Response()
        body = get_conversation_messages(
            modalidad=modalidad,
            solicitud_id=solicitud_id,
            response=response,
            after=after,
            limit=limit,
            claims={"uid": user.firebase_uid},
            db=self.db,
        )
        return response, body

    # CA-02: las rutas REST exigen autenticación Bearer (visitante sin token -> 401)
    def test_route_requires_bearer_authentication(self):
        matching = [
            r for r in chat_router.routes
            if getattr(r, "path", None) == "/chat/solicitudes/{modalidad}/{solicitud_id}/mensajes"
        ]
        self.assertGreaterEqual(len(matching), 1)
        for route in matching:
            dep_calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(get_current_user, dep_calls)

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(None)
        self.assertEqual(ctx.exception.status_code, 401)

    # CA-01, CA-03, CA-04, CA-06
    def test_owner_client_and_selected_tech_read_ordered_messages_with_private_cache(self):
        t0 = datetime(2026, 11, 23, 18, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=30)
        t2 = t0 + timedelta(seconds=60)
        self.db.add_all(
            [
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    autor_id=self.selected_tech.id,
                    autor_uid=self.selected_tech.firebase_uid,
                    rol_autor="TECNICO",
                    contenido="Voy en camino",
                    creado_en=t1,
                ),
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    autor_id=self.owner_client.id,
                    autor_uid=self.owner_client.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Hola, ¿a qué hora llegas?",
                    creado_en=t0,
                ),
                ContractChatMessage(
                    modalidad="DIRECTA",
                    solicitud_id=self.direct_req.id,
                    autor_id=self.owner_client.id,
                    autor_uid=self.owner_client.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Perfecto, aquí te espero",
                    creado_en=t2,
                ),
                ContractChatMessage(
                    modalidad="ABIERTA",
                    solicitud_id=self.open_req.id,
                    autor_id=self.owner_client.id,
                    autor_uid=self.owner_client.firebase_uid,
                    rol_autor="CLIENTE",
                    contenido="Mensaje de otra solicitud",
                    creado_en=t0,
                ),
            ]
        )
        self.db.commit()

        for user, expected_role in (
            (self.owner_client, "CLIENTE"),
            (self.selected_tech, "TECNICO"),
        ):
            response, body = self._call_get(user, "DIRECTA", self.direct_req.id)
            self.assertEqual(response.headers.get("cache-control"), "no-store, private")
            self.assertEqual(body["modalidad"], "DIRECTA")
            self.assertEqual(body["solicitud_id"], self.direct_req.id)
            self.assertEqual(body["estado"], "ACEPTADA")
            self.assertEqual(body["rol_usuario"], expected_role)
            self.assertTrue(body["habilitado"])
            self.assertEqual(
                [m["contenido"] for m in body["mensajes"]],
                ["Hola, ¿a qué hora llegas?", "Voy en camino", "Perfecto, aquí te espero"],
            )
            self.assertEqual(body["mensajes"][0]["autor_nombre"], self.owner_client.nombre_completo)
            self.assertEqual(body["mensajes"][1]["rol_autor"], "TECNICO")

        # CA-04: filtro incremental after recupera solo mensajes posteriores
        _, body_after = self._call_get(self.owner_client, "DIRECTA", self.direct_req.id, after=t0)
        self.assertEqual(
            [m["contenido"] for m in body_after["mensajes"]],
            ["Voy en camino", "Perfecto, aquí te espero"],
        )

    # CA-01: solicitud abierta en estado EN PROCESO funciona para ambas partes
    def test_open_request_in_progress_allows_owner_and_selected_tech(self):
        for user, expected_role in (
            (self.owner_client, "CLIENTE"),
            (self.selected_tech, "TECNICO"),
        ):
            _, body = self._call_get(user, "ABIERTA", self.open_req.id)
            self.assertEqual(body["rol_usuario"], expected_role)
            self.assertEqual(body["estado"], "EN PROCESO")

    # CA-02: postulante no elegido, otro usuario y admin son bloqueados con 403
    def test_unauthorized_users_cannot_read_messages(self):
        for forbidden_user in (self.rejected_tech, self.other_user, self.admin_user):
            with self.assertRaises(HTTPException) as ctx_open:
                self._call_get(forbidden_user, "ABIERTA", self.open_req.id)
            self.assertEqual(ctx_open.exception.status_code, 403)

            with self.assertRaises(HTTPException) as ctx_direct:
                self._call_get(forbidden_user, "DIRECTA", self.direct_req.id)
            self.assertEqual(ctx_direct.exception.status_code, 403)

    # CA-01 & CA-05: estados no habilitados o cerrados bloquean lectura
    def test_inactive_and_closed_states_disable_chat_access(self):
        for state in ("PENDIENTE", "RECHAZADA", "COMPLETADA", "CANCELADA"):
            req = self._create_direct_request(state)
            self.db.commit()
            for participant in (self.owner_client, self.selected_tech):
                with self.assertRaises(HTTPException) as ctx:
                    self._call_get(participant, "DIRECTA", req.id)
                self.assertEqual(ctx.exception.status_code, 409, msg=f"Estado {state} debe bloquear chat")

            # Tercero sigue recibiendo 403 (sin revelar el estado)
            with self.assertRaises(HTTPException) as ctx_other:
                self._call_get(self.other_user, "DIRECTA", req.id)
            self.assertEqual(ctx_other.exception.status_code, 403)

        for state in ("Publicada", "CON OFERTAS", "COMPLETADA", "CANCELADA"):
            req = self._create_open_request(
                state,
                tecnico_id=self.selected_tech.firebase_uid if state in ("COMPLETADA", "CANCELADA") else None,
            )
            self.db.commit()
            with self.assertRaises(HTTPException) as ctx:
                self._call_get(self.owner_client, "ABIERTA", req.id)
            self.assertEqual(ctx.exception.status_code, 409, msg=f"Estado abierta {state} debe bloquear chat")


if __name__ == "__main__":
    unittest.main()
