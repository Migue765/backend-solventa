from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def crear_sesiones(
    database_url: str,
    pool_size: int = 10,
    max_overflow: int = 10,
    *,
    limitar_esperas: bool = False,
) -> sessionmaker[Session]:
    opciones: dict[str, object] = {}
    if not database_url.startswith("sqlite"):
        opciones = {"pool_size": pool_size, "max_overflow": max_overflow}
    url = make_url(database_url)
    dialecto = url.get_backend_name()
    if limitar_esperas:
        if dialecto == "sqlite":
            if not url.database or ":memory:" in url.database or url.query.get("mode") == "memory":
                raise ValueError("La conciliacion requiere una base persistente.")
            opciones["connect_args"] = {"timeout": 2}
            opciones.update(pool_size=pool_size, max_overflow=max_overflow)
        elif dialecto == "postgresql":
            options = url.query.get("options", "")
            if not isinstance(options, str):
                raise ValueError("Las opciones de PostgreSQL deben ser un unico valor.")
            opciones["connect_args"] = {
                "connect_timeout": 3,
                "options": options + " -c statement_timeout=5000 -c lock_timeout=2000",
                "keepalives": 1,
                "keepalives_idle": 5,
                "keepalives_interval": 1,
                "keepalives_count": 3,
            }
        else:
            raise ValueError("La conciliacion requiere SQLite o PostgreSQL.")
        opciones["pool_timeout"] = 5
    engine = create_engine(database_url, pool_pre_ping=True, **opciones)
    return sessionmaker(bind=engine, expire_on_commit=False)
