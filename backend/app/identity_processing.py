"""Server-owned basic identity verification under the revised CA-04."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import logging
import os
import uuid
from typing import Protocol

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity_documents import _get_user
from app.models import DocumentReviewDecision, IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, TechnicianProfile
from app.storage import PrivateDocumentStorage, validate_document_bytes

PROCESS = 'PROCESAMIENTO_IDENTIDAD'
PROCESS_VERSION = '2'
REQUIRED_CHECKS = ('documento_legible', 'datos_coinciden', 'rostro_coincide')
RETRY_SECONDS = 60
logger = logging.getLogger('uvicorn.error')


class IdentityProcessingRetry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    solicitud_id: uuid.UUID


def identity_processing_status(db: Session, request_id: uuid.UUID | None) -> dict | None:
    if request_id is None:
        return None
    result = db.scalar(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == request_id,
        IdentityVerificationResult.proceso_responsable.in_([PROCESS, "REVISION_ADMINISTRATIVA_IDENTIDAD", "IDENTIDAD_REUTILIZADA"]),
    ).order_by(IdentityVerificationResult.creado_en.desc()).limit(1))
    return {'estado': result.estado, 'observacion': result.motivo,
            'fecha': result.creado_en.isoformat(), 'proceso_responsable': result.proceso_responsable} if result else None


@dataclass(frozen=True)
class IdentityChecks:
    documento_legible: bool | None = None
    datos_coinciden: bool | None = None
    rostro_coincide: bool | None = None
    documento_autentico: bool | None = None
    vida_confirmada: bool | None = None


class IdentityProcessor(Protocol):
    """Trusted server adapter, never a browser-supplied decision.

    A real adapter must enforce a finite provider timeout and return None for
    controls it cannot substantiate. A camera photo alone is not liveness proof.
    """
    name: str
    version: str

    def evaluate(self, *, dni: str, nombre_completo: str, documents: dict[str, bytes]) -> IdentityChecks: ...


def configured_processor() -> IdentityProcessor | None:
    if os.getenv('IDENTITY_PROCESSOR', '').strip().lower() == 'python':
        from app.python_identity import PythonIdentityProcessor
        return PythonIdentityProcessor()
    return None


def _decision(checks: IdentityChecks) -> tuple[str, str]:
    values = asdict(checks)
    if any(value is not None and type(value) is not bool for value in values.values()):
        raise ValueError('Invalid processor response')
    reasons = {
        'documento_legible': 'El documento no es legible. Envía fotografías claras de ambos lados.',
        'datos_coinciden': 'Los datos del DNI no coinciden con los registrados. Revisa tus datos y documentos.',
        'rostro_coincide': 'No se pudo confirmar que el rostro del DNI corresponda con la selfie.',
        'documento_autentico': 'No se pudo confirmar la autenticidad del documento.',
        'vida_confirmada': 'No se superó la comprobación de vida. Será necesaria una nueva verificación.',
    }
    for field, message in reasons.items():
        if values[field] is False:
            return 'RECHAZADO', message
    if any(values[field] is None for field in REQUIRED_CHECKS):
        return 'INCIERTO', 'La evaluación no es concluyente o faltan controles. Tu identidad sigue pendiente; no estás habilitado para contratar.'
    return 'VERIFICADO', 'Verificación básica completada: los datos del DNI y el rostro coinciden. Para contratar también necesitas el correo confirmado.'


def process_current_identity(
    claims: dict, db: Session, *, storage: PrivateDocumentStorage | None = None,
    processor: IdentityProcessor | None = None,
    request_id: uuid.UUID | None = None,
) -> IdentityVerificationResult:
    user = _get_user(claims, db, lock=True)
    roles = {role.rol for role in user.roles}
    if not roles.intersection({'CLIENTE', 'TECNICO'}):
        raise HTTPException(403, 'La cuenta no tiene un rol habilitado para verificar identidad')
    technician = db.get(TechnicianProfile, user.id) if 'TECNICO' in roles else None
    if 'TECNICO' in roles and technician is None:
        raise HTTPException(404, 'No se encontró tu perfil de técnico')
    request = db.scalar(select(IdentityVerificationRequest).where(
        IdentityVerificationRequest.usuario_id == user.id,
    ).order_by(IdentityVerificationRequest.version.desc()).limit(1))
    if request is None:
        raise HTTPException(409, 'Primero envía tu DNI por ambos lados y tu selfie')
    if request_id is not None and request.id != request_id:
        raise HTTPException(409, 'El expediente fue sustituido. Actualiza su estado antes de evaluar otra versión.')
    if db.scalar(select(DocumentReviewDecision.id).where(DocumentReviewDecision.solicitud_id == request.id).limit(1)):
        raise HTTPException(409, 'Este expediente ya tiene decisiones administrativas. Consulta tus observaciones o envía documentos corregidos.')
    reused = db.scalar(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == request.id,
        IdentityVerificationResult.proceso_responsable == "IDENTIDAD_REUTILIZADA",
    ).limit(1))
    if reused is not None:
        return reused
    adapter = processor if processor is not None else configured_processor()
    provider = {'nombre': adapter.name, 'version': adapter.version} if adapter else None
    previous = db.scalar(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == request.id,
        IdentityVerificationResult.proceso_responsable == PROCESS,
    ).order_by(IdentityVerificationResult.creado_en.desc()).limit(1))
    if previous:
        if previous.version_proceso == PROCESS_VERSION and previous.estado in ('VERIFICADO', 'RECHAZADO'):
            return previous
        if previous.version_proceso == PROCESS_VERSION and previous.estado == 'INCIERTO' and adapter is None and (previous.evidencia or {}).get('proveedor') is None:
            return previous
        timestamp = previous.creado_en
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - timestamp).total_seconds() < RETRY_SECONDS:
            raise HTTPException(429, 'Espera 60 segundos antes de reintentar la evaluación')
    docs = db.scalars(select(IdentityDocument).where(
        IdentityDocument.solicitud_id == request.id, IdentityDocument.usuario_id == user.id,
    )).all()
    expected = {'DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'}
    identity_docs = [doc for doc in docs if doc.tipo in expected]
    evidence = {
        'proveedor': provider,
        'documentos': [{'id': str(doc.id), 'tipo': doc.tipo, 'sha256': doc.sha256} for doc in identity_docs],
        'version_solicitud': request.version,
        'datos_registrados_sha256': hashlib.sha256(f'{user.dni}\n{user.nombre_completo}'.encode()).hexdigest(),
        'controles': asdict(IdentityChecks()),
        'controles_requeridos': list(REQUIRED_CHECKS),
        'alcance': 'VERIFICACION_BASICA_CA04',
        'requiere_revision_administrativa': technician is not None,
    }
    state, reason = 'INCIERTO', 'Los controles automáticos de identidad no están disponibles. Tu expediente sigue pendiente de verificación.'
    if {doc.tipo for doc in identity_docs} != expected or len(identity_docs) != 3:
        state, reason = 'INCIERTO', 'Faltan documentos requeridos. Es necesario enviar el DNI por ambos lados y la selfie.'
    elif adapter is not None:
        stage = 'lectura_privada'
        try:
            store = storage or PrivateDocumentStorage()
            content = {}
            for doc in identity_docs:
                raw = store.read_private(doc.ruta_privada)
                if len(raw) != doc.tamano_bytes or hashlib.sha256(raw).hexdigest() != doc.sha256:
                    raise ValueError('Document fingerprint mismatch')
                validate_document_bytes(raw, doc.content_type, 'documento de identidad')
                content[doc.tipo] = raw
            stage = 'evaluacion_modelos'
            checks = adapter.evaluate(dni=user.dni, nombre_completo=user.nombre_completo, documents=content)
            stage = 'validacion_resultado'
            if not isinstance(checks, IdentityChecks):
                raise ValueError('Invalid processor response')
            state, reason = _decision(checks)
            evidence['controles'] = asdict(checks)
            if adapter.name == 'PYTHON_LOCAL_OCR_FACE':
                evidence['modelos'] = adapter.model_versions
                evidence['diagnostico_facial'] = adapter.face_diagnostic
        except Exception as error:
            # Provider/storage messages may contain sensitive data; never persist them.
            logger.warning('Identidad: fallo en %s (%s)', stage, type(error).__name__)
            state, reason = 'ERROR', 'No pudimos completar la evaluación. Tus documentos se conservan y tu identidad sigue pendiente. Intenta nuevamente más tarde.'
    if state == 'VERIFICADO':
        reason = ('Los datos del DNI y el rostro coinciden. El expediente técnico sigue pendiente de revisión administrativa.'
                  if technician is not None else
                  'Verificación básica completada: los datos del DNI y el rostro coinciden. Para contratar también necesitas el correo y el teléfono confirmados.')
    evidence['requiere_revision_administrativa'] = technician is not None or state != 'VERIFICADO'
    result = IdentityVerificationResult(
        solicitud_id=request.id, estado=state, motivo=reason,
        proceso_responsable=PROCESS, version_proceso=PROCESS_VERSION, evidencia=evidence,
        creado_en=datetime.now(timezone.utc),
    )
    db.add(result)
    user.estado_identidad = state if state in ('VERIFICADO', 'RECHAZADO') else 'EN_REVISION'
    for doc in identity_docs:
        # A technical identity check never approves background certificates or
        # replaces an administrator's document decision.
        doc.estado = 'APROBADO' if state == 'VERIFICADO' and technician is None else 'RECHAZADO' if state == 'RECHAZADO' else 'PENDIENTE'
        doc.observaciones = reason
    if technician is not None:
        from app.technician_activation import sync_technician_verification_state
        db.flush()
        sync_technician_verification_state(user, technician, db,
            proceso_responsable=PROCESS, causa_override=reason)
    db.commit()
    db.refresh(result)
    return result
