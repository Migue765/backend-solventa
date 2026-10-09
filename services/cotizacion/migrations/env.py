from alembic import context
from sqlalchemy import create_engine, pool

from cotizacion.config import Config
from cotizacion.infrastructure.persistence.models import Base

config = context.config
database_url = config.get_main_option("sqlalchemy.url") or Config.desde_entorno().database_url
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
