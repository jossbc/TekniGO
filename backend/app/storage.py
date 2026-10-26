import hashlib
import hmac
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

PRIVATE_BUCKET = "documentos-identidad"
DEFAULT_LOCAL_DIR = Path(__file__).resolve().parent.parent / ".private_storage"


@dataclass(frozen=True)
class StoredDocument:
    ruta_privada: str
    content_type: str
    tamano_bytes: int
    sha256: str


def detect_image_mime(content: bytes) -> str | None:
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_document_bytes(content: bytes, declared_content_type: str | None, label: str) -> str:
    if not content:
        raise HTTPException(400, f"El archivo de {label} está vacío")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(400, f"El archivo de {label} supera el tamaño máximo permitido de 5 MB")

    real_mime = detect_image_mime(content)
    if real_mime not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"El archivo de {label} no es una imagen válida (solo JPG, PNG o WEBP)")

    if declared_content_type and declared_content_type in ALLOWED_CONTENT_TYPES and declared_content_type != real_mime:
        raise HTTPException(400, f"El contenido real de {label} no coincide con el formato declarado")

    return real_mime


def _cipher_stream(data: bytes, secret: bytes, nonce: bytes) -> bytes:
    """Stream cipher basado en HMAC-SHA256 para cifrar en reposo el almacenamiento privado local."""
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.new(secret, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[offset : offset + 32]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ block[i]
        offset += len(chunk)
        counter += 1
    return bytes(out)


def encrypt_bytes(data: bytes, secret_key: str | None = None) -> bytes:
    key_material = (secret_key or os.getenv("PRIVATE_STORAGE_SECRET") or "teknigo-private-storage-key").encode("utf-8")
    enc_key = hashlib.sha256(b"enc:" + key_material).digest()
    mac_key = hashlib.sha256(b"mac:" + key_material).digest()
    nonce = os.urandom(16)
    ciphertext = _cipher_stream(data, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return b"TKGO1" + nonce + tag + ciphertext


def decrypt_bytes(payload: bytes, secret_key: str | None = None) -> bytes:
    if len(payload) < 5 + 16 + 32 or payload[:5] != b"TKGO1":
        raise ValueError("Formato de archivo privado inválido")
    key_material = (secret_key or os.getenv("PRIVATE_STORAGE_SECRET") or "teknigo-private-storage-key").encode("utf-8")
    enc_key = hashlib.sha256(b"enc:" + key_material).digest()
    mac_key = hashlib.sha256(b"mac:" + key_material).digest()
    nonce = payload[5:21]
    tag = payload[21:53]
    ciphertext = payload[53:]
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Integridad del archivo privado comprometida")
    return _cipher_stream(ciphertext, enc_key, nonce)


class PrivateDocumentStorage:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path(os.getenv("PRIVATE_STORAGE_DIR", str(DEFAULT_LOCAL_DIR)))

    def store(
        self,
        *,
        user_id: uuid.UUID,
        doc_type: str,
        content: bytes,
        declared_content_type: str | None,
        label: str,
    ) -> StoredDocument:
        real_mime = validate_document_bytes(content, declared_content_type, label)
        ext = ALLOWED_CONTENT_TYPES[real_mime]
        digest = hashlib.sha256(content).hexdigest()
        object_key = f"identidad/{user_id}/{doc_type.lower()}_{uuid.uuid4().hex}{ext}.enc"
        encrypted = encrypt_bytes(content)

        supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

        if supabase_url and supabase_key:
            self._upload_supabase(supabase_url, supabase_key, object_key, encrypted)
        else:
            target = (self.base_dir / object_key).resolve()
            if not str(target).startswith(str(self.base_dir.resolve())):
                raise HTTPException(400, "Ruta de almacenamiento inválida")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encrypted)

        return StoredDocument(
            ruta_privada=f"private://{PRIVATE_BUCKET}/{object_key}",
            content_type=real_mime,
            tamano_bytes=len(content),
            sha256=digest,
        )

    def read_private(self, ruta_privada: str) -> bytes:
        prefix = f"private://{PRIVATE_BUCKET}/"
        if not ruta_privada.startswith(prefix):
            raise ValueError("Ruta privada desconocida")
        object_key = ruta_privada[len(prefix) :]
        target = (self.base_dir / object_key).resolve()
        if not str(target).startswith(str(self.base_dir.resolve())) or not target.is_file():
            raise FileNotFoundError("Archivo privado no encontrado")
        return decrypt_bytes(target.read_bytes())

    def _upload_supabase(self, base_url: str, service_key: str, object_key: str, encrypted: bytes) -> None:
        url = f"{base_url}/storage/v1/object/{PRIVATE_BUCKET}/{object_key}"
        req = urllib.request.Request(
            url,
            data=encrypted,
            method="POST",
            headers={
                "Authorization": f"Bearer {service_key}",
                "apikey": service_key,
                "Content-Type": "application/octet-stream",
                "x-upsert": "true",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status not in (200, 201):
                    raise HTTPException(502, "No se pudo almacenar el documento de forma privada")
        except urllib.error.URLError:
            raise HTTPException(502, "Error de conexión con el almacenamiento privado") from None

