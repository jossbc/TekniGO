"""HU-36 ST-04: evidencia privada de cancelación/justificación detrás de una interfaz (DIP)."""
import re
from typing import Protocol, Sequence

from fastapi import HTTPException

from app.storage import PRIVATE_BUCKET, PrivateDocumentStorage

EVIDENCE_DOC_TYPE = "EVIDENCIA_CANCELACION"
MAX_EVIDENCE_FILES = 10
_REF = re.compile(
    rf"private://{re.escape(PRIVATE_BUCKET)}/identidad/(?P<owner>[0-9a-f-]{{36}})/"
    rf"{EVIDENCE_DOC_TYPE.lower()}_[0-9a-f]{{32}}\.(?:jpg|png|webp|pdf)\.enc"
)


class EvidenceStore(Protocol):
    def save(self, *, owner_id, content: bytes, content_type: str | None, label: str) -> dict: ...
    def ensure_owned(self, owner_id, refs: Sequence[str]) -> None: ...
    def read(self, ref: str) -> bytes: ...


class PrivateEvidenceStore:
    def __init__(self, storage: PrivateDocumentStorage | None = None):
        self._storage = storage or PrivateDocumentStorage()

    def save(self, *, owner_id, content, content_type, label):
        stored = self._storage.store(
            user_id=owner_id, doc_type=EVIDENCE_DOC_TYPE, content=content,
            declared_content_type=content_type, label=label,
        )
        return {
            "referencia": stored.ruta_privada,
            "content_type": stored.content_type,
            "tamano_bytes": stored.tamano_bytes,
        }

    def ensure_owned(self, owner_id, refs):
        if len(refs) > MAX_EVIDENCE_FILES:
            raise HTTPException(400, f"Máximo {MAX_EVIDENCE_FILES} evidencias.")
        for ref in refs:
            match = _REF.fullmatch(ref or "")
            if match is None or match.group("owner") != str(owner_id):
                raise HTTPException(400, "Una evidencia no es válida o no te pertenece.")

    def read(self, ref):
        return self._storage.read_private(ref)


def get_evidence_store() -> EvidenceStore:
    return PrivateEvidenceStore()