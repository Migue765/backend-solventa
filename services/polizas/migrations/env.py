import os

from alembic import context
from polizas.infrastructure.models import Base
from sqlalchemy import create_engine, pool

config = context.config
database_url = config.get_main_option("sqlalchemy.url") or os.environ["POLIZAS_DATABASE_URL"]

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
    engine.dispose()
