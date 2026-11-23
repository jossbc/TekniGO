"""HU-36 ST-02: extracción del borrador de reapertura por modalidad (OCP vía interfaz)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.models import DirectRequest, OpenRequest


@dataclass(frozen=True)
class ReopenDraft:
    especialidad_id: str
    descripcion: str
    municipio: str
    sector: str
    direccion_exacta: str
    metodo_pago: str
    presupuesto: Decimal


class ReopenSource(ABC):
    @abstractmethod
    def supports(self, record) -> bool: ...

    @abstractmethod
    def to_draft(self, record) -> ReopenDraft: ...


class DirectRequestReopenSource(ReopenSource):
    def supports(self, record) -> bool:
        return isinstance(record, DirectRequest)

    def to_draft(self, record: DirectRequest) -> ReopenDraft:
        return ReopenDraft(
            especialidad_id=str(record.servicio.especialidad_id),
            descripcion=record.descripcion,
            municipio=record.municipio,
            sector=record.sector,
            direccion_exacta=record.direccion_exacta,
            metodo_pago=record.metodo_pago,
            presupuesto=Decimal(str(record.monto_base)),
        )


class OpenRequestReopenSource(ReopenSource):
    def supports(self, record) -> bool:
        return isinstance(record, OpenRequest)

    def to_draft(self, record: OpenRequest) -> ReopenDraft:
        return ReopenDraft(
            especialidad_id=str(record.especialidad_id),
            descripcion=record.descripcion,
            municipio=record.municipio,
            sector=record.sector,
            direccion_exacta=record.direccion_exacta,
            metodo_pago=record.metodo_pago,
            presupuesto=Decimal(str(record.monto_base or record.presupuesto)),
        )


def default_reopen_sources() -> list[ReopenSource]:
    return [DirectRequestReopenSource(), OpenRequestReopenSource()]