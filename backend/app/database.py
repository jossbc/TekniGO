import os
import re
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


class Base(DeclarativeBase):
    pass


def database_schema(value: str | None = None) -> str:
    schema = value if value is not None else os.getenv('DATABASE_SCHEMA', 'public')
    if schema != 'public' and not re.fullmatch(r'teknigo_dev(?:_[a-z0-9_]+)?', schema):
        raise RuntimeError('DATABASE_SCHEMA debe ser public o un esquema teknigo_dev válido')
    if len(schema) > 63:
        raise RuntimeError('DATABASE_SCHEMA es demasiado largo')
    return schema


def apply_database_schema(connection, schema: str) -> None:
    schema = database_schema(schema)
    if schema == 'public':
        return
    # Applied on EVERY transaction: safe with recycled/transaction-pooled connections.
    # public is deliberately excluded; a missing dev table cannot resolve to production.
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", pg_catalog')
    if connection.exec_driver_sql('SELECT current_schema()').scalar_one() != schema:
        raise RuntimeError('El esquema de desarrollo no existe. Ejecuta python -m app.setup_development_database')


@lru_cache
def get_engine(schema: str | None = None):
    selected_schema = database_schema(schema)
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
    engine = create_engine(parsed, pool_pre_ping=True, pool_size=5, max_overflow=0,
                           pool_timeout=10, connect_args={"connect_timeout": 10})
    if selected_schema != 'public':
        event.listen(engine, 'begin', lambda connection: apply_database_schema(connection, selected_schema))
    return engine


def get_db():
    with Session(get_engine()) as session:
        yield session
