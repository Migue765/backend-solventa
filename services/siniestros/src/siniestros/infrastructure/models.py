from datetime import datetime

from siniestros.models import Base
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


class CambioEstadoModel(Base):
    __tablename__ = "historial_estados_siniestro"

    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"), primary_key=True)
    secuencia: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    estado: Mapped[str] = mapped_column(String(30))
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    visible_cliente: Mapped[bool]
