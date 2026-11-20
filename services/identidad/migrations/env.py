import os

from alembic import context
from identidad.infrastructure.persistence.models import Base
from sqlalchemy import create_engine, inspect, pool
from sqlalchemy.schema import CreateSchema

config = context.config
database_url = (
    config.get_main_option("sqlalchemy.url")
    or os.getenv("DATABASE_URL")
    or "sqlite:///identidad.db"
)
schema = os.getenv("DB_SCHEMA") or None
is_postgres = database_url.startswith("postgresql")
runtime_role = os.getenv("DB_RUNTIME_ROLE")
if runtime_role is not None and not is_postgres:
    raise RuntimeError("DB_RUNTIME_ROLE requiere PostgreSQL.")
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
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        if is_postgres and schema:
            if not inspect(connection).has_schema(schema):
                connection.execute(CreateSchema(schema, if_not_exists=True))
            quoted_schema = connection.dialect.identifier_preparer.quote(schema)
            connection.exec_driver_sql(f"SET search_path TO {quoted_schema}")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=schema if is_postgres else None,
        )
        with context.begin_transaction():
            context.run_migrations()
            if runtime_role is not None:
                quote = connection.dialect.identifier_preparer.quote
                history = f"{quote(schema)}.{VERSION_TABLE}" if schema else VERSION_TABLE
                connection.exec_driver_sql(
                    f"REVOKE ALL ON TABLE {history} FROM {quote(runtime_role)}"
                )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
