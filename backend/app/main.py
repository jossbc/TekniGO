from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.firebase import initialize_firebase
from app.cors import configure_cors
from app.users import router
from app.specialties import router as specialties_router
from app.technicians import router as technicians_router
from app.services import router as services_router
from app.identity_documents import router as identity_documents_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_firebase()
    yield


app = FastAPI(
    title="TekniGO API",
    version="0.1.0",
    lifespan=lifespan,
)
configure_cors(app)
app.include_router(router)
app.include_router(specialties_router)
app.include_router(technicians_router)
app.include_router(services_router)
app.include_router(identity_documents_router)


@app.get("/health")
def health():
    return {"status": "ok"}
