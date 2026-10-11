from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def crear_sesiones(
    database_url: str, pool_size: int = 10, max_overflow: int = 10
) -> sessionmaker[Session]:
    opciones: dict[str, int] = {}
    if not database_url.startswith("sqlite"):
        opciones = {"pool_size": pool_size, "max_overflow": max_overflow}
    engine = create_engine(database_url, pool_pre_ping=True, **opciones)
    return sessionmaker(bind=engine, expire_on_commit=False)
