import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Config:
    database_url: str = "sqlite:///cotizacion.db"
    vigencia_dias: int = 30
    db_pool_size: int = 10
    db_max_overflow: int = 10
    reglas_cache_segundos: float = 30.0
    perfil_score_simulado: Decimal = Decimal("0.50")

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            vigencia_dias=int(os.getenv("COTIZACION_VIGENCIA_DIAS", str(cls.vigencia_dias))),
            db_pool_size=int(os.getenv("DB_POOL_SIZE", str(cls.db_pool_size))),
            db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", str(cls.db_max_overflow))),
            reglas_cache_segundos=float(
                os.getenv("COTIZACION_REGLAS_CACHE_SEGUNDOS", str(cls.reglas_cache_segundos))
            ),
            perfil_score_simulado=Decimal(
                os.getenv("COTIZACION_PERFIL_SCORE_SIMULADO", str(cls.perfil_score_simulado))
            ),
        )
