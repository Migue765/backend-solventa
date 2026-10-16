from decimal import Decimal

import pytest
from cotizacion.config import Config

VARIABLES = (
    "DATABASE_URL",
    "COTIZACION_VIGENCIA_DIAS",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "COTIZACION_REGLAS_CACHE_SEGUNDOS",
    "COTIZACION_PERFIL_SCORE_SIMULADO",
    "COTIZACION_PERFILAMIENTO_HABILITADO",
    "PERFILAMIENTO_SERVICE_URL",
    "INTERNAL_API_TOKEN",
)


def test_valores_por_defecto(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    assert Config.desde_entorno() == Config()


def test_lee_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/cotizacion")
    monkeypatch.setenv("COTIZACION_VIGENCIA_DIAS", "10")
    monkeypatch.setenv("DB_POOL_SIZE", "25")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "5")
    monkeypatch.setenv("COTIZACION_REGLAS_CACHE_SEGUNDOS", "2.5")
    monkeypatch.setenv("COTIZACION_PERFIL_SCORE_SIMULADO", "0.8")

    config = Config.desde_entorno()

    assert config.database_url == "postgresql+psycopg://u:p@db/cotizacion"
    assert config.vigencia_dias == 10
    assert config.db_pool_size == 25
    assert config.db_max_overflow == 5
    assert config.reglas_cache_segundos == 2.5
    assert config.perfil_score_simulado == Decimal("0.8")


def test_hu11_exige_token_y_activacion_explicita(monkeypatch):
    monkeypatch.setenv("COTIZACION_PERFILAMIENTO_HABILITADO", "true")
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="INTERNAL_API_TOKEN"):
        Config.desde_entorno()
    monkeypatch.setenv("INTERNAL_API_TOKEN", "token-solo-pruebas")
    assert Config.desde_entorno().perfilamiento_habilitado is True
    monkeypatch.setenv("COTIZACION_PERFILAMIENTO_HABILITADO", "talvez")
    with pytest.raises(ValueError, match="true o false"):
        Config.desde_entorno()
