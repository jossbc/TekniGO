"""Servicio central de habilitación y estados de verificación del técnico (HU-18 ST-01)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    IdentityDocument,
    IdentityVerificationRequest,
    TechnicianProfile,
    TechnicianStatusAudit,
    User,
)
from app.security import get_current_user

MAX_ANTIGUEDAD_ANTECEDENTES_DIAS = 90

REQUIRED_TECHNICIAN_DOCS: tuple[tuple[str, str], ...] = (
    ("DNI_FRONTAL", "DNI (frente)"),
    ("DNI_REVERSO", "DNI (reverso)"),
    ("SELFIE", "Selfie"),
    ("ANTECEDENTES_POLICIALES", "Antecedentes policiales"),
    ("ANTECEDENTES_PENALES", "Antecedentes penales"),
)

ANTECEDENTES_TYPES = frozenset({"ANTECEDENTES_POLICIALES", "ANTECEDENTES_PENALES"})
IDENTITY_BASIC_TYPES = frozenset({"DNI_FRONTAL", "DNI_REVERSO", "SELFIE"})


@dataclass
class RequirementStatus:
    codigo: str
    nombre: str
    aprobado: bool
    vigente: bool
    estado: str
    fecha_emision: date | None = None
    fecha_vencimiento: date | None = None
    motivo: str | None = None

    def to_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "nombre": self.nombre,
            "aprobado": self.aprobado,
            "vigente": self.vigente,
            "estado": self.estado,
            "fecha_emision": self.fecha_emision.isoformat() if self.fecha_emision else None,
            "fecha_vencimiento": self.fecha_vencimiento.isoformat() if self.fecha_vencimiento else None,
            "motivo": self.motivo,
        }


def compute_document_expiration(doc_type: str, fecha_emision: date | None) -> date | None:
    if doc_type in ANTECEDENTES_TYPES and fecha_emision is not None:
        return fecha_emision + timedelta(days=MAX_ANTIGUEDAD_ANTECEDENTES_DIAS)
    return None


def _latest_documents_by_type(db: Session, usuario_id: uuid.UUID) -> dict[str, IdentityDocument]:
    current_id = select(IdentityVerificationRequest.id).where(
        IdentityVerificationRequest.usuario_id == usuario_id,
    ).order_by(IdentityVerificationRequest.version.desc()).limit(1).scalar_subquery()
    docs = db.scalars(select(IdentityDocument).where(
        IdentityDocument.usuario_id == usuario_id,
        IdentityDocument.solicitud_id == current_id,
    )).all()
    latest: dict[str, IdentityDocument] = {}
    for doc in docs:
        latest[doc.tipo] = doc
    return latest


def _evaluate_single_document(
    doc_type: str,
    label: str,
    doc: IdentityDocument | None,
    today: date,
) -> tuple[RequirementStatus, bool]:
    """Devuelve el estado del requisito documental y si el documento cambió a VENCIDO."""
    if doc is None:
        return (
            RequirementStatus(
                codigo=doc_type,
                nombre=label,
                aprobado=False,
                vigente=False,
                estado="FALTANTE",
                motivo=f"Falta cargar {label}.",
            ),
            False,
        )

    vencimiento = doc.fecha_vencimiento
    if vencimiento is None and doc.tipo in ANTECEDENTES_TYPES and doc.fecha_emision_declarada is not None:
        vencimiento = compute_document_expiration(doc.tipo, doc.fecha_emision_declarada)

    expired_by_date = False
    if vencimiento is not None and vencimiento < today:
        expired_by_date = True
    elif doc.tipo in ANTECEDENTES_TYPES:
        if doc.fecha_emision_declarada is None or doc.fecha_emision_declarada > today:
            expired_by_date = True
        elif (today - doc.fecha_emision_declarada).days > MAX_ANTIGUEDAD_ANTECEDENTES_DIAS:
            expired_by_date = True

    marked_expired = False
    if expired_by_date and doc.estado in ("APROBADO", "PENDIENTE"):
        doc.estado = "VENCIDO"
        doc.observaciones = f"{label} venció el {vencimiento.isoformat() if vencimiento else today.isoformat()}."
        marked_expired = True

    if doc.estado == "VENCIDO":
        return (
            RequirementStatus(
                codigo=doc_type,
                nombre=label,
                aprobado=False,
                vigente=False,
                estado="VENCIDO",
                fecha_emision=doc.fecha_emision_declarada,
                fecha_vencimiento=vencimiento,
                motivo=doc.observaciones or f"{label} ha vencido y debe renovarse.",
            ),
            marked_expired,
        )

    if doc.estado == "INVALIDADO":
        return (
            RequirementStatus(
                codigo=doc_type,
                nombre=label,
                aprobado=False,
                vigente=False,
                estado="INVALIDADO",
                fecha_emision=doc.fecha_emision_declarada,
                fecha_vencimiento=vencimiento,
                motivo=doc.observaciones or f"{label} fue invalidado y requiere actualización.",
            ),
            False,
        )

    if doc.estado == "RECHAZADO":
        return (
            RequirementStatus(
                codigo=doc_type,
                nombre=label,
                aprobado=False,
                vigente=False,
                estado="RECHAZADO",
                fecha_emision=doc.fecha_emision_declarada,
                fecha_vencimiento=vencimiento,
                motivo=doc.observaciones or f"{label} fue rechazado.",
            ),
            False,
        )

    if doc.estado == "APROBADO":
        return (
            RequirementStatus(
                codigo=doc_type,
                nombre=label,
                aprobado=True,
                vigente=True,
                estado="APROBADO",
                fecha_emision=doc.fecha_emision_declarada,
                fecha_vencimiento=vencimiento,
                motivo=None,
            ),
            False,
        )

    return (
        RequirementStatus(
            codigo=doc_type,
            nombre=label,
            aprobado=False,
            vigente=not expired_by_date,
            estado="PENDIENTE",
            fecha_emision=doc.fecha_emision_declarada,
            fecha_vencimiento=vencimiento,
            motivo=f"{label} está pendiente de aprobación.",
        ),
        False,
    )


def is_operationally_enabled(user: User, profile: TechnicianProfile) -> bool:
    """CA-01, CA-02, CA-05: Habilitación operativa combinando todas las dimensiones."""
    return bool(
        user.telefono_verificado
        and profile.estado_verificacion == "VERIFICADO"
        and profile.disponible
        and not profile.suspendido
        and not profile.restriccion_financiera
    )


def evaluate_technician_requirements(
    user: User,
    profile: TechnicianProfile,
    db: Session,
    *,
    today: date | None = None,
) -> dict:
    """Evalúa los requisitos obligatorios del técnico sin confiar en flags del frontend."""
    eval_date = today or date.today()
    latest_docs = _latest_documents_by_type(db, user.id)

    requirements: list[RequirementStatus] = []
    any_doc_expired_or_invalid = False
    any_doc_rejected = False
    any_doc_missing = False
    any_doc_pending = False
    all_docs_approved_and_valid = True

    for doc_type, label in REQUIRED_TECHNICIAN_DOCS:
        req_status, _ = _evaluate_single_document(doc_type, label, latest_docs.get(doc_type), eval_date)
        requirements.append(req_status)
        if not (req_status.aprobado and req_status.vigente):
            all_docs_approved_and_valid = False
        if req_status.estado in ("VENCIDO", "INVALIDADO"):
            any_doc_expired_or_invalid = True
        elif req_status.estado == "RECHAZADO":
            any_doc_rejected = True
        elif req_status.estado == "FALTANTE":
            any_doc_missing = True
        elif req_status.estado == "PENDIENTE":
            any_doc_pending = True

    phone_ok = bool(user.telefono_verificado)
    phone_req = RequirementStatus(
        codigo="TELEFONO_VERIFICADO",
        nombre="Teléfono validado",
        aprobado=phone_ok,
        vigente=phone_ok,
        estado="APROBADO" if phone_ok else "PENDIENTE",
        motivo=None if phone_ok else "Falta validar la posesión de tu número de teléfono activo.",
    )
    requirements.append(phone_req)

    # Sincronizar estado_identidad del usuario si los 3 documentos de identidad básicos están aprobados
    basic_reqs = [r for r in requirements if r.codigo in IDENTITY_BASIC_TYPES]
    if all(r.aprobado and r.vigente for r in basic_reqs):
        user.estado_identidad = "VERIFICADO"
    elif any(r.estado == "RECHAZADO" for r in basic_reqs):
        user.estado_identidad = "RECHAZADO"
    else:
        user.estado_identidad = "EN_REVISION"

    # Determinar nuevo estado_verificacion según CA-01, CA-02, CA-04
    pending_reasons = [r.motivo for r in requirements if r.motivo]

    if all_docs_approved_and_valid and phone_ok:
        target_state = "VERIFICADO"
        restriction_reason = None
    elif any_doc_expired_or_invalid or (profile.estado_verificacion == "VERIFICADO" and not phone_ok):
        target_state = "RESTRINGIDO"
        expired_reasons = [
            r.motivo for r in requirements if r.estado in ("VENCIDO", "INVALIDADO") and r.motivo
        ]
        if not phone_ok and phone_req.motivo:
            expired_reasons.append(phone_req.motivo)
        restriction_reason = " ".join(r for r in expired_reasons if r)
    elif any_doc_rejected:
        target_state = "RECHAZADO"
        rejected_reasons = [r.motivo for r in requirements if r.estado == "RECHAZADO" and r.motivo]
        restriction_reason = (
            profile.observaciones_verificacion
            or " ".join(r for r in rejected_reasons if r)
            or "Uno o más documentos fueron rechazados."
        )
    elif not any_doc_missing and (any_doc_pending or not phone_ok):
        target_state = "EN_REVISION"
        restriction_reason = " ".join(r for r in pending_reasons if r)
    elif latest_docs:
        target_state = "RESTRINGIDO" if profile.estado_verificacion == "VERIFICADO" else "PENDIENTE"
        restriction_reason = " ".join(r for r in pending_reasons if r)
    else:
        target_state = "NO_VERIFICADO"
        restriction_reason = " ".join(r for r in pending_reasons if r)

    operational_blockers: list[str] = []
    if target_state != "VERIFICADO":
        if restriction_reason:
            operational_blockers.append(restriction_reason)
        else:
            operational_blockers.append("El técnico debe completar su verificación documental y de teléfono.")
    if not profile.disponible:
        operational_blockers.append("El técnico se encuentra configurado como No disponible.")
    if profile.suspendido:
        operational_blockers.append("La cuenta técnica presenta una suspensión activa.")
    if profile.restriccion_financiera:
        operational_blockers.append("La cuenta técnica presenta una restricción financiera activa.")

    enabled = bool(
        target_state == "VERIFICADO"
        and phone_ok
        and profile.disponible
        and not profile.suspendido
        and not profile.restriccion_financiera
    )

    return {
        "usuario_id": str(user.id),
        "estado_verificacion": target_state,
        "tecnico_verificado": target_state == "VERIFICADO",
        "telefono_verificado": phone_ok,
        "disponible": bool(profile.disponible),
        "suspendido": bool(profile.suspendido),
        "restriccion_financiera": bool(profile.restriccion_financiera),
        "estado_certificacion": profile.estado_certificacion or "NO_CERTIFICADO",
        "calificacion_promedio": (
            float(profile.calificacion_promedio) if profile.calificacion_promedio is not None else None
        ),
        "puede_recibir_contrataciones": enabled,
        "habilitado_operativamente": enabled,
        "motivo_restriccion": restriction_reason,
        "observaciones_verificacion": profile.observaciones_verificacion,
        "requisitos": [r.to_dict() for r in requirements],
        "requisitos_pendientes": [r.to_dict() for r in requirements if not (r.aprobado and r.vigente)],
        "motivos_bloqueo": operational_blockers,
    }


def sync_technician_verification_state(
    user: User,
    profile: TechnicianProfile,
    db: Session,
    *,
    proceso_responsable: str,
    actor_usuario_id: uuid.UUID | None = None,
    causa_override: str | None = None,
    today: date | None = None,
) -> dict:
    """Sincroniza el estado de verificación del técnico y registra auditoría si hubo transición (CA-01 a CA-06)."""
    previous_state = profile.estado_verificacion or "NO_VERIFICADO"
    previous_enabled = is_operationally_enabled(user, profile)

    evaluation = evaluate_technician_requirements(user, profile, db, today=today)
    new_state = evaluation["estado_verificacion"]
    new_enabled = evaluation["habilitado_operativamente"]

    profile.estado_verificacion = new_state
    profile.motivo_restriccion = evaluation["motivo_restriccion"]
    if new_state == "VERIFICADO" and profile.verificado_en is None:
        profile.verificado_en = datetime.now(timezone.utc)
    elif new_state != "VERIFICADO":
        profile.verificado_en = None

    if previous_state != new_state or previous_enabled != new_enabled:
        if causa_override:
            causa = causa_override
        elif new_state == "VERIFICADO":
            causa = (
                "Activación a Técnico Verificado: DNI (frente y reverso), selfie y ambos antecedentes "
                "aprobados y vigentes, y teléfono validado."
            )
        elif evaluation["motivo_restriccion"]:
            causa = evaluation["motivo_restriccion"]
        elif evaluation["motivos_bloqueo"]:
            causa = " ".join(evaluation["motivos_bloqueo"])
        else:
            causa = f"Transición de estado de {previous_state} a {new_state}."

        db.add(
            TechnicianStatusAudit(
                usuario_id=profile.usuario_id,
                estado_anterior=previous_state,
                estado_nuevo=new_state,
                habilitado_anterior=previous_enabled,
                habilitado_nuevo=new_enabled,
                causa=causa,
                actor_usuario_id=actor_usuario_id,
                proceso_responsable=proceso_responsable,
                creado_en=datetime.now(timezone.utc),
            )
        )

    return evaluation


def require_operational_technician(
    user: User,
    db: Session,
    *,
    proceso_responsable: str = "VALIDACION_OPERATIVA_API",
    today: date | None = None,
) -> TechnicianProfile:
    """CA-02, CA-04, CA-05: Repite las verificaciones al ejecutar una acción operativa."""
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    profile = db.get(TechnicianProfile, user.id)
    if profile is None:
        raise HTTPException(404, "No se encontró tu perfil de técnico")

    previous_state = profile.estado_verificacion
    evaluation = sync_technician_verification_state(
        user,
        profile,
        db,
        proceso_responsable=proceso_responsable,
        actor_usuario_id=user.id,
        today=today,
    )
    if previous_state != evaluation["estado_verificacion"]:
        db.commit()

    if not evaluation["habilitado_operativamente"]:
        detail = (
            " ".join(evaluation["motivos_bloqueo"])
            or "Tu perfil técnico se encuentra restringido para aceptar solicitudes o postular."
        )
        raise HTTPException(403, detail)

    return profile


def get_operational_technician(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TechnicianProfile:
    user = db.scalar(
        select(User)
        .where(User.firebase_uid == claims["uid"])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    return require_operational_technician(user, db)


def run_scheduled_expiration_review(
    db: Session,
    *,
    today: date | None = None,
    actor_usuario_id: uuid.UUID | None = None,
    proceso_responsable: str = "REVISION_PROGRAMADA_VENCIMIENTOS",
) -> dict:
    """HU-18 ST-05 (CA-04, CA-06): Revisión programada de vencimientos de requisitos obligatorios."""
    eval_date = today or date.today()
    rows = db.execute(
        select(User, TechnicianProfile)
        .join(TechnicianProfile, User.id == TechnicianProfile.usuario_id)
        .order_by(User.creado_en.asc(), User.id.asc())
    ).all()

    affected: list[dict] = []
    restricted_count = 0

    for user, profile in rows:
        prev_state = profile.estado_verificacion
        prev_enabled = is_operationally_enabled(user, profile)

        evaluation = sync_technician_verification_state(
            user,
            profile,
            db,
            proceso_responsable=proceso_responsable,
            actor_usuario_id=actor_usuario_id,
            today=eval_date,
        )

        new_state = evaluation["estado_verificacion"]
        new_enabled = evaluation["habilitado_operativamente"]

        if prev_state != new_state or prev_enabled != new_enabled:
            if new_state == "RESTRINGIDO":
                restricted_count += 1
            affected.append(
                {
                    "usuario_id": str(user.id),
                    "estado_anterior": prev_state,
                    "estado_nuevo": new_state,
                    "habilitado_anterior": prev_enabled,
                    "habilitado_nuevo": new_enabled,
                    "motivo_restriccion": profile.motivo_restriccion,
                }
            )

    return {
        "fecha_ejecucion": eval_date.isoformat(),
        "proceso_responsable": proceso_responsable,
        "total_evaluados": len(rows),
        "total_restringidos_por_vencimiento": restricted_count,
        "tecnicos_afectados": affected,
    }


