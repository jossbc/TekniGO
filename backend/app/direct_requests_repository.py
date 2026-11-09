from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import Service
from app.models import DirectRequest

class DirectRequestRepository:
    @staticmethod
    def get_service_by_id(db: Session, servicio_id: str) -> Service | None:
        return db.scalar(select(Service).where(Service.id == servicio_id))

    @staticmethod
    def create_request(db: Session, request_data: dict) -> DirectRequest:
        db_request = DirectRequest(**request_data)
        db.add(db_request)
        db.commit()
        db.refresh(db_request)
        return db_request