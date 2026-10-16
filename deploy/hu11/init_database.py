import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from identidad.persistence import Base as IdentityBase
from perfilamiento.persistence import Base as ProfileBase
from sqlalchemy import create_engine, text


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    schemas = {
        "hu11_identidad": IdentityBase.metadata,
        "hu11_perfilamiento": ProfileBase.metadata,
    }
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for schema, metadata in schemas.items():
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            metadata.create_all(connection)
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS hu11_cotizacion"))
    config = Config(str(Path("/app/services/cotizacion/alembic.ini")))
    config.set_main_option(
        "sqlalchemy.url", os.environ["HU11_COTIZACION_DATABASE_URL"].replace("%", "%%")
    )
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
