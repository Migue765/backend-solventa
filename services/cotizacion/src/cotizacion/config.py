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
    perfilamiento_url: str = "http://localhost:5003"
    internal_api_token: str = ""
    perfilamiento_habilitado: bool = False

    def __post_init__(self) -> None:
        if self.perfilamiento_habilitado and not self.internal_api_token.strip():
            raise ValueError("HU11 requiere INTERNAL_API_TOKEN.")

    @classmethod
    def desde_entorno(cls) -> "Config":
        habilitado = os.getenv("COTIZACION_PERFILAMIENTO_HABILITADO", "false").strip().lower()
        if habilitado not in {"true", "false"}:
            raise ValueError("COTIZACION_PERFILAMIENTO_HABILITADO debe ser true o false.")
        return cls(
            perfilamiento_habilitado=habilitado == "true",
            perfilamiento_url=os.getenv("PERFILAMIENTO_SERVICE_URL", cls.perfilamiento_url),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", ""),
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
