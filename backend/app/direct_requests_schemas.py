from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime

class EstimateRequest(BaseModel):
    servicio_id: str
    cantidad: Decimal = Field(gt=0, description="La cantidad debe ser mayor a cero")

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
    metodo_pago: str # 'efectivo' o 'tarjeta'