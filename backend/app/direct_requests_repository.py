import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DirectRequest, Service


class DirectRequestRepository:
    @staticmethod
    def get_service_by_id(db: Session, servicio_id: str) -> Service | None:
        try:
            parsed_id = uuid.UUID(str(servicio_id).strip())
        except (ValueError, AttributeError):
            return None
        return db.scalar(select(Service).where(Service.id == parsed_id))

    @staticmethod
    def create_request(db: Session, request_data: dict) -> DirectRequest:
        db_request = DirectRequest(**request_data)
        db.add(db_request)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise
        db.refresh(db_request)
        return db_request