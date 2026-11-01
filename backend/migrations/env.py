from alembic import context
from app.database import Base, get_engine, database_schema
from app import models

schema = database_schema()
version_schema = None if schema == 'public' else schema

if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True,
                      version_table_schema=version_schema)
    with context.begin_transaction():
        if schema != 'public':
            context.execute(f'SET LOCAL search_path TO "{schema}", pg_catalog')
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, version_table_schema=version_schema)
        with context.begin_transaction():
            context.run_migrations()
