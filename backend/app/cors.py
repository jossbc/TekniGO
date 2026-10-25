import os

from starlette.middleware.cors import CORSMiddleware

LOCAL_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def allowed_origins():
    return list(dict.fromkeys(
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ORIGINS", LOCAL_ORIGINS).split(",")
        if origin.strip()
    ))


def configure_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
