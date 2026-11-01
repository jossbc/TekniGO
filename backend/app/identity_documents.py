import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as default_email_policy

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, User
from app.security import get_current_user
from app.storage import PrivateDocumentStorage, validate_document_bytes

router = APIRouter()


@dataclass
class UploadedPart:
    content: bytes
    content_type: str | None = None


def _get_user(claims: dict, db: Session, *, lock: bool = False) -> User:
    query = select(User).where(User.firebase_uid == claims["uid"])
    if lock:
        query = query.with_for_update()
    user = db.scalar(query.execution_options(populate_existing=True))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    return user


def _require_admin(claims: dict, db: Session) -> User:
    user = _get_user(claims, db)
    if not any(role.rol == "ADMIN" for role in user.roles):
        raise HTTPException(403, "Solo administración puede acceder a los archivos sensibles")
    return user


def _serialize_doc(doc: IdentityDocument) -> dict:
    # Nunca expone ruta_privada ni contenido al cliente; solo estado y observaciones.
    return {
        "id": str(doc.id),
        "tipo": doc.tipo,
        "estado": doc.estado,
        "observaciones": doc.observaciones,
        "creado_en": doc.creado_en.isoformat() if doc.creado_en else None,
    }


def _parse_multipart_parts(content_type_header: str, body: bytes) -> dict[str, UploadedPart]:
    if not content_type_header.lower().startswith("multipart/form-data"):
        raise HTTPException(400, "Se requiere envío multipart/form-data")
    raw_headers = f"Content-Type: {content_type_header}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    msg = BytesParser(policy=default_email_policy).parsebytes(raw_headers + body)
    if not msg.is_multipart():
        raise HTTPException(400, "Formulario multipart inválido")

    parts: dict[str, UploadedPart] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            payload = part.get_payload(decode=True) or b""
            parts[name] = UploadedPart(content=payload, content_type=part.get_content_type())
    return parts


def submit_identity_documents(
    *,
    dni_frontal: UploadedPart,
    dni_reverso: UploadedPart,
    selfie: UploadedPart,
    claims: dict,
    db: Session,
    storage: PrivateDocumentStorage | None = None,
) -> dict:
    user = _get_user(claims, db, lock=True)
    existing_docs = db.scalars(
        select(IdentityDocument.id).where(IdentityDocument.usuario_id == user.id)
    ).all()
    if existing_docs and user.estado_identidad == "VERIFICADO":
        raise HTTPException(409, "Tu identidad ya fue verificada")
    if existing_docs and user.estado_identidad == "EN_REVISION":
        raise HTTPException(409, "Ya tienes una solicitud de verificación pendiente de revisión")

    store = storage or PrivateDocumentStorage()
    items = [
        ("DNI_FRONTAL", "el DNI (frente)", dni_frontal),
        ("DNI_REVERSO", "el DNI (reverso)", dni_reverso),
        ("SELFIE", "la selfie", selfie),
    ]

    # Validate the whole submission before storing any of its files.
    for _, label, part in items:
        validate_document_bytes(part.content, part.content_type, label)
    version = (db.scalar(select(func.max(IdentityVerificationRequest.version)).where(
        IdentityVerificationRequest.usuario_id == user.id,
    )) or 0) + 1
    request_id = uuid.uuid4()

    stored_docs: list[IdentityDocument] = []
    for doc_type, label, part in items:
        saved = store.store(
            user_id=user.id,
            doc_type=doc_type,
            content=part.content,
            declared_content_type=part.content_type,
            label=label,
        )
        stored_docs.append(
            IdentityDocument(
                usuario_id=user.id,
                solicitud_id=request_id,
                tipo=doc_type,
                ruta_privada=saved.ruta_privada,
                content_type=saved.content_type,
                tamano_bytes=saved.tamano_bytes,
                sha256=saved.sha256,
                estado="PENDIENTE",
            )
        )

    try:
        db.add(IdentityVerificationRequest(id=request_id, usuario_id=user.id, version=version))
        db.flush()
        db.add_all(stored_docs)
        db.add(IdentityVerificationResult(
            solicitud_id=request_id, estado="EN_REVISION",
            motivo="Documentos recibidos. Pendientes de verificación de identidad.",
            proceso_responsable="CARGA_DOCUMENTAL", version_proceso="1",
        ))
        user.estado_identidad = "EN_REVISION"
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya se registró un envío. Actualiza el estado antes de reintentar.") from None
    for doc in stored_docs:
        db.refresh(doc)
    if any(role.rol == 'CLIENTE' for role in user.roles):
        from app.identity_processing import process_current_identity
        process_current_identity(claims, db, storage=store)
    return get_my_identity_documents(claims, db)


