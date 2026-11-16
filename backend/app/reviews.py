"""Reviews and reputation module (EP-08: HU-44, HU-45, HU-46).

Cubre:
- HU-44: Calificar al técnico por parte del cliente.
- HU-45: Calificar al cliente por parte del técnico.
- HU-46: Publicación de reseñas, doble ciego, publicación bilateral y silenciosa a las 48h,
         cálculo de promedios, distribución de estrellas y reputación en Honduras (America/Tegucigalpa).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import cast, func, or_, select, String
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DirectRequest, OpenRequest, ServiceReview, TechnicianProfile, User
from app.security import get_current_user

TEGUCIGALPA_TZ = ZoneInfo("America/Tegucigalpa")

router = APIRouter(prefix="/reviews", tags=["Reviews & Reputation"])


# --- ESQUEMAS PYDANTIC ---


class CreateReviewSchema(BaseModel):
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


class ReputationSummaryOut(BaseModel):
    usuario_id: str
    promedio: Optional[float]
    total_resenas: int
    sin_calificacion: bool
    distribucion_estrellas: dict[str, int]


class ServiceReviewsStatusOut(BaseModel):
    tipo_solicitud: str
    solicitud_id: str
    servicio_completado: bool
    mi_resena: Optional[ServiceReviewOut] = None
    contraparte_resena: Optional[ServiceReviewOut] = None
    contraparte_ha_calificado: bool = False
    estado_publicacion_general: str  # 'NINGUNA', 'PENDIENTE', 'PUBLICADA'


# --- HELPERS ---


def to_tegucigalpa_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TEGUCIGALPA_TZ).isoformat()


def _clean_uuid(val: Any) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Identificador inválido: {val}",
        ) from None


def _find_user_by_id_or_firebase_uid(user_id_or_uid: Any, db: Session) -> Optional[User]:
    raw_str = str(user_id_or_uid).strip()
    clean_uuid_obj = None
    try:
        clean_uuid_obj = uuid.UUID(raw_str)
    except Exception:
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


def _resolve_service_request(
    tipo_solicitud: str,
    solicitud_id: str,
    db: Session,
) -> tuple[Any, User, User, datetime]:
    """Recupera la solicitud, el cliente, el técnico y la fecha de finalización."""
    clean_id = (solicitud_id or "").strip()
    norm_tipo = tipo_solicitud.strip().upper()

    if norm_tipo == "DIRECTA":
        bind = db.get_bind()
        lookup_id = clean_id
        if bind is not None and bind.dialect.name == "postgresql":
            try:
                lookup_id = str(uuid.UUID(clean_id))
            except ValueError:
                raise HTTPException(status_code=404, detail="Solicitud directa no encontrada.") from None

        record = db.scalar(select(DirectRequest).where(cast(DirectRequest.id, String) == str(lookup_id)))
        if not record:
            raise HTTPException(status_code=404, detail="Solicitud directa no encontrada.")

        client_user = _find_user_by_id_or_firebase_uid(record.cliente_id, db)
        tech_user = _find_user_by_id_or_firebase_uid(record.tecnico_id, db)

    elif norm_tipo == "ABIERTA":
        record = db.scalar(select(OpenRequest).where(OpenRequest.id == clean_id))
        if not record:
            raise HTTPException(status_code=404, detail="Solicitud abierta no encontrada.")

        client_user = _find_user_by_id_or_firebase_uid(record.cliente_id, db)
        tech_user = (
            _find_user_by_id_or_firebase_uid(record.tecnico_id, db)
            if record.tecnico_id
            else None
        )
    else:
        raise HTTPException(status_code=400, detail="Tipo de solicitud inválido. Usa DIRECTA o ABIERTA.")

    if not client_user or not tech_user:
        raise HTTPException(status_code=400, detail="La solicitud no tiene cliente o técnico asignado válido.")

    completada_en = (
        getattr(record, "completada_en", None)
        or getattr(record, "fecha_hora", None)
        or getattr(record, "creada_en", None)
        or datetime.now(timezone.utc)
    )
    if completada_en.tzinfo is None:
        completada_en = completada_en.replace(tzinfo=timezone.utc)

    return record, client_user, tech_user, completada_en


def _recompute_user_reputation(user_id: uuid.UUID, db: Session) -> Optional[float]:
    """Calcula el promedio publicado y actualiza TechnicianProfile si corresponde."""
    published_scores = list(
        db.scalars(
            select(ServiceReview.puntuacion).where(
                ServiceReview.evaluado_id == user_id,
                ServiceReview.estado_publicacion == "PUBLICADA",
            )
        ).all()
    )
    avg: Optional[float] = None
    if published_scores:
        avg = round(float(sum(published_scores)) / len(published_scores), 2)

    tech_profile = db.get(TechnicianProfile, user_id)
    if tech_profile is not None:
        tech_profile.calificacion_promedio = avg
    return avg


def sync_expired_reviews(
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
) -> int:
    """HU-46 CA-01/CA-02/CA-03: Sincronización silenciosa e idempotente de reseñas tras 48h.

    Si han transcurrido >= 48 horas desde fecha_completado_servicio y existe una reseña
    PENDIENTE, se publica automáticamente. No genera reseñas falsas.
    """
    now = clock_fn() if clock_fn else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    cutoff = now - timedelta(hours=48)

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


# --- FUNCIONES DE SERVICIO (UTILIZABLES EN TESTS Y ENDPOINTS) ---


def create_service_review(
    payload: CreateReviewSchema,
    claims: dict,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
) -> dict:
    """Crea una reseña cumpliendo todas las reglas de negocio de HU-44, HU-45 y HU-46."""
    current_user = _get_authenticated_user(claims, db)
    record, client_user, tech_user, completed_at = _resolve_service_request(
        payload.tipo_solicitud, payload.solicitud_id, db
    )

    # CA-01 y CA-05: El servicio debe estar en estado COMPLETADA
    estado_actual = (getattr(record, "estado", None) or "").strip().upper()
    if estado_actual != "COMPLETADA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo se pueden calificar servicios con estado COMPLETADA (estado actual: {estado_actual}).",
        )

    # Determinar si el usuario autenticado es el cliente o el técnico
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

    # CA-03: Unicidad (1 evaluación por rol por solicitud)
    clean_solicitud_id = str(payload.solicitud_id).strip()
    existing_my_review = db.scalar(
        select(ServiceReview).where(
            ServiceReview.tipo_solicitud == payload.tipo_solicitud,
            cast(ServiceReview.solicitud_id, String) == clean_solicitud_id,
            ServiceReview.rol_evaluador == rol_evaluador,
        )
    )
    if existing_my_review is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya has calificado este servicio. No es posible enviar evaluaciones duplicadas.",
        )

    now = clock_fn() if clock_fn else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Sincronizar vencimientos de 48h antes de verificar contraparte
    sync_expired_reviews(db, clock_fn=clock_fn)

    # Verificar si la contraparte ya envió su reseña
    counterpart_review = db.scalar(
        select(ServiceReview).where(
            ServiceReview.tipo_solicitud == payload.tipo_solicitud,
            cast(ServiceReview.solicitud_id, String) == clean_solicitud_id,
            ServiceReview.rol_evaluador == counterpart_role,
        )
    )

    # Si la contraparte ya evaluó o ya pasaron >= 48h desde que finalizó el servicio:
    hours_since_completion = (now - completed_at).total_seconds() / 3600.0
    should_publish_now = (counterpart_review is not None) or (hours_since_completion >= 48.0)

    initial_status = "PUBLICADA" if should_publish_now else "PENDIENTE"
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

    # Publicación bilateral instantánea si la contraparte estaba PENDIENTE
    if counterpart_review is not None and counterpart_review.estado_publicacion != "PUBLICADA":
        counterpart_review.estado_publicacion = "PUBLICADA"
        counterpart_review.publicada_en = now

    db.flush()

    # Recomputar promedios si se publicó
    if should_publish_now:
        _recompute_user_reputation(evaluado_id, db)
        if counterpart_review is not None:
            _recompute_user_reputation(counterpart_review.evaluado_id, db)

    db.commit()
    db.refresh(new_review)

    serialized = _serialize_review(new_review, db)

    if should_publish_now:
        msg = "Calificación enviada y publicada exitosamente al completarse la evaluación mutua."
    else:
        msg = "Tu calificación fue enviada con éxito. Será visible cuando la otra parte también califique o al transcurrir 48 horas."

    return {
        "resena": serialized,
        "estado_publicacion": initial_status,
        "publicada_inmediata": should_publish_now,
        "mensaje": msg,
    }


def get_service_reviews_status_data(
    tipo_solicitud: str,
    solicitud_id: str,
    claims: dict,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
) -> ServiceReviewsStatusOut:
    """Obtiene el estado de reseñas para una solicitud."""
    sync_expired_reviews(db, clock_fn=clock_fn)

    current_user = _get_authenticated_user(claims, db)
    record, client_user, tech_user, _ = _resolve_service_request(tipo_solicitud, solicitud_id, db)

    is_client = current_user.id == client_user.id or current_user.firebase_uid == client_user.firebase_uid
    is_tech = current_user.id == tech_user.id or current_user.firebase_uid == tech_user.firebase_uid

    if not is_client and not is_tech:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver el estado de calificación de este servicio.",
        )

    my_role = "CLIENTE" if is_client else "TECNICO"
    counterpart_role = "TECNICO" if is_client else "CLIENTE"

    clean_id = str(solicitud_id).strip()
    reviews = list(
        db.scalars(
            select(ServiceReview).where(
                ServiceReview.tipo_solicitud == tipo_solicitud.strip().upper(),
                cast(ServiceReview.solicitud_id, String) == clean_id,
            )
        ).all()
    )

    my_rev = next((r for r in reviews if r.rol_evaluador == my_role), None)
    other_rev = next((r for r in reviews if r.rol_evaluador == counterpart_role), None)

    my_rev_serialized = _serialize_review(my_rev, db) if my_rev else None

    # Doble ciego: solo mostrar la reseña de la contraparte si está PUBLICADA
    other_rev_serialized = None
    counterpart_has_reviewed = other_rev is not None
    if other_rev and other_rev.estado_publicacion == "PUBLICADA":
        other_rev_serialized = _serialize_review(other_rev, db)

    general_status = "NINGUNA"
    if my_rev and my_rev.estado_publicacion == "PUBLICADA":
        general_status = "PUBLICADA"
    elif my_rev:
        general_status = "PENDIENTE"

    estado_req = (getattr(record, "estado", None) or "").strip().upper()

    return ServiceReviewsStatusOut(
        tipo_solicitud=tipo_solicitud.strip().upper(),
        solicitud_id=clean_id,
        servicio_completado=(estado_req == "COMPLETADA"),
        mi_resena=my_rev_serialized,
        contraparte_resena=other_rev_serialized,
        contraparte_ha_calificado=counterpart_has_reviewed,
        estado_publicacion_general=general_status,
    )


def get_user_reputation_summary(
    user_id: str,
    db: Session,
    clock_fn: Optional[Callable[[], datetime]] = None,
) -> ReputationSummaryOut:
    """Calcula y devuelve el resumen de reputación pública de un usuario."""
    sync_expired_reviews(db, clock_fn=clock_fn)

    target_user = _find_user_by_id_or_firebase_uid(user_id, db)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    published_reviews = list(
        db.scalars(
            select(ServiceReview).where(
                ServiceReview.evaluado_id == target_user.id,
                ServiceReview.estado_publicacion == "PUBLICADA",
            )
        ).all()
    )

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
) -> dict:
    """Devuelve la lista de reseñas públicas de un usuario."""
    summary = get_user_reputation_summary(user_id, db, clock_fn=clock_fn)
    target_user = _find_user_by_id_or_firebase_uid(user_id, db)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    published = list(
        db.scalars(
            select(ServiceReview)
            .where(
                ServiceReview.evaluado_id == target_user.id,
                ServiceReview.estado_publicacion == "PUBLICADA",
            )
            .order_by(ServiceReview.publicada_en.desc(), ServiceReview.creada_en.desc())
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


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_service_review_endpoint(
    payload: CreateReviewSchema,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_service_review(payload, claims, db)


@router.get("/service/{tipo_solicitud}/{solicitud_id}", response_model=ServiceReviewsStatusOut)
def get_service_reviews_status_endpoint(
    tipo_solicitud: str,
    solicitud_id: str,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_service_reviews_status_data(tipo_solicitud, solicitud_id, claims, db)


@router.get("/users/{user_id}/reputation", response_model=ReputationSummaryOut)
def get_user_reputation_endpoint(
    user_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return get_user_reputation_summary(user_id, db)


@router.get("/users/{user_id}")
def get_user_public_reviews_endpoint(
    user_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    return list_user_published_reviews(user_id, db)


@router.post("/sync-expirations")
def trigger_sync_expirations_endpoint(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = sync_expired_reviews(db)
    return {"publicadas": count, "mensaje": f"Se publicaron {count} reseñas vencidas de 48h."}
