from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.firebase import initialize_firebase
from fastapi import Depends
from app.security import get_current_user

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_firebase()
    yield


app = FastAPI(
    title="TekniGO API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/users/me")
def get_my_profile(user: dict = Depends(get_current_user)):
    return {
        "uid": user["uid"],
        "email": user.get("email"),
    }