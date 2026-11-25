from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator

VALID_PAYMENT_METHODS = {"efectivo", "tarjeta"}


def _require_non_empty(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"El campo {label} es obligatorio.")
    return cleaned


class EstimateRequest(BaseModel):
    servicio_id: str
    cantidad: Decimal = Field(gt=0, description="La cantidad debe ser mayor a cero")

    @field_validator("servicio_id")
    @classmethod
    def validate_service_id(cls, value: str) -> str:
        return _require_non_empty(value, "servicio_id")


class EstimateResponse(BaseModel):
    tarifa_base: Decimal
    cantidad: Decimal
    unidad_cobro: str
    total: Decimal


class CreateDirectRequest(BaseModel):
    tecnico_id: str
    servicio_id: str
    municipio: str
    sector: str
    direccion_exacta: str
    fecha_hora: datetime
    descripcion: str
    cantidad: Decimal = Field(gt=0)
    metodo_pago: str  # Únicamente 'efectivo' o 'tarjeta' (CA-01, CA-02)
    referencia_pasarela: str | None = None
    total_estimado: Decimal | None = None

    @field_validator(
        "tecnico_id",
        "servicio_id",
        "municipio",
        "sector",
        "direccion_exacta",
        "descripcion",
    )
    @classmethod
    def validate_non_empty_fields(cls, value: str, info) -> str:
        return _require_non_empty(value, info.field_name)

    @field_validator("metodo_pago")
    @classmethod
    def validate_metodo_pago(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if not cleaned:
            raise ValueError("El método de pago es obligatorio y no puede estar vacío.")
        if cleaned not in VALID_PAYMENT_METHODS:
            raise ValueError("Método de pago no permitido. Solo se acepta 'efectivo' o 'tarjeta'.")
        return cleaned


class UpdatePaymentMethodRequest(BaseModel):
    metodo_pago: str
    referencia_pasarela: str | None = None

    @field_validator("metodo_pago")
    @classmethod
    def validate_metodo_pago(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if not cleaned:
            raise ValueError("El método de pago es obligatorio.")
        if cleaned not in VALID_PAYMENT_METHODS:
            raise ValueError("Método de pago no permitido. Solo se acepta 'efectivo' o 'tarjeta'.")
        return cleaned


class RegisterAdjustmentRequest(BaseModel):
    monto_ajuste: Decimal = Field(gt=0, description="Monto adicional del ajuste aprobado")
    metodo_pago_ajuste: str
    referencia_pasarela_ajuste: str | None = None

    @field_validator("metodo_pago_ajuste")
    @classmethod
    def validate_metodo_pago_ajuste(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned not in VALID_PAYMENT_METHODS:
            raise ValueError("Método de pago del ajuste inválido. Solo se acepta 'efectivo' o 'tarjeta'.")
        return cleaned