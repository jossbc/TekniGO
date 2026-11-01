"""Create/migrate only teknigo_dev, then select it for the local backend."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import set_key
from sqlalchemy import text

from app.database import ENV_PATH, get_engine

SCHEMA = 'teknigo_dev'


def main():
    stage = 'conexión'
    try:
        # Explicit connection for schema creation only; no business table writes.
        with get_engine(schema='public').begin() as connection:
            connection.exec_driver_sql('SET LOCAL lock_timeout = 5000')
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
            connection.exec_driver_sql(f'REVOKE ALL ON SCHEMA "{SCHEMA}" FROM PUBLIC, anon, authenticated')
        print('OK: esquema privado teknigo_dev preparado.', flush=True)
        os.environ['DATABASE_SCHEMA'] = SCHEMA
        stage = 'migración del esquema de desarrollo'
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'migrations'))
        command.upgrade(config, 'head')
        with get_engine().connect() as connection:
            if connection.scalar(text('SELECT current_schema()')) != SCHEMA:
                raise RuntimeError('Schema mismatch')
            revision = connection.scalar(text(f'SELECT version_num FROM "{SCHEMA}".alembic_version'))
            for table in ('usuarios', 'documentos_identidad', 'solicitudes_verificacion_identidad',
                          'resultados_verificacion_identidad'):
                if connection.scalar(text('SELECT to_regclass(:table)'), {'table': f'{SCHEMA}.{table}'}) is None:
                    raise RuntimeError('Missing dev table')
        print('OK: migraciones de desarrollo aplicadas:', revision, flush=True)
        stage = 'configuración local'
        set_key(str(ENV_PATH), 'DATABASE_SCHEMA', SCHEMA)
        set_key(str(ENV_PATH), 'IDENTITY_PROCESSOR', 'python')
        print('OK: backend/.env apunta a teknigo_dev y activa el procesamiento local.')
        print('Reinicia FastAPI. Las cuentas/documentos de public no se copiaron ni modificaron.')
        print('Usa tu acceso Firebase existente y completa el perfil de desarrollo si se solicita.')
        return 0
    except Exception:
        # Never print connection errors with credentials or provider details.
        print(f'ERROR: no se completó la etapa de {stage}. No se aplicaron migraciones a public.')
        print('Si falla la conexión, ejecuta este mismo comando en tu terminal local.')
        return 1
    finally:
        get_engine.cache_clear()


if __name__ == '__main__':
    raise SystemExit(main())
