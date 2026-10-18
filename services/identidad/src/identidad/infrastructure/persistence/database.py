from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_sessions(
    database_url: str,
    schema: str | None = None,
    pool_size: int = 5,
    max_overflow: int = 5,
) -> sessionmaker[Session]:
    options: dict[str, Any] = {}
    if not database_url.startswith("sqlite"):
        options = {"pool_size": pool_size, "max_overflow": max_overflow}
        if schema:
            options["connect_args"] = {"options": f"-csearch_path={schema}"}
    engine = create_engine(database_url, pool_pre_ping=True, **options)
    return sessionmaker(bind=engine, expire_on_commit=False)
