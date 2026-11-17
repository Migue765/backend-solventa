import os

from alembic import context
from cotizacion.config import Config
from cotizacion.infrastructure.persistence.models import Base
from sqlalchemy import create_engine, pool

config = context.config
database_url = config.get_main_option("sqlalchemy.url") or Config.desde_entorno().database_url
runtime_role = os.getenv("DB_RUNTIME_ROLE")
if runtime_role is not None and not database_url.startswith("postgresql"):
    raise RuntimeError("DB_RUNTIME_ROLE requiere PostgreSQL.")
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
            if runtime_role is not None:
                role = connection.dialect.identifier_preparer.quote(runtime_role)
                connection.exec_driver_sql(f"REVOKE ALL ON TABLE alembic_version FROM {role}")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
