import os

from alembic import context
from identidad.infrastructure.persistence.models import Base
from sqlalchemy import create_engine, pool, text

config = context.config
database_url = (
    config.get_main_option("sqlalchemy.url")
    or os.getenv("DATABASE_URL")
    or "sqlite:///identidad.db"
)
schema = os.getenv("DB_SCHEMA") or None
is_postgres = database_url.startswith("postgresql")
target_metadata = Base.metadata
# La base se comparte con otros servicios: la versión de este esquema va en su propia tabla.
VERSION_TABLE = "alembic_version_identidad"


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=VERSION_TABLE,
        version_table_schema=schema if is_postgres else None,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_args = {}
    if is_postgres and schema:
        connect_args = {"options": f"-csearch_path={schema}"}
    engine = create_engine(database_url, poolclass=pool.NullPool, connect_args=connect_args)
    with engine.connect() as connection:
        if is_postgres and schema:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=schema if is_postgres else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
