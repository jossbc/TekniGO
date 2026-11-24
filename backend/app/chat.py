"""HU-48 ST-01: reglas de dominio, autorización y consulta de mensajes del chat de contratación."""
from dataclasses import dataclass
from datetime import datetime, timezone

from firebase_admin import auth as firebase_auth
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ContractChatMessage,
    DirectRequest,
    OpenRequest,
    User,
    ensure_utc_datetime,
)
from app.request_states import state_key
from app.security import TOKEN_CLOCK_SKEW_SECONDS

ALLOWED_MODALIDADES = ("DIRECTA", "ABIERTA")
CHAT_ACTIVE_STATES = frozenset({"ACEPTADA", "EN PROCESO"})
CHAT_CLOSED_STATES = frozenset({"COMPLETADA", "CANCELADA"})
MAX_MESSAGE_LENGTH = 2000
MAX_CLIENT_MESSAGE_ID_LENGTH = 64
DEFAULT_MESSAGE_LIMIT = 200
MAX_MESSAGE_LIMIT = 500

RequestRecord = DirectRequest | OpenRequest


class ChatError(Exception):
    """Error base del módulo de chat operativo."""


class ChatValidationError(ChatError):
    """Parámetros o contenido de mensaje inválidos."""


class ChatNotFoundError(ChatError):
    """La solicitud o conversación indicada no existe."""


class ChatForbiddenError(ChatError):
    """El usuario no es participante autorizado de la conversación."""


class ChatUnauthorizedError(ChatError):
    """Credenciales de autenticación faltantes, inválidas o revocadas."""


class ChatStateError(ChatError):
    """El estado actual de la solicitud no habilita el chat operativo."""


@dataclass(frozen=True)
class AuthorizedConversation:
    modalidad: str
    solicitud_id: str
    estado: str
    rol_usuario: str
    usuario: User
    solicitud: RequestRecord


