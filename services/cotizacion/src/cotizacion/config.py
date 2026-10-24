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
    polizas_url: str = ""
    socios_cuota_por_minuto: int = 60
    socios_max_concurrentes: int = 2

    def __post_init__(self) -> None:
        if self.socios_cuota_por_minuto < 1 or self.socios_max_concurrentes < 1:
            raise ValueError("Las cuotas y concurrencia de socios deben ser positivas.")
        if self.polizas_url and not self.internal_api_token.strip():
            raise ValueError("Polizas requiere INTERNAL_API_TOKEN.")
        if self.perfilamiento_habilitado and (
            not self.internal_api_token.strip() or not self.perfilamiento_url.strip()
        ):
            raise ValueError(
                "Perfilamiento requiere INTERNAL_API_TOKEN y PERFILAMIENTO_SERVICE_URL."
            )

    @classmethod
    def desde_entorno(cls) -> "Config":
        habilitado = os.getenv("COTIZACION_PERFILAMIENTO_HABILITADO", "false").strip().lower()
        if habilitado not in {"true", "false"}:
            raise ValueError("COTIZACION_PERFILAMIENTO_HABILITADO debe ser true o false.")
        return cls(
            socios_cuota_por_minuto=int(os.getenv("SOCIOS_CUOTA_POR_MINUTO", "60")),
            socios_max_concurrentes=int(os.getenv("SOCIOS_MAX_CONCURRENTES", "2")),
            polizas_url=os.getenv("POLIZAS_SERVICE_URL", "").strip().rstrip("/"),
            perfilamiento_habilitado=habilitado == "true",
            perfilamiento_url=os.getenv("PERFILAMIENTO_SERVICE_URL", cls.perfilamiento_url),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", "").strip(),
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
