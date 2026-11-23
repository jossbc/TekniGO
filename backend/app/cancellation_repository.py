"""HU-36: persistencia de cancelaciones y justificaciones detrás de interfaces."""
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CancellationJustification, RequestCancellation


class CancellationRepository(Protocol):
    def get(self, db: Session, modalidad: str, solicitud_id: str) -> RequestCancellation | None: ...
    def get_by_id(self, db: Session, cancellation_id: int) -> RequestCancellation | None: ...
    def add(self, db: Session, cancellation: RequestCancellation) -> None: ...


class SqlAlchemyCancellationRepository:
    def get(self, db, modalidad, solicitud_id):
        return db.scalar(select(RequestCancellation).where(
            RequestCancellation.modalidad == modalidad,
            RequestCancellation.solicitud_id == solicitud_id,
        ))

    def get_by_id(self, db, cancellation_id):
        return db.get(RequestCancellation, cancellation_id)

    def add(self, db, cancellation):
        db.add(cancellation)


class JustificationRepository(Protocol):
    def get(self, db: Session, justification_id: str) -> CancellationJustification | None: ...
    def get_by_cancellation(self, db: Session, cancellation_id: int) -> CancellationJustification | None: ...
    def add(self, db: Session, item: CancellationJustification) -> None: ...


class SqlAlchemyJustificationRepository:
    def get(self, db, justification_id):
        return db.get(CancellationJustification, justification_id)

    def get_by_cancellation(self, db, cancellation_id):
        return db.scalar(select(CancellationJustification).where(
            CancellationJustification.cancelacion_id == cancellation_id,
        ))

    def add(self, db, item):
        db.add(item)