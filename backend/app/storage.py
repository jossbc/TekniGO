import hashlib
import hmac
import os
import urllib.error
import urllib.request
import uuid
import re
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

PRIVATE_BUCKET = "documentos-identidad"
DEFAULT_LOCAL_DIR = Path(__file__).resolve().parent.parent / ".private_storage"


@dataclass(frozen=True)
class StoredDocument:
    ruta_privada: str
    content_type: str
    tamano_bytes: int
    sha256: str


def detect_mime(content: bytes) -> str | None:
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 5 and content[:5] == b"%PDF-":
        return "application/pdf"
    return None


def validate_document_bytes(content: bytes, declared_content_type: str | None, label: str) -> str:
    if not content:
        raise HTTPException(400, f"El archivo de {label} está vacío")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(400, f"El archivo de {label} supera el tamaño máximo permitido de 5 MB")

    real_mime = detect_mime(content)
    if real_mime not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"El archivo de {label} no es válido (solo JPG, PNG, WEBP o PDF)")

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


def _key_material(secret_key: str | None = None) -> bytes:
    secret = secret_key if secret_key is not None else os.getenv("PRIVATE_STORAGE_SECRET", "")
    if len(secret.encode("utf-8")) < 32 or secret == "teknigo-private-storage-key":
        raise HTTPException(503, "Configura una clave privada de cifrado segura de al menos 32 bytes")
    return secret.encode("utf-8")


def encrypt_bytes(data: bytes, secret_key: str | None = None) -> bytes:
    key = hashlib.sha256(b"TKGO2:" + _key_material(secret_key)).digest()
    nonce = os.urandom(12)
    return b"TKGO2" + nonce + AESGCM(key).encrypt(nonce, data, b"TKGO2")


def decrypt_bytes(payload: bytes, secret_key: str | None = None) -> bytes:
    if payload[:5] == b"TKGO2":
        key = hashlib.sha256(b"TKGO2:" + _key_material(secret_key)).digest()
        if len(payload) < 33:
            raise ValueError("Archivo privado incompleto")
        try:
            return AESGCM(key).decrypt(payload[5:17], payload[17:], b"TKGO2")
        except InvalidTag:
            raise ValueError("No se pudo verificar la integridad del archivo privado") from None
    if len(payload) < 5 + 16 + 32 or payload[:5] != b"TKGO1":
        raise ValueError("Formato de archivo privado inválido")
    # Legacy reads require the original key explicitly; never try a default key.
    _key_material(secret_key)
    legacy_secret = secret_key if secret_key is not None else os.getenv("PRIVATE_STORAGE_LEGACY_SECRET") or os.getenv("PRIVATE_STORAGE_SECRET")
    key_material = legacy_secret.encode("utf-8")
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
        self.local_override = base_dir is not None

    def _supabase_config(self) -> tuple[str, str] | None:
        if self.local_override:
            return None
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url and not key:
            if os.getenv("PRIVATE_STORAGE_BACKEND") == "local":
                return None
            raise HTTPException(503, "Configura el almacenamiento privado de Supabase")
        parsed = urlsplit(url)
        if not key or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise HTTPException(503, "Configuración del almacenamiento privado incompleta o inválida")
        return url, key

    def _local_target(self, object_key: str) -> Path:
        target = (self.base_dir / object_key).resolve()
        if not target.is_relative_to(self.base_dir.resolve()):
            raise ValueError("Ruta privada inválida")
        return target

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

        config = self._supabase_config()
        if config:
            self._upload_supabase(*config, object_key, encrypted)
        else:
            target = self._local_target(object_key)
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
        if not re.fullmatch(r"identidad/[0-9a-f-]{36}/(?:dni_frontal|dni_reverso|selfie)_[0-9a-f]{32}\.(?:jpg|png|webp)\.enc", object_key):
            raise ValueError("Ruta privada inválida")
        config = self._supabase_config()
        if config:
            payload = self._download_supabase(*config, object_key)
        else:
            payload = self._local_target(object_key).read_bytes()
        try:
            return decrypt_bytes(payload)
        except ValueError:
            raise HTTPException(502, "No se pudo comprobar la integridad del documento privado") from None

    def _download_supabase(self, base_url: str, service_key: str, object_key: str) -> bytes:
        req = urllib.request.Request(
            f"{base_url}/storage/v1/object/authenticated/{PRIVATE_BUCKET}/{object_key}",
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
        )
        try:
            with _private_urlopen(req) as response:
                if response.status != 200:
                    raise HTTPException(502, "No se pudo recuperar el documento privado")
                payload = response.read(MAX_DOCUMENT_BYTES + 1025)
                if len(payload) > MAX_DOCUMENT_BYTES + 1024:
                    raise HTTPException(502, "Documento privado demasiado grande")
                return payload
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404:
                raise FileNotFoundError("Archivo privado no encontrado") from None
            raise HTTPException(502, "No se pudo recuperar el documento privado") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise HTTPException(502, "Error de conexión con el almacenamiento privado") from None

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
                "x-upsert": "false",
            },
        )
        try:
            with _private_urlopen(req) as resp:
                if resp.status not in (200, 201):
                    raise HTTPException(502, "No se pudo almacenar el documento de forma privada")
        except urllib.error.HTTPError as error:
            error.close()
            raise HTTPException(502, "No se pudo almacenar el documento de forma privada") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise HTTPException(502, "Error de conexión con el almacenamiento privado") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _private_urlopen(request):
    # Do not forward backend credentials to a redirect destination.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=15)

