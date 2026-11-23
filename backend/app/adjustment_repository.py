"""HU-36 ST-03: persistencia del ajuste detrás de una interfaz."""
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ServiceAdjustment


class AdjustmentRepository(Protocol):
    def get(self, db: Session, modalidad: str, solicitud_id: str) -> ServiceAdjustment | None: ...
    def add(self, db: Session, adjustment: ServiceAdjustment) -> None: ...


class SqlAlchemyAdjustmentRepository:
    """También satisface RejectedAdjustmentLookup (ST-01) de forma estructural."""

    def get(self, db, modalidad, solicitud_id):
        return db.scalar(select(ServiceAdjustment).where(
            ServiceAdjustment.modalidad == modalidad,
            ServiceAdjustment.solicitud_id == solicitud_id,
        ))

    def add(self, db, adjustment):
        db.add(adjustment)

    def is_rejected(self, db, modalidad, solicitud_id) -> bool:
        item = self.get(db, modalidad, solicitud_id)
        return item is not None and item.estado == "RECHAZADO"