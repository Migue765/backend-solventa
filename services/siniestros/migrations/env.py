import os

from alembic import context
from siniestros.infrastructure import fraude as _fraude  # noqa: F401
from siniestros.infrastructure.models import Base
from sqlalchemy import create_engine, pool

config = context.config
database_url = config.get_main_option("sqlalchemy.url") or os.environ["SINIESTROS_DATABASE_URL"]
runtime_role = os.getenv("DB_RUNTIME_ROLE")
if runtime_role is not None and not database_url.startswith("postgresql"):
    raise RuntimeError("DB_RUNTIME_ROLE requiere PostgreSQL.")

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
            if runtime_role is not None:
                role = connection.dialect.identifier_preparer.quote(runtime_role)
                connection.exec_driver_sql(f"REVOKE ALL ON TABLE alembic_version FROM {role}")
    engine.dispose()
