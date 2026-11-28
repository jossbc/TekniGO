import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.account_suspension_router import router as account_suspension_router
from app.account_suspension_service import run_scheduled_suspension_expiration
from app.adjustment_router import router as adjustment_router
from app.database import get_engine
from app.fault_recidivism_router import router as fault_recidivism_router
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
from app.reviews import router as reviews_router
from app.admin_catalog import router as admin_catalog_router
from app.admin_reports import router as admin_reports_router
from app.service_reception import (
    router as service_reception_router,
    run_scheduled_auto_completion,
)
from app.service_completion import router as service_completion_router
from app.incident_management import router as incident_management_router
from app.settlements_router import router as settlements_router
from app.payouts_router import router as payouts_router
from app.digital_payments_router import router as digital_payments_router
from app.wallet import router as wallet_router
from app.chat_router import router as chat_router
from app.notifications_router import router as notifications_router
from app.notifications_consumer import process_pending_notification_events


logger = logging.getLogger(__name__)
AUTO_COMPLETION_INTERVAL_SECONDS = 60


def _run_auto_completion_once() -> None:
    with Session(get_engine()) as session:
        try:
            exp_res = run_scheduled_suspension_expiration(session)
            if exp_res.get("total_expiradas"):
                logger.info("Vencimiento automático expiró %s suspensión(es) temporal(es).", exp_res["total_expiradas"])
        except Exception as exc:
            session.rollback()
            logger.warning("Error al expirar suspensiones temporales vencidas: %s", exc)
        result = run_scheduled_auto_completion(session)
        total = result.get("total_completadas_automaticamente", 0)
        if total:
            logger.info("Cierre automático 48h completó %s servicio(s).", total)
        pending_res = process_pending_notification_events(session)
        if pending_res.get("procesados"):
            logger.info("Consumidor de notificaciones reprocesó %s evento(s).", pending_res["procesados"])


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
app.include_router(reviews_router)
app.include_router(admin_catalog_router)
app.include_router(admin_reports_router)
app.include_router(service_reception_router)
app.include_router(service_completion_router)
app.include_router(incident_management_router)
app.include_router(settlements_router)
app.include_router(payouts_router)
app.include_router(digital_payments_router)
app.include_router(wallet_router)
app.include_router(chat_router)
app.include_router(notifications_router)
app.include_router(cancellation_router)
app.include_router(adjustment_router)
app.include_router(cancellation_justification_router)
app.include_router(fault_recidivism_router)
app.include_router(account_suspension_router)

@app.get("/health")
def health():
    return {"status": "ok"}
