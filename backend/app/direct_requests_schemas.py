from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator


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
    metodo_pago: str  # Ej. 'Efectivo', 'Transferencia', 'efectivo', 'tarjeta'
    total_estimado: Decimal | None = None

    @field_validator(
        "tecnico_id",
        "servicio_id",
        "municipio",
        "sector",
        "direccion_exacta",
        "descripcion",
        "metodo_pago",
    )
    @classmethod
    def validate_non_empty_fields(cls, value: str, info) -> str:
        return _require_non_empty(value, info.field_name)
