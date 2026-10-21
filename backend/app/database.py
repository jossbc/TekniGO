import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Configura DATABASE_URL en backend/.env")
    try:
        parsed = make_url(url)
    except Exception:
        raise RuntimeError("DATABASE_URL no es una URI válida de PostgreSQL") from None
    if parsed.drivername in ("postgres", "postgresql"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    if parsed.drivername != "postgresql+psycopg":
        raise RuntimeError("DATABASE_URL debe usar PostgreSQL con Psycopg")
    if not parsed.host or not parsed.username or not parsed.password:
        raise RuntimeError("Completa host, usuario y contraseña en DATABASE_URL")
    if any(marker in url for marker in ("[YOUR-PASSWORD]", "[YOUR_PASSWORD]", "USER:PASSWORD", "HOST:", "REPLACE_ME")):
        raise RuntimeError("Reemplaza los valores de ejemplo en DATABASE_URL")
    # Require encryption unless a stronger SSL mode is explicitly provided.
    if parsed.query.get("sslmode") not in ("require", "verify-ca", "verify-full"):
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return create_engine(parsed, pool_pre_ping=True, pool_size=5, max_overflow=0,
                         pool_timeout=10, connect_args={"connect_timeout": 10})


def get_db():
    with Session(get_engine()) as session:
        yield session
