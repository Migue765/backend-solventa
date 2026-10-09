import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Config:
    database_url: str = "sqlite:///cotizacion.db"
    tasa_base: Decimal = Decimal("0.015")
    vigencia_dias: int = 30

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            tasa_base=Decimal(os.getenv("COTIZACION_TASA_BASE", str(cls.tasa_base))),
            vigencia_dias=int(os.getenv("COTIZACION_VIGENCIA_DIAS", str(cls.vigencia_dias))),
        )