def get_my_identity_documents(claims: dict, db: Session) -> dict:
    user = _get_user(claims, db)
    current = db.scalar(select(IdentityVerificationRequest).where(
        IdentityVerificationRequest.usuario_id == user.id,
    ).order_by(IdentityVerificationRequest.version.desc()).limit(1))
    docs = db.scalars(
        select(IdentityDocument)
        .where(IdentityDocument.usuario_id == user.id)
        .where(IdentityDocument.solicitud_id == current.id if current else False)
        .order_by(IdentityDocument.creado_en.asc())
    ).all()
    effective_status = user.estado_identidad if docs else "PENDIENTE"
    processing = db.scalar(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id == current.id,
        IdentityVerificationResult.proceso_responsable == 'PROCESAMIENTO_IDENTIDAD',
    ).order_by(IdentityVerificationResult.creado_en.desc()).limit(1)) if current else None
    return {
        "estado_identidad": effective_status,
        "solicitud_id": str(current.id) if current else None,
        "version": current.version if current else None,
        "documentos": [_serialize_doc(doc) for doc in docs],
        "procesamiento": {
            "estado": processing.estado, "observacion": processing.motivo,
            "fecha": processing.creado_en.isoformat(),
        } if processing else None,
    }


def get_my_identity_history(claims: dict, db: Session) -> dict:
    user = _get_user(claims, db)
    requests = db.scalars(select(IdentityVerificationRequest).where(
        IdentityVerificationRequest.usuario_id == user.id,
    ).order_by(IdentityVerificationRequest.version.desc())).all()
    ids = [request.id for request in requests]
    docs = db.scalars(select(IdentityDocument).where(
        IdentityDocument.solicitud_id.in_(ids), IdentityDocument.usuario_id == user.id,
    ).order_by(IdentityDocument.creado_en, IdentityDocument.id)).all() if ids else []
    results = db.scalars(select(IdentityVerificationResult).where(
        IdentityVerificationResult.solicitud_id.in_(ids),
    ).order_by(IdentityVerificationResult.creado_en, IdentityVerificationResult.id)).all() if ids else []
    return {"solicitudes": [{
        "id": str(request.id), "version": request.version,
        "creado_en": request.creado_en.isoformat(),
        "documentos": [_serialize_doc(doc) for doc in docs if doc.solicitud_id == request.id],
        "resultados": [{
            "id": str(result.id), "estado": result.estado, "motivo": result.motivo,
            "proceso_responsable": result.proceso_responsable,
            "version_proceso": result.version_proceso, "creado_en": result.creado_en.isoformat(),
        } for result in results if result.solicitud_id == request.id],
    } for request in requests]}


def get_document_content_for_admin(
    document_id: uuid.UUID,
    claims: dict,
    db: Session,
    storage: PrivateDocumentStorage | None = None,
) -> tuple[bytes, str]:
    _require_admin(claims, db)
    doc = db.get(IdentityDocument, document_id)
    if doc is None:
        raise HTTPException(404, "No se encontró el documento")
    store = storage or PrivateDocumentStorage()
    try:
        raw_bytes = store.read_private(doc.ruta_privada)
    except FileNotFoundError:
        raise HTTPException(404, "Archivo privado no encontrado") from None
    return raw_bytes, doc.content_type


@router.post("/users/me/identity-documents", status_code=201)
async def upload_identity_documents_endpoint(
    request: Request,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.body()
    parts = _parse_multipart_parts(request.headers.get("content-type", ""), body)
    for required in ("dni_frontal", "dni_reverso", "selfie"):
        if required not in parts:
            raise HTTPException(400, f"Falta adjuntar el archivo '{required}'")
    return submit_identity_documents(
        dni_frontal=parts["dni_frontal"],
        dni_reverso=parts["dni_reverso"],
        selfie=parts["selfie"],
        claims=claims,
        db=db,
    )


@router.get("/users/me/identity-documents")
def list_my_identity_documents_endpoint(
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_my_identity_documents(claims, db)


@router.get("/users/me/identity-history")
def list_my_identity_history_endpoint(
    claims=Depends(get_current_user), db: Session = Depends(get_db),
):
    return get_my_identity_history(claims, db)


@router.post("/users/me/identity-verification")
def process_my_identity_endpoint(
    claims=Depends(get_current_user), db: Session = Depends(get_db),
):
    from app.identity_processing import process_current_identity
    process_current_identity(claims, db)
    return get_my_identity_documents(claims, db)


@router.get("/admin/identity-documents/{document_id}/content")
def download_identity_document_admin_endpoint(
    document_id: uuid.UUID,
    claims=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content, media_type = get_document_content_for_admin(document_id, claims, db)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store, private"},
    )
