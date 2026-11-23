import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.adjustment_router import router as adjustment_router
from app.database import get_engine
from app.firebase import initialize_firebase
from app.database_errors import database_error_response
from app.cancellation_justification_router import router as cancellation_justification_router
from app.cancellation_router import router as cancellation_router
from app.cors import configure_cors
from app.users import router
from app.specialties import router as specialties_router
from app.technicians import router as technicians_router
from app.services import router as services_router
from app.identity_documents import router as identity_documents_router
from app.email_verification import router as email_verification_router
from app.permissions import router as permissions_router
from app.phone_verification import router as phone_verification_router
from app.admin_verification import router as admin_verification_router
from app.direct_requests_router import router as direct_requests_router
from app.technician_direct_requests import router as technician_direct_requests_router
from app.client_requests import router as client_requests_router
from app.open_requests import router as open_requests_router
from app.certifications import router as certifications_router
from app.admin_catalog import router as admin_catalog_router
from app.service_reception import (
    router as service_reception_router,
    run_scheduled_auto_completion,
)
from app.service_completion import router as service_completion_router
from app.incident_management import router as incident_management_router


logger = logging.getLogger(__name__)
AUTO_COMPLETION_INTERVAL_SECONDS = 60


def _run_auto_completion_once() -> None:
    with Session(get_engine()) as session:
        result = run_scheduled_auto_completion(session)
        total = result.get("total_completadas_automaticamente", 0)
        if total:
            logger.info("Cierre automático 48h completó %s servicio(s).", total)


async def _auto_completion_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(_run_auto_completion_once)
        except Exception as exc:
            logger.warning("Error en proceso automático de cierre 48h: %s", exc)
        await asyncio.sleep(AUTO_COMPLETION_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_firebase()
    auto_complete_task = asyncio.create_task(_auto_completion_worker())
    try:
        yield
    finally:
        auto_complete_task.cancel()
        with suppress(asyncio.CancelledError):
            await auto_complete_task

app = FastAPI(
    title="TekniGO API",
    version="0.1.0",
    lifespan=lifespan,
)
configure_cors(app)
app.add_exception_handler(SQLAlchemyError, database_error_response)
app.include_router(router)
app.include_router(specialties_router)
app.include_router(technicians_router)
app.include_router(services_router)
app.include_router(identity_documents_router)
app.include_router(email_verification_router)
app.include_router(permissions_router)
app.include_router(phone_verification_router)
app.include_router(admin_verification_router)
app.include_router(direct_requests_router)
app.include_router(technician_direct_requests_router)
app.include_router(client_requests_router)
app.include_router(open_requests_router)
app.include_router(certifications_router)
app.include_router(admin_catalog_router)
app.include_router(service_reception_router)
app.include_router(service_completion_router)
app.include_router(incident_management_router)
app.include_router(cancellation_router)
app.include_router(adjustment_router)
app.include_router(cancellation_justification_router)

@app.get("/health")
def health():
    return {"status": "ok"}
