import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as default_email_policy

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IdentityDocument, User
from app.security import get_current_user
from app.storage import PrivateDocumentStorage

router = APIRouter()


@dataclass
class UploadedPart:
    content: bytes
    content_type: str | None = None


def _get_user(claims: dict, db: Session) -> User:
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
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
    user = _get_user(claims, db)
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
                tipo=doc_type,
                ruta_privada=saved.ruta_privada,
                content_type=saved.content_type,
                tamano_bytes=saved.tamano_bytes,
                sha256=saved.sha256,
                estado="PENDIENTE",
            )
        )

    db.execute(delete(IdentityDocument).where(IdentityDocument.usuario_id == user.id))
    db.add_all(stored_docs)
    user.estado_identidad = "EN_REVISION"
    db.commit()
    for doc in stored_docs:
        db.refresh(doc)

    return {
        "estado_identidad": user.estado_identidad,
        "documentos": [_serialize_doc(doc) for doc in stored_docs],
    }


def get_my_identity_documents(claims: dict, db: Session) -> dict:
    user = _get_user(claims, db)
    docs = db.scalars(
        select(IdentityDocument)
        .where(IdentityDocument.usuario_id == user.id)
        .order_by(IdentityDocument.creado_en.asc())
    ).all()
    effective_status = user.estado_identidad if docs else "PENDIENTE"
    return {
        "estado_identidad": effective_status,
        "documentos": [_serialize_doc(doc) for doc in docs],
    }


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