def normalize_modality(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if value not in ALLOWED_MODALIDADES:
        raise ChatValidationError("Modalidad inválida. Usa DIRECTA o ABIERTA.")
    return value


def normalize_solicitud_id(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value or len(value) > 36:
        raise ChatValidationError("Identificador de solicitud inválido.")
    return value


def validate_message_content(raw: str | None) -> str:
    if not isinstance(raw, str):
        raise ChatValidationError("El contenido del mensaje debe ser texto.")
    cleaned = raw.strip()
    if not cleaned:
        raise ChatValidationError("El mensaje no puede estar vacío.")
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        raise ChatValidationError(
            f"El mensaje no puede superar los {MAX_MESSAGE_LENGTH} caracteres."
        )
    return cleaned


def normalize_client_message_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ChatValidationError("El identificador de mensaje del cliente debe ser texto.")
    cleaned = raw.strip()
    if not cleaned:
        raise ChatValidationError("El identificador de mensaje del cliente no puede estar vacío.")
    if len(cleaned) > MAX_CLIENT_MESSAGE_ID_LENGTH:
        raise ChatValidationError(
            f"El identificador de mensaje no puede superar {MAX_CLIENT_MESSAGE_ID_LENGTH} caracteres."
        )
    return cleaned


def is_chat_active_state(estado: str | None) -> bool:
    return state_key(estado or "") in CHAT_ACTIVE_STATES


def is_chat_closed_state(estado: str | None) -> bool:
    return state_key(estado or "") in CHAT_CLOSED_STATES


def load_request_record(db: Session, modalidad: str, solicitud_id: str) -> RequestRecord:
    mode = normalize_modality(modalidad)
    req_id = normalize_solicitud_id(solicitud_id)
    model_cls = DirectRequest if mode == "DIRECTA" else OpenRequest
    record = db.scalar(select(model_cls).where(model_cls.id == req_id))
    if record is None:
        raise ChatNotFoundError("Solicitud no encontrada.")
    return record


def resolve_participant_role(record: RequestRecord, user: User) -> str | None:
    """Devuelve 'CLIENTE' si es propietario o 'TECNICO' si es el técnico seleccionado; None en otro caso."""
    user_identifiers = {user.firebase_uid, str(user.id)}
    if record.cliente_id and record.cliente_id in user_identifiers:
        return "CLIENTE"
    if record.tecnico_id and record.tecnico_id in user_identifiers:
        return "TECNICO"
    return None


def authorize_conversation(
    db: Session,
    *,
    modalidad: str,
    solicitud_id: str,
    user: User,
) -> AuthorizedConversation:
    """Valida pertenencia (CA-01/CA-02) y estado operativo habilitado (CA-01/CA-05)."""
    mode = normalize_modality(modalidad)
    req_id = normalize_solicitud_id(solicitud_id)
    record = load_request_record(db, mode, req_id)

    role = resolve_participant_role(record, user)
    if role is None:
        raise ChatForbiddenError(
            "No tienes permiso para acceder al chat de esta solicitud."
        )

    current_state = state_key(record.estado or "")
    if current_state in CHAT_CLOSED_STATES:
        raise ChatStateError(
            "El chat operativo está deshabilitado porque la solicitud fue completada o cancelada."
        )
    if current_state not in CHAT_ACTIVE_STATES:
        raise ChatStateError(
            "El chat operativo solo está habilitado mientras la solicitud está Aceptada o En proceso."
        )

    return AuthorizedConversation(
        modalidad=mode,
        solicitud_id=str(record.id),
        estado=current_state,
        rol_usuario=role,
        usuario=user,
        solicitud=record,
    )


def list_conversation_messages(
    db: Session,
    *,
    conversation: AuthorizedConversation,
    after: datetime | None = None,
    limit: int = DEFAULT_MESSAGE_LIMIT,
) -> list[ContractChatMessage]:
    """Consulta mensajes persistidos de una conversación autorizada en orden cronológico (CA-03/CA-04)."""
    if limit < 1 or limit > MAX_MESSAGE_LIMIT:
        raise ChatValidationError(
            f"El límite de mensajes debe estar entre 1 y {MAX_MESSAGE_LIMIT}."
        )

    query = (
        select(ContractChatMessage)
        .options(selectinload(ContractChatMessage.autor))
        .where(
            ContractChatMessage.modalidad == conversation.modalidad,
            ContractChatMessage.solicitud_id == conversation.solicitud_id,
        )
    )
    if after is not None:
        normalized_after = ensure_utc_datetime(after)
        assert normalized_after is not None
        query = query.where(ContractChatMessage.creado_en > normalized_after)

    query = query.order_by(
        ContractChatMessage.creado_en.asc(),
        ContractChatMessage.id.asc(),
    ).limit(limit)

    return list(db.scalars(query).all())


def iso_utc(value: datetime | None) -> str | None:
    normalized = ensure_utc_datetime(value)
    return normalized.isoformat() if normalized is not None else None


def serialize_chat_message(message: ContractChatMessage) -> dict:
    return {
        "id": str(message.id),
        "modalidad": message.modalidad,
        "solicitud_id": message.solicitud_id,
        "autor_id": str(message.autor_id),
        "autor_uid": message.autor_uid,
        "autor_nombre": message.autor.nombre_completo if message.autor is not None else None,
        "rol_autor": message.rol_autor,
        "contenido": message.contenido,
        "client_message_id": message.client_message_id,
        "creado_en": iso_utc(message.creado_en),
    }


def verify_websocket_token(token: str | None) -> dict:
    """Valida token Firebase Bearer para conexiones WebSocket sin registrar secretos (CA-02/CA-06)."""
    cleaned = (token or "").strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    if not cleaned:
        raise ChatUnauthorizedError("Se requiere un token Bearer para conectar al chat.")
    try:
        return firebase_auth.verify_id_token(
            cleaned,
            check_revoked=True,
            clock_skew_seconds=TOKEN_CLOCK_SKEW_SECONDS,
        )
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.ExpiredIdTokenError,
        firebase_auth.RevokedIdTokenError,
        firebase_auth.UserDisabledError,
        ValueError,
    ):
        raise ChatUnauthorizedError("Token inválido, vencido o revocado.") from None


def _find_existing_by_client_id(
    db: Session,
    *,
    conversation: AuthorizedConversation,
    client_message_id: str,
) -> ContractChatMessage | None:
    return db.scalar(
        select(ContractChatMessage)
        .options(selectinload(ContractChatMessage.autor))
        .where(
            ContractChatMessage.modalidad == conversation.modalidad,
            ContractChatMessage.solicitud_id == conversation.solicitud_id,
            ContractChatMessage.autor_id == conversation.usuario.id,
            ContractChatMessage.client_message_id == client_message_id,
        )
    )


def create_chat_message(
    db: Session,
    *,
    conversation: AuthorizedConversation,
    contenido: str | None,
    client_message_id: str | None = None,
) -> tuple[ContractChatMessage, bool]:
    """Persiste un mensaje no vacío de forma idempotente antes de cualquier entrega en vivo (CA-03/CA-04/CA-06)."""
    clean_content = validate_message_content(contenido)
    clean_client_id = normalize_client_message_id(client_message_id)

    if clean_client_id is not None:
        existing = _find_existing_by_client_id(
            db,
            conversation=conversation,
            client_message_id=clean_client_id,
        )
        if existing is not None:
            return existing, False

    message = ContractChatMessage(
        modalidad=conversation.modalidad,
        solicitud_id=conversation.solicitud_id,
        autor_id=conversation.usuario.id,
        autor_uid=conversation.usuario.firebase_uid,
        rol_autor=conversation.rol_usuario,
        contenido=clean_content,
        client_message_id=clean_client_id,
        creado_en=datetime.now(timezone.utc),
    )
    db.add(message)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if clean_client_id is not None:
            existing = _find_existing_by_client_id(
                db,
                conversation=conversation,
                client_message_id=clean_client_id,
            )
            if existing is not None:
                return existing, False
        raise

    db.refresh(message)
    # Asegurar que la relación autor esté disponible para serializar sin consultas adicionales
    _ = message.autor
    return message, True
