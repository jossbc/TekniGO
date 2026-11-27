"""HU-47 ST-01/ST-05: sanitización estricta (CA-06) y plantillas Strategy/Builder por tipo de evento."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Protocol


EVENT_NUEVA_SOLICITUD_DIRECTA = "NUEVA_SOLICITUD_DIRECTA"
EVENT_SOLICITUD_DIRECTA_ACEPTADA = "SOLICITUD_DIRECTA_ACEPTADA"
EVENT_SOLICITUD_DIRECTA_RECHAZADA = "SOLICITUD_DIRECTA_RECHAZADA"
EVENT_NUEVA_OFERTA_ABIERTA = "NUEVA_OFERTA_ABIERTA"
EVENT_NUEVA_CONTRAOFERTA_ABIERTA = "NUEVA_CONTRAOFERTA_ABIERTA"
EVENT_PROPUESTA_RECHAZADA = "PROPUESTA_RECHAZADA"
EVENT_PROPUESTA_ADJUDICADA = "PROPUESTA_ADJUDICADA"
EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION = "PROPUESTA_DESCARTADA_POR_ADJUDICACION"
EVENT_SERVICIO_INICIADO = "SERVICIO_INICIADO"
EVENT_FINALIZACION_REPORTADA = "FINALIZACION_REPORTADA"
EVENT_RECEPCION_CONFIRMADA = "RECEPCION_CONFIRMADA"
EVENT_CIERRE_AUTOMATICO_48H = "CIERRE_AUTOMATICO_48H"
EVENT_SERVICIO_CANCELADO = "SERVICIO_CANCELADO"
EVENT_INCIDENCIA_ABIERTA = "INCIDENCIA_ABIERTA"
EVENT_INCIDENCIA_RESUELTA = "INCIDENCIA_RESUELTA"
EVENT_AJUSTE_PRESUPUESTO_SOLICITADO = "AJUSTE_PRESUPUESTO_SOLICITADO"
EVENT_AJUSTE_PRESUPUESTO_APROBADO = "AJUSTE_PRESUPUESTO_APROBADO"
EVENT_AJUSTE_PRESUPUESTO_RECHAZADO = "AJUSTE_PRESUPUESTO_RECHAZADO"
EVENT_VERIFICACION_APROBADA = "VERIFICACION_APROBADA"
EVENT_DOCUMENTACION_APROBADA = "DOCUMENTACION_APROBADA"
EVENT_VERIFICACION_RECHAZADA = "VERIFICACION_RECHAZADA"
EVENT_CERTIFICACION_APROBADA = "CERTIFICACION_APROBADA"
EVENT_CERTIFICACION_RECHAZADA = "CERTIFICACION_RECHAZADA"
EVENT_CERTIFICACION_REVOCADA = "CERTIFICACION_REVOCADA"
EVENT_LIQUIDACION_APROBADA = "LIQUIDACION_APROBADA"
EVENT_LIQUIDACION_RECHAZADA = "LIQUIDACION_RECHAZADA"
EVENT_LIQUIDACION_RECIBIDA = "LIQUIDACION_RECIBIDA"
EVENT_DESEMBOLSO_PAGADO = "DESEMBOLSO_PAGADO"
EVENT_DESEMBOLSO_RECHAZADO = "DESEMBOLSO_RECHAZADO"
EVENT_DESEMBOLSO_SOLICITADO = "DESEMBOLSO_SOLICITADO"
EVENT_RESTRICCION_OPERATIVA = "RESTRICCION_OPERATIVA"

PROPOSAL_LIVE_EVENT_TYPES = frozenset({
    EVENT_NUEVA_OFERTA_ABIERTA,
    EVENT_NUEVA_CONTRAOFERTA_ABIERTA,
    EVENT_PROPUESTA_RECHAZADA,
    EVENT_PROPUESTA_ADJUDICADA,
    EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION,
})

SENSITIVE_FIELD_NAMES = frozenset({
    "dni",
    "numero_dni",
    "identidad",
    "telefono",
    "cliente_telefono",
    "tecnico_telefono",
    "celular",
    "correo",
    "email",
    "direccion",
    "direccion_exacta",
    "ubicacion_exacta",
    "ruta",
    "ruta_privada",
    "file_path",
    "storage_path",
    "url_privada",
    "evidencia",
    "evidencias",
    "evidencias_privadas",
    "evidencia_url",
    "numero_cuenta",
    "numero_cuenta_cifrado",
    "numero_cuenta_hash",
    "cuenta_bancaria",
    "cuenta_enmascarada",
    "ultimos_cuatro",
    "comprobante",
    "comprobante_sha256",
    "referencia",
    "referencia_normalizada",
    "referencia_bancaria",
    "referencia_transferencia",
    "referencia_pasarela",
    "referencia_pasarela_ajuste",
    "referencia_pasarela_origen",
    "referencia_reembolso_pasarela",
    "sha256",
    "token",
    "password",
    "secret",
})

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DNI_SPACED_RE = re.compile(r"(?<![a-fA-F0-9-])\d{4}[-\s]\d{4}[-\s]\d{5}(?![a-fA-F0-9-])")
_GROUPED_DIGITS_RE = re.compile(r"(?<![a-fA-F0-9-])\d{4}(?:[-\s]\d{4}){1,5}(?![a-fA-F0-9-])")
_LONG_DIGITS_RE = re.compile(r"(?<![a-fA-F0-9-])\d{8,24}(?![a-fA-F0-9-])")
_EXTERNAL_OR_STORAGE_URI_RE = re.compile(r"(?:https?|gs|s3|private|file)://\S+", re.IGNORECASE)
_PRIVATE_ROUTE_RE = re.compile(
    r"(?:/[A-Za-z0-9_.-]+)*/(?:admin|private|uploads|documentos|evidencia|proof|bank-details|identity-documents|verification-documents|certifications)/\S*",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_DOC_FILE_RE = re.compile(r"\b[^\s/\\]+\.(?:pdf|png|jpe?g|webp|enc)\b", re.IGNORECASE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

REDACTED_MARKER = "[dato protegido]"


class NotificationPrivacySanitizer:
    """Capa explícita de sanitización para garantizar CA-06 en textos, rutas y metadatos."""

    @staticmethod
    def sanitize_text(value: Any, *, max_length: int = 500) -> str:
        text = str(value or "")
        stripped = text.strip()
        if _UUID_RE.fullmatch(stripped):
            return stripped
        text = _CONTROL_CHARS_RE.sub(" ", text)
        text = _EXTERNAL_OR_STORAGE_URI_RE.sub(REDACTED_MARKER, text)
        text = _PRIVATE_ROUTE_RE.sub(REDACTED_MARKER, text)
        text = _WINDOWS_PATH_RE.sub(REDACTED_MARKER, text)
        text = _DOC_FILE_RE.sub(REDACTED_MARKER, text)
        text = _DNI_SPACED_RE.sub(REDACTED_MARKER, text)
        text = _GROUPED_DIGITS_RE.sub(REDACTED_MARKER, text)
        text = _LONG_DIGITS_RE.sub(REDACTED_MARKER, text)
        text = " ".join(text.split()).strip()
        if len(text) > max_length:
            text = text[: max_length - 1].rstrip() + "…"
        return text

    @classmethod
    def sanitize_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls.sanitize_text(value, max_length=240)
        if isinstance(value, dict):
            return cls.sanitize_metadata(value)
        if isinstance(value, (list, tuple)):
            return [cls.sanitize_value(item) for item in value[:20]]
        return cls.sanitize_text(str(value), max_length=240)

    @classmethod
    def sanitize_metadata(cls, data: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        clean: dict[str, Any] = {}
        for raw_key, raw_val in data.items():
            key = str(raw_key).strip()
            if not key:
                continue
            key_lower = key.lower()
            if key_lower in SENSITIVE_FIELD_NAMES or any(
                frag in key_lower for frag in ("dni", "cuenta", "comprobante", "ruta_privada", "direccion_exacta", "telefono")
            ):
                continue
            clean[key] = cls.sanitize_value(raw_val)
        return clean

    @staticmethod
    def validate_internal_route(route: str | None) -> str:
        raw = str(route or "").strip()
        if (
            not raw
            or not raw.startswith("/")
            or raw.startswith("//")
            or ".." in raw
            or "://" in raw
            or _CONTROL_CHARS_RE.search(raw)
        ):
            return "/mi-cuenta"
        if len(raw) > 240:
            return "/mi-cuenta"
        base_path = raw.split("?", 1)[0].split("#", 1)[0]
        if _PRIVATE_ROUTE_RE.search(base_path) or base_path.endswith("/content"):
            return "/mi-cuenta"
        allowed_prefixes = (
            "/mis-solicitudes",
            "/mis-trabajos",
            "/tecnico",
            "/verificacion",
            "/perfil",
            "/admin",
            "/mi-cuenta",
            "/solicitudes",
        )
        if not any(
            base_path == prefix or (prefix != "/admin" and base_path.startswith(f"{prefix}/"))
            for prefix in allowed_prefixes
        ):
            return "/mi-cuenta"
        return raw

    @staticmethod
    def contains_sensitive_leak(text: str | None) -> bool:
        raw = str(text or "")
        return bool(
            _DNI_SPACED_RE.search(raw)
            or _GROUPED_DIGITS_RE.search(raw)
            or _LONG_DIGITS_RE.search(raw)
            or _EXTERNAL_OR_STORAGE_URI_RE.search(raw)
            or _PRIVATE_ROUTE_RE.search(raw)
            or _WINDOWS_PATH_RE.search(raw)
            or _DOC_FILE_RE.search(raw)
        )


@dataclass(frozen=True)
class NotificationDraft:
    titulo: str
    resumen: str
    enlace_ruta: str
    recurso_tipo: str | None = None
    recurso_id: str | None = None
    metadatos_seguros: dict[str, Any] = field(default_factory=dict)


class NotificationTemplateStrategy(Protocol):
    """Contrato (Strategy + OCP) para construir avisos sanitizados por categoría."""

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft: ...


def _safe_zone(data: dict[str, Any]) -> str:
    muni = NotificationPrivacySanitizer.sanitize_text(data.get("municipio") or "", max_length=80)
    sector = NotificationPrivacySanitizer.sanitize_text(data.get("sector") or "", max_length=100)
    if muni and sector:
        return f"{muni}, {sector}"
    return muni or sector or "tu zona de cobertura"


def _safe_money_fragment(data: dict[str, Any], key: str = "monto", prefix: str = " por ") -> str:
    raw = data.get(key)
    if raw is None or raw == "":
        return ""
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    return f"{prefix}L. {amount:.2f}"


def _request_route(role: str, solicitud_id: str, *, active_job: bool) -> str:
    clean_id = str(solicitud_id or "").strip()
    if role == "ADMIN":
        return "/admin?tab=incidents"
    if role == "TECNICO":
        return f"/tecnico/solicitudes/{clean_id}" if clean_id else "/tecnico/solicitudes"
    if active_job:
        return f"/mis-trabajos/{clean_id}" if clean_id else "/mis-trabajos"
    return f"/mis-solicitudes/{clean_id}" if clean_id else "/mis-solicitudes"


class DirectRequestCreatedTemplate:
    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        service_name = NotificationPrivacySanitizer.sanitize_text(
            data.get("servicio_nombre") or "servicio técnico", max_length=100
        )
        zone = _safe_zone(data)
        return NotificationDraft(
            titulo="Nueva solicitud directa recibida",
            resumen=f"Recibiste una nueva solicitud directa para {service_name} en {zone}.",
            enlace_ruta=_request_route("TECNICO", req_id, active_job=False),
            recurso_tipo="SOLICITUD_DIRECTA",
            recurso_id=req_id or None,
            metadatos_seguros={"modalidad": "DIRECTA", "solicitud_id": req_id},
        )


class DirectRequestDecisionTemplate:
    def __init__(self, accepted: bool):
        self.accepted = accepted

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        service_name = NotificationPrivacySanitizer.sanitize_text(
            data.get("servicio_nombre") or "tu solicitud", max_length=100
        )
        if self.accepted:
            return NotificationDraft(
                titulo="Solicitud directa aceptada",
                resumen=f"El técnico aceptó tu solicitud directa de {service_name}. Ya puedes dar seguimiento al servicio.",
                enlace_ruta=_request_route("CLIENTE", req_id, active_job=True),
                recurso_tipo="SOLICITUD_DIRECTA",
                recurso_id=req_id or None,
                metadatos_seguros={"modalidad": "DIRECTA", "solicitud_id": req_id, "estado": "ACEPTADA"},
            )
        return NotificationDraft(
            titulo="Solicitud directa rechazada",
            resumen=f"El técnico rechazó tu solicitud directa de {service_name}. Puedes consultar alternativas en el catálogo.",
            enlace_ruta=_request_route("CLIENTE", req_id, active_job=False),
            recurso_tipo="SOLICITUD_DIRECTA",
            recurso_id=req_id or None,
            metadatos_seguros={"modalidad": "DIRECTA", "solicitud_id": req_id, "estado": "CANCELADA"},
        )


class OpenRequestProposalTemplate:
    def __init__(self, is_counter: bool):
        self.is_counter = is_counter

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        monto_txt = _safe_money_fragment(data)
        if self.is_counter:
            titulo = "Nueva contraoferta recibida"
            resumen = f"Un técnico verificado envió una contraoferta{monto_txt} en tu solicitud abierta."
        else:
            titulo = "Nueva oferta en tu solicitud abierta"
            resumen = f"Un técnico verificado aceptó el presupuesto publicado{monto_txt} para tu solicitud abierta."
        return NotificationDraft(
            titulo=titulo,
            resumen=resumen,
            enlace_ruta=_request_route("CLIENTE", req_id, active_job=False),
            recurso_tipo="SOLICITUD_ABIERTA",
            recurso_id=req_id or None,
            metadatos_seguros={
                "modalidad": "ABIERTA",
                "solicitud_id": req_id,
                "es_evento_propuesta_vivo": True,
            },
        )


class ProposalRejectedTemplate:
    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        return NotificationDraft(
            titulo="Propuesta rechazada",
            resumen="El cliente rechazó tu propuesta en una solicitud abierta. Revisa otras oportunidades disponibles.",
            enlace_ruta="/tecnico/oportunidades",
            recurso_tipo="SOLICITUD_ABIERTA",
            recurso_id=req_id or None,
            metadatos_seguros={
                "modalidad": "ABIERTA",
                "solicitud_id": req_id,
                "es_evento_propuesta_vivo": True,
            },
        )


class ProposalAwardedTemplate:
    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        monto_txt = _safe_money_fragment(data)
        return NotificationDraft(
            titulo="¡Tu propuesta fue adjudicada!",
            resumen=f"El cliente seleccionó tu propuesta{monto_txt}. La contratación pasó a estado Aceptada.",
            enlace_ruta=_request_route("TECNICO", req_id, active_job=True),
            recurso_tipo="SOLICITUD_ABIERTA",
            recurso_id=req_id or None,
            metadatos_seguros={
                "modalidad": "ABIERTA",
                "solicitud_id": req_id,
                "estado": "ACEPTADA",
                "es_evento_propuesta_vivo": True,
            },
        )


class ProposalDiscardedByAwardTemplate:
    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        zone = _safe_zone(data)
        return NotificationDraft(
            titulo="Propuesta descartada por adjudicación",
            resumen=f"La solicitud abierta en {zone} fue adjudicada a otro técnico y tu propuesta quedó descartada.",
            enlace_ruta="/tecnico/oportunidades",
            recurso_tipo="SOLICITUD_ABIERTA",
            recurso_id=req_id or None,
            metadatos_seguros={
                "modalidad": "ABIERTA",
                "solicitud_id": req_id,
                "es_evento_propuesta_vivo": True,
            },
        )


class ServiceLifecycleTemplate:
    def __init__(self, *, titulo: str, resumen_cliente: str, resumen_tecnico: str, estado: str, active_job: bool):
        self.titulo = titulo
        self.resumen_cliente = resumen_cliente
        self.resumen_tecnico = resumen_tecnico
        self.estado = estado
        self.active_job = active_job

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        mod = str(data.get("modalidad") or "DIRECTA").strip().upper()
        summary = self.resumen_tecnico if recipient_role == "TECNICO" else self.resumen_cliente
        return NotificationDraft(
            titulo=self.titulo,
            resumen=summary,
            enlace_ruta=_request_route(recipient_role, req_id, active_job=self.active_job),
            recurso_tipo=f"SOLICITUD_{mod}",
            recurso_id=req_id or None,
            metadatos_seguros={"modalidad": mod, "solicitud_id": req_id, "estado": self.estado},
        )


class IncidentTemplate:
    def __init__(self, resolved: bool):
        self.resolved = resolved

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        mod = str(data.get("modalidad") or "DIRECTA").strip().upper()
        if not self.resolved:
            if recipient_role == "ADMIN":
                return NotificationDraft(
                    titulo="Nueva incidencia abierta",
                    resumen="Se registró una incidencia prioritaria en un servicio y requiere moderación administrativa.",
                    enlace_ruta="/admin?tab=incidents",
                    recurso_tipo="INCIDENCIA",
                    recurso_id=str(data.get("ticket_id") or req_id or "") or None,
                    metadatos_seguros={"modalidad": mod, "solicitud_id": req_id},
                )
            return NotificationDraft(
                titulo="Incidencia abierta en el servicio",
                resumen=(
                    "Se abrió una incidencia en revisión administrativa. El cierre automático y la liberación "
                    "de fondos permanecen detenidos hasta su resolución."
                ),
                enlace_ruta=_request_route(recipient_role, req_id, active_job=True),
                recurso_tipo="INCIDENCIA",
                recurso_id=req_id or None,
                metadatos_seguros={"modalidad": mod, "solicitud_id": req_id},
            )

        decision = NotificationPrivacySanitizer.sanitize_text(data.get("decision") or "", max_length=60).replace("_", " ").lower()
        decision_txt = f" ({decision})" if decision else ""
        return NotificationDraft(
            titulo="Incidencia resuelta por administración",
            resumen=f"La administración resolvió la incidencia del servicio{decision_txt}. Revisa el estado actualizado.",
            enlace_ruta=_request_route(recipient_role, req_id, active_job=True),
            recurso_tipo="INCIDENCIA",
            recurso_id=req_id or None,
            metadatos_seguros={"modalidad": mod, "solicitud_id": req_id},
        )


class AdjustmentTemplate:
    def __init__(self, action: str):
        self.action = action

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        req_id = str(data.get("solicitud_id") or "").strip()
        mod = str(data.get("modalidad") or "DIRECTA").strip().upper()
        monto_txt = _safe_money_fragment(data, key="nuevo_total", prefix=" a ")
        if self.action == "SOLICITADO":
            return NotificationDraft(
                titulo="Solicitud de ajuste de presupuesto",
                resumen=f"El técnico solicitó un ajuste del presupuesto del servicio{monto_txt}. Revísalo para aprobar o rechazar.",
                enlace_ruta=_request_route("CLIENTE", req_id, active_job=True),
                recurso_tipo="AJUSTE_PRESUPUESTO",
                recurso_id=req_id or None,
                metadatos_seguros={"modalidad": mod, "solicitud_id": req_id, "estado": "PENDIENTE"},
            )
        if self.action == "APROBADO":
            return NotificationDraft(
                titulo="Ajuste de presupuesto aprobado",
                resumen=f"El cliente aprobó el ajuste de presupuesto del servicio{monto_txt}.",
                enlace_ruta=_request_route("TECNICO", req_id, active_job=True),
                recurso_tipo="AJUSTE_PRESUPUESTO",
                recurso_id=req_id or None,
                metadatos_seguros={"modalidad": mod, "solicitud_id": req_id, "estado": "APROBADO"},
            )
        return NotificationDraft(
            titulo="Ajuste de presupuesto rechazado",
            resumen="El cliente rechazó el ajuste de presupuesto propuesto para el servicio.",
            enlace_ruta=_request_route("TECNICO", req_id, active_job=True),
            recurso_tipo="AJUSTE_PRESUPUESTO",
            recurso_id=req_id or None,
            metadatos_seguros={"modalidad": mod, "solicitud_id": req_id, "estado": "RECHAZADO"},
        )


class VerificationTemplate:
    def __init__(self, mode: str):
        self.mode = mode

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        if self.mode == "VERIFICADO":
            return NotificationDraft(
                titulo="Verificación aprobada",
                resumen="Tu verificación de identidad y documentos fue aprobada. Tu cuenta quedó habilitada.",
                enlace_ruta="/verificacion",
                recurso_tipo="VERIFICACION",
                metadatos_seguros={"estado": "VERIFICADO"},
            )
        if self.mode == "DOCUMENTACION_APROBADA":
            return NotificationDraft(
                titulo="Documentos de identidad aprobados",
                resumen="Tus documentos básicos de identidad fueron aprobados. Revisa el estado de tus requisitos restantes.",
                enlace_ruta="/verificacion",
                recurso_tipo="VERIFICACION",
                metadatos_seguros={"estado": "EN_REVISION"},
            )
        return NotificationDraft(
            titulo="Documentación o identidad rechazada",
            resumen="Uno o más requisitos de tu verificación fueron rechazados o invalidados. Ingresa para corregirlos.",
            enlace_ruta="/verificacion",
            recurso_tipo="VERIFICACION",
            metadatos_seguros={"estado": "RECHAZADO"},
        )


class CertificationTemplate:
    def __init__(self, status_name: str):
        self.status_name = status_name

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        cert_id = str(data.get("certificacion_id") or "").strip() or None
        raw_title = NotificationPrivacySanitizer.sanitize_text(data.get("titulo_certificado") or "", max_length=90)
        title_txt = f' "{raw_title}"' if raw_title else ""
        if self.status_name == "APROBADO":
            return NotificationDraft(
                titulo="Certificación de oficio aprobada",
                resumen=f"Tu certificación de oficio{title_txt} fue aprobada y ya se muestra en tu perfil.",
                enlace_ruta="/perfil",
                recurso_tipo="CERTIFICACION",
                recurso_id=cert_id,
                metadatos_seguros={"estado": "APROBADO"},
            )
        if self.status_name == "REVOCADO":
            return NotificationDraft(
                titulo="Certificación de oficio revocada",
                resumen=f"Tu certificación de oficio{title_txt} fue revocada por administración.",
                enlace_ruta="/perfil",
                recurso_tipo="CERTIFICACION",
                recurso_id=cert_id,
                metadatos_seguros={"estado": "REVOCADO"},
            )
        return NotificationDraft(
            titulo="Certificación de oficio rechazada",
            resumen=f"Tu certificación de oficio{title_txt} fue rechazada. Puedes cargar un respaldo actualizado en tu perfil.",
            enlace_ruta="/perfil",
            recurso_tipo="CERTIFICACION",
            recurso_id=cert_id,
            metadatos_seguros={"estado": "RECHAZADO"},
        )


class SettlementTemplate:
    def __init__(self, mode: str):
        self.mode = mode

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        settlement_id = str(data.get("liquidacion_id") or "").strip() or None
        if self.mode == "RECIBIDA":
            return NotificationDraft(
                titulo="Nueva liquidación pendiente",
                resumen="Se registró una nueva liquidación de comisiones pendiente de revisión administrativa.",
                enlace_ruta="/admin?tab=settlements",
                recurso_tipo="LIQUIDACION",
                recurso_id=settlement_id,
                metadatos_seguros={"estado": "PENDIENTE"},
            )
        if self.mode == "APROBADA":
            aplicado_txt = _safe_money_fragment(data, key="importe_aplicado", prefix=" por ")
            unblocked = bool(data.get("desbloqueado"))
            extra = " Tu restricción financiera fue retirada." if unblocked else ""
            return NotificationDraft(
                titulo="Liquidación de comisiones aprobada",
                resumen=f"Tu liquidación de comisiones{aplicado_txt} fue aprobada.{extra}",
                enlace_ruta="/tecnico",
                recurso_tipo="LIQUIDACION",
                recurso_id=settlement_id,
                metadatos_seguros={"estado": str(data.get("estado") or "APROBADA"), "desbloqueado": unblocked},
            )
        return NotificationDraft(
            titulo="Liquidación de comisiones rechazada",
            resumen="Tu liquidación de comisiones fue rechazada. Revisa el estado y envía un nuevo comprobante desde tu espacio.",
            enlace_ruta="/tecnico",
            recurso_tipo="LIQUIDACION",
            recurso_id=settlement_id,
            metadatos_seguros={"estado": "RECHAZADA"},
        )


class PayoutTemplate:
    def __init__(self, mode: str):
        self.mode = mode

    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        order_id = str(data.get("orden_id") or "").strip() or None
        monto_txt = _safe_money_fragment(data, key="importe")
        if self.mode == "SOLICITADO":
            return NotificationDraft(
                titulo="Nueva solicitud de desembolso",
                resumen=f"Un técnico solicitó un desembolso de ganancias{monto_txt} pendiente de revisión.",
                enlace_ruta="/admin?tab=payouts",
                recurso_tipo="DESEMBOLSO",
                recurso_id=order_id,
                metadatos_seguros={"estado": "PENDIENTE"},
            )
        if self.mode == "PAGADA":
            return NotificationDraft(
                titulo="Desembolso confirmado",
                resumen=f"Tu orden de desembolso{monto_txt} fue confirmada y pagada.",
                enlace_ruta="/tecnico",
                recurso_tipo="DESEMBOLSO",
                recurso_id=order_id,
                metadatos_seguros={"estado": "PAGADA"},
            )
        return NotificationDraft(
            titulo="Desembolso rechazado",
            resumen=f"Tu orden de desembolso{monto_txt} fue rechazada y el saldo reservado fue liberado a tu balance disponible.",
            enlace_ruta="/tecnico",
            recurso_tipo="DESEMBOLSO",
            recurso_id=order_id,
            metadatos_seguros={"estado": "RECHAZADA"},
        )


class OperationalRestrictionTemplate:
    def build(self, *, recipient_role: str, data: dict[str, Any]) -> NotificationDraft:
        motivo_codigo = str(data.get("motivo_codigo") or "").strip().upper()
        if recipient_role == "ADMIN" or motivo_codigo == "ALERTA_TERCERA_FALTA":
            return NotificationDraft(
                titulo="Alerta administrativa de incumplimientos",
                resumen="Una cuenta alcanzó el umbral de faltas no justificadas y requiere evaluación administrativa.",
                enlace_ruta="/admin?tab=incidents",
                recurso_tipo="RESTRICCION_OPERATIVA",
                metadatos_seguros={"motivo_codigo": "ALERTA_TERCERA_FALTA"},
            )
        if motivo_codigo == "DEUDA_COMISIONES":
            return NotificationDraft(
                titulo="Acción requerida: bloqueo por deuda de comisiones",
                resumen=(
                    "Tu cuenta técnica tiene una restricción operativa activa por deuda de comisiones. "
                    "Liquida tu saldo pendiente para volver a recibir contrataciones."
                ),
                enlace_ruta="/tecnico",
                recurso_tipo="RESTRICCION_OPERATIVA",
                metadatos_seguros={"motivo_codigo": "DEUDA_COMISIONES", "requiere_accion": True},
            )
        if motivo_codigo == "TELEFONO_NO_VERIFICADO":
            return NotificationDraft(
                titulo="Acción requerida: verifica tu teléfono",
                resumen=(
                    "Tu habilitación operativa fue restringida tras el cambio de teléfono. "
                    "Valida tu nuevo número para reactivar tu perfil."
                ),
                enlace_ruta="/verificacion",
                recurso_tipo="RESTRICCION_OPERATIVA",
                metadatos_seguros={"motivo_codigo": "TELEFONO_NO_VERIFICADO", "requiere_accion": True},
            )
        return NotificationDraft(
            titulo="Acción requerida: actualiza tus documentos",
            resumen=(
                "Tu perfil presenta documentos vencidos o rechazados que restringen tu habilitación operativa. "
                "Ingresa a verificación para renovarlos."
            ),
            enlace_ruta="/verificacion",
            recurso_tipo="RESTRICCION_OPERATIVA",
            metadatos_seguros={"motivo_codigo": motivo_codigo or "DOCUMENTOS_PENDIENTES", "requiere_accion": True},
        )


TEMPLATE_STRATEGIES: dict[str, NotificationTemplateStrategy] = {
    EVENT_NUEVA_SOLICITUD_DIRECTA: DirectRequestCreatedTemplate(),
    EVENT_SOLICITUD_DIRECTA_ACEPTADA: DirectRequestDecisionTemplate(accepted=True),
    EVENT_SOLICITUD_DIRECTA_RECHAZADA: DirectRequestDecisionTemplate(accepted=False),
    EVENT_NUEVA_OFERTA_ABIERTA: OpenRequestProposalTemplate(is_counter=False),
    EVENT_NUEVA_CONTRAOFERTA_ABIERTA: OpenRequestProposalTemplate(is_counter=True),
    EVENT_PROPUESTA_RECHAZADA: ProposalRejectedTemplate(),
    EVENT_PROPUESTA_ADJUDICADA: ProposalAwardedTemplate(),
    EVENT_PROPUESTA_DESCARTADA_POR_ADJUDICACION: ProposalDiscardedByAwardTemplate(),
    EVENT_SERVICIO_INICIADO: ServiceLifecycleTemplate(
        titulo="Servicio iniciado",
        resumen_cliente="El técnico inició la ejecución de tu servicio.",
        resumen_tecnico="Iniciaste la ejecución del servicio.",
        estado="EN_PROCESO",
        active_job=True,
    ),
    EVENT_FINALIZACION_REPORTADA: ServiceLifecycleTemplate(
        titulo="Finalización de servicio reportada",
        resumen_cliente=(
            "El técnico reportó la finalización del servicio. Tienes 48 horas para confirmar la "
            "recepción o abrir una incidencia."
        ),
        resumen_tecnico="Se registró el reporte de finalización del servicio.",
        estado="EN_PROCESO",
        active_job=True,
    ),
    EVENT_RECEPCION_CONFIRMADA: ServiceLifecycleTemplate(
        titulo="Recepción confirmada por el cliente",
        resumen_cliente="Confirmaste la recepción del servicio y la contratación quedó completada.",
        resumen_tecnico="El cliente confirmó la recepción del servicio. La contratación pasó a estado Completada.",
        estado="COMPLETADA",
        active_job=True,
    ),
    EVENT_CIERRE_AUTOMATICO_48H: ServiceLifecycleTemplate(
        titulo="Cierre automático de servicio (48 h)",
        resumen_cliente=(
            "Transcurrieron 48 horas desde el reporte sin incidencias activas y tu servicio fue completado "
            "automáticamente."
        ),
        resumen_tecnico=(
            "Transcurrieron 48 horas sin incidencias activas y el servicio pasó automáticamente a estado Completada."
        ),
        estado="COMPLETADA",
        active_job=True,
    ),
    EVENT_SERVICIO_CANCELADO: ServiceLifecycleTemplate(
        titulo="Servicio cancelado",
        resumen_cliente="La solicitud o contratación fue cancelada.",
        resumen_tecnico="La solicitud o contratación fue cancelada.",
        estado="CANCELADA",
        active_job=False,
    ),
    EVENT_INCIDENCIA_ABIERTA: IncidentTemplate(resolved=False),
    EVENT_INCIDENCIA_RESUELTA: IncidentTemplate(resolved=True),
    EVENT_AJUSTE_PRESUPUESTO_SOLICITADO: AdjustmentTemplate("SOLICITADO"),
    EVENT_AJUSTE_PRESUPUESTO_APROBADO: AdjustmentTemplate("APROBADO"),
    EVENT_AJUSTE_PRESUPUESTO_RECHAZADO: AdjustmentTemplate("RECHAZADO"),
    EVENT_VERIFICACION_APROBADA: VerificationTemplate("VERIFICADO"),
    EVENT_DOCUMENTACION_APROBADA: VerificationTemplate("DOCUMENTACION_APROBADA"),
    EVENT_VERIFICACION_RECHAZADA: VerificationTemplate("RECHAZADO"),
    EVENT_CERTIFICACION_APROBADA: CertificationTemplate("APROBADO"),
    EVENT_CERTIFICACION_RECHAZADA: CertificationTemplate("RECHAZADO"),
    EVENT_CERTIFICACION_REVOCADA: CertificationTemplate("REVOCADO"),
    EVENT_LIQUIDACION_APROBADA: SettlementTemplate("APROBADA"),
    EVENT_LIQUIDACION_RECHAZADA: SettlementTemplate("RECHAZADA"),
    EVENT_LIQUIDACION_RECIBIDA: SettlementTemplate("RECIBIDA"),
    EVENT_DESEMBOLSO_PAGADO: PayoutTemplate("PAGADA"),
    EVENT_DESEMBOLSO_RECHAZADO: PayoutTemplate("RECHAZADA"),
    EVENT_DESEMBOLSO_SOLICITADO: PayoutTemplate("SOLICITADO"),
    EVENT_RESTRICCION_OPERATIVA: OperationalRestrictionTemplate(),
}


def is_supported_notification_event(tipo_evento: str) -> bool:
    return (tipo_evento or "").strip().upper() in TEMPLATE_STRATEGIES


def build_notification_for_recipient(
    tipo_evento: str,
    *,
    recipient_role: str,
    safe_data: dict[str, Any],
) -> NotificationDraft:
    """Construye y sanitiza un NotificationDraft usando el registro Strategy por tipo de evento."""
    event_key = (tipo_evento or "").strip().upper()
    strategy = TEMPLATE_STRATEGIES.get(event_key)
    if strategy is None:
        raise ValueError(f"Tipo de evento de notificación no soportado: {event_key}")

    sanitized_input = NotificationPrivacySanitizer.sanitize_metadata(safe_data)
    draft = strategy.build(recipient_role=recipient_role.strip().upper(), data=sanitized_input)

    titulo = NotificationPrivacySanitizer.sanitize_text(draft.titulo, max_length=160) or "Actualización de TekniGO"
    resumen = NotificationPrivacySanitizer.sanitize_text(draft.resumen, max_length=500) or "Tienes una actualización en tu cuenta."
    enlace_ruta = NotificationPrivacySanitizer.validate_internal_route(draft.enlace_ruta)
    recurso_tipo = NotificationPrivacySanitizer.sanitize_text(draft.recurso_tipo, max_length=50) if draft.recurso_tipo else None
    recurso_id = NotificationPrivacySanitizer.sanitize_text(draft.recurso_id, max_length=64) if draft.recurso_id else None
    metadatos_seguros = NotificationPrivacySanitizer.sanitize_metadata(draft.metadatos_seguros)

    return NotificationDraft(
        titulo=titulo,
        resumen=resumen,
        enlace_ruta=enlace_ruta,
        recurso_tipo=recurso_tipo or None,
        recurso_id=recurso_id or None,
        metadatos_seguros=metadatos_seguros,
    )
