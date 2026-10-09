from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def crear_sesiones(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)
