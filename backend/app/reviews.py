"""Reviews and reputation module (EP-08: HU-44, HU-45, HU-46).

Cubre:
- HU-44: Calificar al técnico por parte del cliente.
- HU-45: Calificar al cliente por parte del técnico.
- HU-46: Publicación de reseñas (doble ciego, publicación bilateral simultánea o silenciosa a las 48h),
         cálculo de promedios, distribución de estrellas y reputación localizada en Honduras (America/Tegucigalpa).

Arquitectura y Principios SOLID aplicados:
- Adapter + LSP/OCP (`ReviewableRequestAdapter`): unifica la consulta de solicitudes directas y abiertas,
  la extracción de participantes, validación de estado COMPLETADA y fecha de cierre real desde HU-35/HU-32.
- Strategy + OCP/DIP (`ReviewPublicationStrategy`): encapsula las políticas de publicación
  (publicación bilateral instantánea vs. vencimiento silencioso de 48 horas con reloj inyectable).
- SRP / ISP: esquemas Pydantic segregados por caso de uso y funciones de dominio cohesivas.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional, Protocol
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    DirectRequest,
    OpenRequest,
    RequestStateEvent,
    ServiceReview,
    TechnicianProfile,
    User,
    ensure_utc_datetime,
)
from app.security import get_current_user

REVIEW_WINDOW_HOURS = 48
TEGUCIGALPA_TZ_NAME = "America/Tegucigalpa"
try:
    TEGUCIGALPA_TZ = ZoneInfo(TEGUCIGALPA_TZ_NAME)
except ZoneInfoNotFoundError:
    TEGUCIGALPA_TZ = timezone(timedelta(hours=-6), name=TEGUCIGALPA_TZ_NAME)

router = APIRouter(prefix="/reviews", tags=["Reviews & Reputation"])


# --- ESQUEMAS PYDANTIC SEGREGADOS (SRP / ISP) ---


class CreateReviewSchema(BaseModel):
    """DTO de entrada estricto para registrar una reseña bilateral (HU-44 / HU-45)."""

    model_config = ConfigDict(extra="forbid")

    tipo_solicitud: Literal["DIRECTA", "ABIERTA"]
    solicitud_id: str
    puntuacion: int
    comentario: str
    aspectos_destacados: Optional[list[str]] = None

    @field_validator("puntuacion")
    @classmethod
    def validate_puntuacion(cls, v: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or v < 1 or v > 5:
            raise ValueError("La puntuación debe ser un número entero entre 1 y 5.")
        return v

    @field_validator("comentario")
    @classmethod
    def validate_comentario(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("El comentario escrito es obligatorio.")
        return cleaned

    @field_validator("aspectos_destacados")
    @classmethod
    def validate_aspectos(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        return [item.strip() for item in v if item and item.strip()]


class ServiceReviewOut(BaseModel):
    """DTO de salida para una reseña individual localizada en America/Tegucigalpa."""

    id: str
    tipo_solicitud: str
    solicitud_id: str
    evaluador_id: str
    evaluador_nombre: str
    evaluado_id: str
    rol_evaluador: str
    puntuacion: int
    comentario: str
    aspectos_destacados: list[str]
    estado_publicacion: str
    fecha: str
    publicada_en: Optional[str] = None


class SubmitReviewResponseOut(BaseModel):
    """DTO de respuesta al crear una reseña indicando si se publicó de inmediato o quedó pendiente."""

    resena: ServiceReviewOut
    estado_publicacion: Literal["PENDIENTE", "PUBLICADA"]
    publicada_inmediata: bool
    publicada: bool = False
    motivo_publicacion: Optional[Literal["BILATERAL_SIMULTANEA", "EXPIRACION_SILENCIOSA_48H"]] = None
    mensaje: str


class ReputationSummaryOut(BaseModel):
    """DTO segregado únicamente para el resumen de reputación de un usuario."""

    usuario_id: str
    promedio: Optional[float]
    total_resenas: int
    sin_calificacion: bool
    distribucion_estrellas: dict[str, int]


class UserPublicReviewsOut(ReputationSummaryOut):
    """DTO de perfil público con resumen de reputación y lista de reseñas publicadas."""

    resenas: list[ServiceReviewOut]


class ServiceReviewsStatusOut(BaseModel):
    """DTO para inspeccionar el estado bilateral (doble ciego) dentro de una contratación."""

    tipo_solicitud: str
    solicitud_id: str
    servicio_completado: bool
    mi_resena: Optional[ServiceReviewOut] = None
    contraparte_resena: Optional[ServiceReviewOut] = None
    contraparte_ha_calificado: bool = False
    estado_publicacion_general: Literal["NINGUNA", "PENDIENTE", "PUBLICADA"]


class SyncExpirationsOut(BaseModel):
    """DTO de respuesta para la sincronización idempotente de reseñas vencidas a 48h."""

    publicadas: int
    mensaje: str


# --- HELPERS DE FECHA, IDENTIDAD Y REPUTACIÓN ---


def to_tegucigalpa_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    utc_dt = ensure_utc_datetime(dt)
    assert utc_dt is not None
    return utc_dt.astimezone(TEGUCIGALPA_TZ).isoformat()


def _parse_iso_utc(raw_value: Any) -> Optional[datetime]:
    if isinstance(raw_value, datetime):
        return ensure_utc_datetime(raw_value)
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value).strip())
        return ensure_utc_datetime(parsed)
    except ValueError:
        return None


def _find_user_by_id_or_firebase_uid(user_id_or_uid: Any, db: Session) -> Optional[User]:
    raw_str = str(user_id_or_uid).strip()
    clean_uuid_obj = None
    try:
        clean_uuid_obj = uuid.UUID(raw_str)
    except ValueError:
        pass

    conditions = [User.firebase_uid == raw_str]
    if clean_uuid_obj is not None:
        conditions.append(User.id == clean_uuid_obj)
    return db.scalar(select(User).where(or_(*conditions)))


def _get_authenticated_user(claims: dict, db: Session) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada.")
    user = _find_user_by_id_or_firebase_uid(uid, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    return user


# --- PATRÓN ADAPTER + LSP / OCP (SOLICITUD DIRECTA vs. ABIERTA) ---


class ReviewableRequestAdapter(Protocol):
    """Patrón Adapter + Principios SOLID (LSP, OCP, ISP):
    Contrato unificado para consultar solicitudes DirectRequest/OpenRequest elegibles para reseña,
    extraer sus participantes, verificar estado COMPLETADA y determinar la fecha de cierre real.
    """

    tipo_solicitud: Literal["DIRECTA", "ABIERTA"]

    def find_request(self, db: Session, clean_id: str) -> Any | None: ...

    def extract_participants(self, record: Any, db: Session) -> tuple[User, User]: ...

    def is_completed(self, record: Any) -> bool: ...

    def resolve_completed_at(self, record: Any, db: Session) -> datetime: ...


class _BaseReviewableRequestAdapter:
    """Comportamiento común reutilizable para adaptadores de solicitudes evaluables (DRY / LSP)."""

    tipo_solicitud: Literal["DIRECTA", "ABIERTA"]

    def extract_participants(self, record: Any, db: Session) -> tuple[User, User]:
        client_uid = getattr(record, "cliente_id", None)
        tech_uid = getattr(record, "tecnico_id", None)
        client_user = _find_user_by_id_or_firebase_uid(client_uid, db) if client_uid else None
        tech_user = _find_user_by_id_or_firebase_uid(tech_uid, db) if tech_uid else None
        if client_user is None or tech_user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La solicitud no tiene cliente o técnico asignado válido.",
            )
        return client_user, tech_user

    def is_completed(self, record: Any) -> bool:
        return (getattr(record, "estado", None) or "").strip().upper() == "COMPLETADA"

    def resolve_completed_at(self, record: Any, db: Session) -> datetime:
        """Obtiene la fecha real de cierre alineada con HU-35 (`seguimiento_recepcion`) y HU-32 (`historial_estados_solicitud`)."""
        tracking = getattr(record, "seguimiento_recepcion", None) or {}
        if isinstance(tracking, dict):
            closed_from_tracking = _parse_iso_utc(tracking.get("fecha_cierre"))
            if closed_from_tracking is not None:
                return closed_from_tracking
            deadline_from_tracking = _parse_iso_utc(tracking.get("plazo_resena_vence_en"))
            if deadline_from_tracking is not None:
                return deadline_from_tracking - timedelta(hours=REVIEW_WINDOW_HOURS)

        event_dt = db.scalar(
            select(RequestStateEvent.fecha)
            .where(
                RequestStateEvent.modalidad == self.tipo_solicitud,
                RequestStateEvent.solicitud_id == str(record.id),
                RequestStateEvent.estado == "COMPLETADA",
            )
            .order_by(RequestStateEvent.id.desc())
            .limit(1)
        )
        parsed_event_dt = ensure_utc_datetime(event_dt)
        if parsed_event_dt is not None:
            return parsed_event_dt

        fallback = (
            getattr(record, "finalizacion_reportada_en", None)
            or getattr(record, "fecha_hora", None)
            or getattr(record, "creada_en", None)
            or datetime.now(timezone.utc)
        )
        normalized = ensure_utc_datetime(fallback)
        assert normalized is not None
        return normalized


class DirectReviewableRequestAdapter(_BaseReviewableRequestAdapter):
    """Adaptador concreto para solicitudes directas (`DirectRequest`)."""

    tipo_solicitud: Literal["DIRECTA", "ABIERTA"] = "DIRECTA"

    def find_request(self, db: Session, clean_id: str) -> DirectRequest | None:
        if not clean_id:
            return None
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            try:
                lookup_id = str(uuid.UUID(clean_id))
            except ValueError:
                return None
            return db.scalar(select(DirectRequest).where(DirectRequest.id == lookup_id))
        return db.scalar(select(DirectRequest).where(DirectRequest.id.in_(_solicitud_id_candidates(clean_id))))


class OpenReviewableRequestAdapter(_BaseReviewableRequestAdapter):
    """Adaptador concreto para licitaciones/solicitudes abiertas (`OpenRequest`)."""

    tipo_solicitud: Literal["DIRECTA", "ABIERTA"] = "ABIERTA"

    def find_request(self, db: Session, clean_id: str) -> OpenRequest | None:
        if not clean_id:
            return None
        return db.scalar(select(OpenRequest).where(OpenRequest.id.in_(_solicitud_id_candidates(clean_id))))


_REVIEWABLE_ADAPTERS: dict[str, ReviewableRequestAdapter] = {
    "DIRECTA": DirectReviewableRequestAdapter(),
    "ABIERTA": OpenReviewableRequestAdapter(),
}


def _get_reviewable_adapter(tipo_solicitud: str) -> ReviewableRequestAdapter:
    norm_tipo = (tipo_solicitud or "").strip().upper()
    adapter = _REVIEWABLE_ADAPTERS.get(norm_tipo)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo de solicitud inválido. Usa DIRECTA o ABIERTA.",
        )
    return adapter


def _resolve_service_request(
    tipo_solicitud: str,
    solicitud_id: str,
    db: Session,
) -> tuple[ReviewableRequestAdapter, Any, User, User, datetime]:
    """Recupera el adaptador de modalidad, el registro, cliente, técnico y la fecha de cierre real."""
    adapter = _get_reviewable_adapter(tipo_solicitud)
    clean_id = (solicitud_id or "").strip()
    record = adapter.find_request(db, clean_id)
    if record is None:
        label = "directa" if adapter.tipo_solicitud == "DIRECTA" else "abierta"
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Solicitud {label} no encontrada.")

    client_user, tech_user = adapter.extract_participants(record, db)
    completed_at = adapter.resolve_completed_at(record, db)
    return adapter, record, client_user, tech_user, completed_at


# --- PATRÓN STRATEGY + OCP / DIP (POLÍTICAS DE PUBLICACIÓN DE RESEÑAS) ---


class ReviewPublicationStrategy(Protocol):
    """Patrón Strategy + Principios SOLID (OCP, DIP, SRP):
    Contrato desacoplado para determinar si una reseña debe publicarse de inmediato
    (publicación bilateral mutua o expiración silenciosa del plazo de 48 horas).
    """

    name: str

    def should_publish(
        self,
        *,
        counterpart_review: Optional[ServiceReview],
        completed_at: datetime,
        now: datetime,
    ) -> bool: ...


class BilateralMutualPublicationStrategy:
    """HU-46 CA-01: Publica instantáneamente ambas reseñas en cuanto la segunda parte evalúa."""

    name = "BILATERAL_SIMULTANEA"

    def should_publish(
        self,
        *,
        counterpart_review: Optional[ServiceReview],
        completed_at: datetime,
        now: datetime,
    ) -> bool:
        return counterpart_review is not None


class SilentTimeoutPublicationStrategy:
    """HU-46 CA-01/CA-02: Publica silenciosamente al cumplirse 48h desde la fecha de cierre del servicio."""

    name = "EXPIRACION_SILENCIOSA_48H"

    def __init__(self, window_hours: int = REVIEW_WINDOW_HOURS) -> None:
        self.window_hours = window_hours

    def should_publish(
        self,
        *,
        counterpart_review: Optional[ServiceReview],
        completed_at: datetime,
        now: datetime,
    ) -> bool:
        now_utc = ensure_utc_datetime(now)
        completed_utc = ensure_utc_datetime(completed_at)
        assert now_utc is not None and completed_utc is not None
        elapsed_hours = (now_utc - completed_utc).total_seconds() / 3600.0
        return elapsed_hours >= float(self.window_hours)


_DEFAULT_PUBLICATION_STRATEGIES: tuple[ReviewPublicationStrategy, ...] = (
    BilateralMutualPublicationStrategy(),
    SilentTimeoutPublicationStrategy(window_hours=REVIEW_WINDOW_HOURS),
)


def evaluate_review_publication_policy(
    *,
    counterpart_review: Optional[ServiceReview],
    completed_at: datetime,
    now: datetime,
    strategies: Optional[tuple[ReviewPublicationStrategy, ...]] = None,
) -> tuple[bool, Optional[str]]:
    """Evalúa las estrategias configuradas y devuelve si corresponde publicar inmediatamente."""
    for strategy in strategies or _DEFAULT_PUBLICATION_STRATEGIES:
        if strategy.should_publish(
            counterpart_review=counterpart_review,
            completed_at=completed_at,
            now=now,
        ):
            return True, strategy.name
    return False, None


# --- CÁLCULO DE REPUTACIÓN Y SINCRONIZACIÓN SILENCIOSA ---


def get_technician_reputation_stats_map(
    user_ids: list[uuid.UUID],
    db: Session,
) -> dict[uuid.UUID, tuple[Optional[float], int]]:
    """Calcula promedio y conteo usando únicamente reseñas PUBLICADAS recibidas en rol de TECNICO."""
    unique_ids = list({uid for uid in user_ids if uid is not None})
    if not unique_ids:
        return {}

    rows = db.execute(
        select(ServiceReview.evaluado_id, ServiceReview.puntuacion).where(
            ServiceReview.evaluado_id.in_(unique_ids),
            ServiceReview.rol_evaluador == "CLIENTE",
            ServiceReview.estado_publicacion == "PUBLICADA",
        )
    ).all()

    scores_by_uid: dict[uuid.UUID, list[int]] = {uid: [] for uid in unique_ids}
    for eval_uid, score in rows:
        if eval_uid in scores_by_uid:
            scores_by_uid[eval_uid].append(int(score))

    result: dict[uuid.UUID, tuple[Optional[float], int]] = {}
    for uid in unique_ids:
        scores = scores_by_uid[uid]
        total = len(scores)
        if total == 0:
            result[uid] = (None, 0)
        else:
            result[uid] = (round(float(sum(scores)) / total, 2), total)
    return result


def get_technician_reputation_stats(
    user_id: uuid.UUID,
    db: Session,
) -> tuple[Optional[float], int]:
    """Calcula promedio y total de reseñas publicadas recibidas por un técnico (solo rol_evaluador='CLIENTE')."""
    return get_technician_reputation_stats_map([user_id], db).get(user_id, (None, 0))


def _canonical_solicitud_id(raw_id: Any) -> str:
    cleaned = str(raw_id or "").strip()
    if not cleaned:
        return ""
    try:
        return str(uuid.UUID(cleaned))
    except ValueError:
        return cleaned


def _solicitud_id_candidates(raw_id: Any) -> list[str]:
    raw_str = str(raw_id or "").strip()
    canonical = _canonical_solicitud_id(raw_str)
    candidates: list[str] = []
    for candidate in (canonical, raw_str, raw_str.upper(), canonical.upper()):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def get_review_flags_map(
    db: Session,
    requests_meta: list[tuple[str, str]],
    viewer_role: Literal["CLIENTE", "TECNICO"],
    *,
    sync_expirations: bool = True,
) -> dict[tuple[str, str], tuple[bool, bool]]:
    """Calcula (mi_resena_enviada, evaluacion_cerrada) para solicitudes COMPLETADA según el rol actual."""
    raw_pairs = [
        ((mod or "").strip().upper(), str(sid).strip())
        for mod, sid in requests_meta
        if mod and sid and str(sid).strip()
    ]
    if not raw_pairs:
        return {}

    if sync_expirations:
        sync_expired_reviews(db)

    candidate_ids: list[str] = []
    seen_candidates: set[str] = set()
    for _, sid in raw_pairs:
        for cand in _solicitud_id_candidates(sid):
            if cand not in seen_candidates:
                seen_candidates.add(cand)
                candidate_ids.append(cand)

    reviews = list(
        db.scalars(
            select(ServiceReview).where(
                ServiceReview.solicitud_id.in_(candidate_ids),
            )
        ).all()
    )

    by_request: dict[tuple[str, str], dict[str, ServiceReview]] = {}
    for rev in reviews:
        norm_mod = (rev.tipo_solicitud or "").strip().upper()
        canon_sid = _canonical_solicitud_id(rev.solicitud_id)
        if not norm_mod or not canon_sid:
            continue
        by_request.setdefault((norm_mod, canon_sid), {})[(rev.rol_evaluador or "").strip().upper()] = rev

    counterpart_role = "TECNICO" if viewer_role == "CLIENTE" else "CLIENTE"
    flags: dict[tuple[str, str], tuple[bool, bool]] = {}
    for norm_mod, raw_sid in raw_pairs:
        canon_key = (norm_mod, _canonical_solicitud_id(raw_sid))
        role_reviews = by_request.get(canon_key, {})
        my_rev = role_reviews.get(viewer_role)
        other_rev = role_reviews.get(counterpart_role)
        mi_resena_enviada = my_rev is not None
        evaluacion_cerrada = bool(
            my_rev is not None
            and (other_rev is not None or (my_rev.estado_publicacion or "").strip().upper() == "PUBLICADA")
        )
        flags[canon_key] = (mi_resena_enviada, evaluacion_cerrada)
        flags[(norm_mod, raw_sid)] = (mi_resena_enviada, evaluacion_cerrada)
    return flags


def get_request_review_flags(
    db: Session,
    modalidad: str,
    solicitud_id: str,
    viewer_role: Literal["CLIENTE", "TECNICO"],
    *,
    sync_expirations: bool = True,
) -> tuple[bool, bool]:
    """Devuelve (mi_resena_enviada, evaluacion_cerrada) para una solicitud individual."""
    norm_mod = (modalidad or "").strip().upper()
    canon_sid = _canonical_solicitud_id(solicitud_id)
    flags = get_review_flags_map(
        db,
        [(norm_mod, canon_sid)],
        viewer_role=viewer_role,
        sync_expirations=sync_expirations,
    )
    return flags.get((norm_mod, canon_sid), (False, False))


def _recompute_user_reputation(user_id: uuid.UUID, db: Session) -> Optional[float]:
    """Calcula el promedio de calificaciones publicadas como TÉCNICO y sincroniza `TechnicianProfile`."""
    avg, _ = get_technician_reputation_stats(user_id, db)

    tech_profile = db.get(TechnicianProfile, user_id)
    if tech_profile is not None:
        tech_profile.calificacion_promedio = avg
    return avg


def sync_expired_reviews(
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
    *,
    window_hours: int = REVIEW_WINDOW_HOURS,
) -> int:
    """HU-46 CA-01/CA-02/CA-03: Sincronización silenciosa e idempotente tras expirar 48h desde el cierre.

    Si han transcurrido >= 48 horas desde `fecha_completado_servicio` y una reseña sigue `PENDIENTE`,
    pasa a `PUBLICADA` de forma silenciosa y recalcula promedios. Nunca inventa reseñas falsas.
    """
    now = ensure_utc_datetime(clock_fn() if clock_fn else datetime.now(timezone.utc))
    assert now is not None

    cutoff = now - timedelta(hours=window_hours)

    pending_reviews = list(
        db.scalars(
            select(ServiceReview).where(
                ServiceReview.estado_publicacion == "PENDIENTE",
                ServiceReview.fecha_completado_servicio <= cutoff,
            )
        ).all()
    )

    if not pending_reviews:
        return 0

    evaluated_users: set[uuid.UUID] = set()
    for rev in pending_reviews:
        rev.estado_publicacion = "PUBLICADA"
        rev.publicada_en = now
        evaluated_users.add(rev.evaluado_id)

    for uid in evaluated_users:
        _recompute_user_reputation(uid, db)

    db.commit()
    return len(pending_reviews)


def _serialize_review(review: ServiceReview, db: Session) -> ServiceReviewOut:
    evaluator = db.get(User, review.evaluador_id)
    evaluator_name = evaluator.nombre_completo if evaluator else "Usuario de TekniGO"
    return ServiceReviewOut(
        id=str(review.id),
        tipo_solicitud=review.tipo_solicitud,
        solicitud_id=str(review.solicitud_id),
        evaluador_id=str(review.evaluador_id),
        evaluador_nombre=evaluator_name,
        evaluado_id=str(review.evaluado_id),
        rol_evaluador=review.rol_evaluador,
        puntuacion=review.puntuacion,
        comentario=review.comentario,
        aspectos_destacados=review.aspectos_destacados or [],
        estado_publicacion=review.estado_publicacion,
        fecha=to_tegucigalpa_iso(review.creada_en) or "",
        publicada_en=to_tegucigalpa_iso(review.publicada_en),
    )


# --- FUNCIONES DE SERVICIO (INYECCIÓN DE RELOJ Y ESTRATEGIAS PARA TESTS) ---


def create_service_review(
    payload: CreateReviewSchema,
    claims: dict,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
    *,
    publication_strategies: Optional[tuple[ReviewPublicationStrategy, ...]] = None,
) -> dict[str, Any]:
    """Crea una reseña cumpliendo todas las reglas de negocio de HU-44, HU-45 y HU-46."""
    current_user = _get_authenticated_user(claims, db)
    adapter, record, client_user, tech_user, completed_at = _resolve_service_request(
        payload.tipo_solicitud, payload.solicitud_id, db
    )

    # CA-01 y CA-05: El servicio debe estar en estado real COMPLETADA (HU-35)
    if not adapter.is_completed(record):
        estado_actual = (getattr(record, "estado", None) or "").strip().upper()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo se pueden calificar servicios con estado COMPLETADA (estado actual: {estado_actual}).",
        )

    is_client = current_user.id == client_user.id or current_user.firebase_uid == client_user.firebase_uid
    is_tech = current_user.id == tech_user.id or current_user.firebase_uid == tech_user.firebase_uid

    if not is_client and not is_tech:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para calificar este servicio.",
        )

    if is_client:
        rol_evaluador = "CLIENTE"
        evaluador_id = client_user.id
        evaluado_id = tech_user.id
        counterpart_role = "TECNICO"
    else:
        rol_evaluador = "TECNICO"
        evaluador_id = tech_user.id
        evaluado_id = client_user.id
        counterpart_role = "CLIENTE"

    # CA-03: Unicidad (máximo 1 evaluación por rol por solicitud -> 409 Conflict)
    clean_solicitud_id = _canonical_solicitud_id(payload.solicitud_id)
    id_candidates = _solicitud_id_candidates(payload.solicitud_id)
    existing_my_review = db.scalar(
        select(ServiceReview).where(
            ServiceReview.tipo_solicitud == payload.tipo_solicitud,
            ServiceReview.solicitud_id.in_(id_candidates),
            ServiceReview.rol_evaluador == rol_evaluador,
        )
    )
    if existing_my_review is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya has calificado este servicio. No es posible enviar evaluaciones duplicadas.",
        )

    now = ensure_utc_datetime(clock_fn() if clock_fn else datetime.now(timezone.utc))
    assert now is not None

    # Sincronizar vencimientos de 48h antes de verificar contraparte
    sync_expired_reviews(db, clock_fn=lambda: now)

    counterpart_review = db.scalar(
        select(ServiceReview).where(
            ServiceReview.tipo_solicitud == payload.tipo_solicitud,
            ServiceReview.solicitud_id.in_(id_candidates),
            ServiceReview.rol_evaluador == counterpart_role,
        )
    )

    should_publish_now, policy_matched = evaluate_review_publication_policy(
        counterpart_review=counterpart_review,
        completed_at=completed_at,
        now=now,
        strategies=publication_strategies,
    )

    initial_status: Literal["PENDIENTE", "PUBLICADA"] = "PUBLICADA" if should_publish_now else "PENDIENTE"
    published_at = now if should_publish_now else None

    new_review = ServiceReview(
        tipo_solicitud=payload.tipo_solicitud,
        solicitud_id=clean_solicitud_id,
        evaluador_id=evaluador_id,
        evaluado_id=evaluado_id,
        rol_evaluador=rol_evaluador,
        puntuacion=payload.puntuacion,
        comentario=payload.comentario,
        aspectos_destacados=payload.aspectos_destacados or [],
        estado_publicacion=initial_status,
        fecha_completado_servicio=completed_at,
        creada_en=now,
        publicada_en=published_at,
    )
    db.add(new_review)

    # Publicación bilateral simultánea si la contraparte estaba PENDIENTE
    if counterpart_review is not None and counterpart_review.estado_publicacion != "PUBLICADA":
        counterpart_review.estado_publicacion = "PUBLICADA"
        counterpart_review.publicada_en = now

    db.flush()

    if should_publish_now:
        _recompute_user_reputation(evaluado_id, db)
        if counterpart_review is not None:
            _recompute_user_reputation(counterpart_review.evaluado_id, db)

    db.commit()
    db.refresh(new_review)

    serialized = _serialize_review(new_review, db)

    if should_publish_now:
        if policy_matched == "EXPIRACION_SILENCIOSA_48H":
            msg = "Calificación publicada inmediatamente al haberse cumplido el plazo de 48 horas."
        else:
            msg = "Calificación enviada y publicada exitosamente al completarse la evaluación mutua."
    else:
        msg = "Tu calificación fue enviada con éxito. Será visible cuando la otra parte también califique o al transcurrir 48 horas."

    return {
        "resena": serialized,
        "estado_publicacion": initial_status,
        "publicada_inmediata": should_publish_now,
        "publicada": should_publish_now,
        "motivo_publicacion": policy_matched if should_publish_now else None,
        "mensaje": msg,
    }


def get_service_reviews_status_data(
    tipo_solicitud: str,
    solicitud_id: str,
    claims: dict,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
) -> ServiceReviewsStatusOut:
    """Obtiene el estado de reseñas para una solicitud manteniendo la regla de doble ciego."""
    sync_expired_reviews(db, clock_fn=clock_fn)

    current_user = _get_authenticated_user(claims, db)
    adapter, record, client_user, tech_user, _ = _resolve_service_request(tipo_solicitud, solicitud_id, db)

    is_client = current_user.id == client_user.id or current_user.firebase_uid == client_user.firebase_uid
    is_tech = current_user.id == tech_user.id or current_user.firebase_uid == tech_user.firebase_uid

    if not is_client and not is_tech:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver el estado de calificación de este servicio.",
        )

    my_role = "CLIENTE" if is_client else "TECNICO"
    counterpart_role = "TECNICO" if is_client else "CLIENTE"

    clean_id = _canonical_solicitud_id(solicitud_id)
    reviews = list(
        db.scalars(
            select(ServiceReview).where(
                ServiceReview.tipo_solicitud == adapter.tipo_solicitud,
                ServiceReview.solicitud_id.in_(_solicitud_id_candidates(solicitud_id)),
            )
        ).all()
    )

    my_rev = next((r for r in reviews if r.rol_evaluador == my_role), None)
    other_rev = next((r for r in reviews if r.rol_evaluador == counterpart_role), None)

    my_rev_serialized = _serialize_review(my_rev, db) if my_rev else None

    # Doble ciego: solo exponer la reseña de la contraparte si está PUBLICADA
    other_rev_serialized = None
    counterpart_has_reviewed = other_rev is not None
    if other_rev and other_rev.estado_publicacion == "PUBLICADA":
        other_rev_serialized = _serialize_review(other_rev, db)

    general_status: Literal["NINGUNA", "PENDIENTE", "PUBLICADA"] = "NINGUNA"
    if my_rev and my_rev.estado_publicacion == "PUBLICADA":
        general_status = "PUBLICADA"
    elif my_rev:
        general_status = "PENDIENTE"

    return ServiceReviewsStatusOut(
        tipo_solicitud=adapter.tipo_solicitud,
        solicitud_id=clean_id,
        servicio_completado=adapter.is_completed(record),
        mi_resena=my_rev_serialized,
        contraparte_resena=other_rev_serialized,
        contraparte_ha_calificado=counterpart_has_reviewed,
        estado_publicacion_general=general_status,
    )


def _apply_evaluated_role_filter(stmt, rol_evaluado: Optional[Literal["TECNICO", "CLIENTE"]]):
    if rol_evaluado == "TECNICO":
        return stmt.where(ServiceReview.rol_evaluador == "CLIENTE")
    if rol_evaluado == "CLIENTE":
        return stmt.where(ServiceReview.rol_evaluador == "TECNICO")
    return stmt


def get_user_reputation_summary(
    user_id: str,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
    *,
    rol_evaluado: Optional[Literal["TECNICO", "CLIENTE"]] = None,
) -> ReputationSummaryOut:
    """Calcula y devuelve el resumen de reputación pública de un usuario (opcionalmente filtrado por rol evaluado)."""
    sync_expired_reviews(db, clock_fn=clock_fn)

    target_user = _find_user_by_id_or_firebase_uid(user_id, db)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    query = select(ServiceReview).where(
        ServiceReview.evaluado_id == target_user.id,
        ServiceReview.estado_publicacion == "PUBLICADA",
    )
    query = _apply_evaluated_role_filter(query, rol_evaluado)
    published_reviews = list(db.scalars(query).all())

    total = len(published_reviews)
    distribution = {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}

    if total == 0:
        return ReputationSummaryOut(
            usuario_id=str(target_user.id),
            promedio=None,
            total_resenas=0,
            sin_calificacion=True,
            distribucion_estrellas=distribution,
        )

    for r in published_reviews:
        k = str(r.puntuacion)
        distribution[k] = distribution.get(k, 0) + 1

    avg = round(float(sum(r.puntuacion for r in published_reviews)) / total, 2)

    return ReputationSummaryOut(
        usuario_id=str(target_user.id),
        promedio=avg,
        total_resenas=total,
        sin_calificacion=False,
        distribucion_estrellas=distribution,
    )


def list_user_published_reviews(
    user_id: str,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
    *,
    rol_evaluado: Optional[Literal["TECNICO", "CLIENTE"]] = None,
) -> dict[str, Any]:
    """Devuelve la lista de reseñas públicas de un usuario junto con su resumen de reputación."""
    summary = get_user_reputation_summary(user_id, db, clock_fn=clock_fn, rol_evaluado=rol_evaluado)
    target_user = _find_user_by_id_or_firebase_uid(user_id, db)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    query = select(ServiceReview).where(
        ServiceReview.evaluado_id == target_user.id,
        ServiceReview.estado_publicacion == "PUBLICADA",
    )
    query = _apply_evaluated_role_filter(query, rol_evaluado)
    published = list(
        db.scalars(
            query.order_by(ServiceReview.publicada_en.desc(), ServiceReview.creada_en.desc())
        ).all()
    )
    serialized_list = [_serialize_review(r, db) for r in published]

    return {
        "usuario_id": str(target_user.id),
        "promedio": summary.promedio,
        "total_resenas": summary.total_resenas,
        "sin_calificacion": summary.sin_calificacion,
        "distribucion_estrellas": summary.distribucion_estrellas,
        "resenas": serialized_list,
    }


# --- ENDPOINTS FASTAPI ---


@router.post("", response_model=SubmitReviewResponseOut, status_code=status.HTTP_201_CREATED)
def submit_service_review_endpoint(
    payload: CreateReviewSchema,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return create_service_review(payload, claims, db)


@router.get("/service/{tipo_solicitud}/{solicitud_id}", response_model=ServiceReviewsStatusOut)
def get_service_reviews_status_endpoint(
    tipo_solicitud: str,
    solicitud_id: str,
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_service_reviews_status_data(tipo_solicitud, solicitud_id, claims, db)


@router.get("/users/{user_id}/reputation", response_model=ReputationSummaryOut)
def get_user_reputation_endpoint(
    user_id: str,
    response: Response,
    rol_evaluado: Optional[Literal["TECNICO", "CLIENTE"]] = None,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_user_reputation_summary(user_id, db, rol_evaluado=rol_evaluado)


@router.get("/users/{user_id}", response_model=UserPublicReviewsOut)
def get_user_public_reviews_endpoint(
    user_id: str,
    response: Response,
    rol_evaluado: Optional[Literal["TECNICO", "CLIENTE"]] = None,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_user_published_reviews(user_id, db, rol_evaluado=rol_evaluado)


@router.post("/sync-expirations", response_model=SyncExpirationsOut)
def trigger_sync_expirations_endpoint(
    response: Response,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    count = sync_expired_reviews(db)
    return {"publicadas": count, "mensaje": f"Se publicaron {count} reseñas vencidas de 48h."}
