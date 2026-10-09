from decimal import Decimal

from cotizacion.config import Config


def test_valores_por_defecto(monkeypatch):
    for variable in ("DATABASE_URL", "COTIZACION_TASA_BASE", "COTIZACION_VIGENCIA_DIAS"):
        monkeypatch.delenv(variable, raising=False)

    assert Config.desde_entorno() == Config()


def test_lee_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/cotizacion")
    monkeypatch.setenv("COTIZACION_TASA_BASE", "0.02")
    monkeypatch.setenv("COTIZACION_VIGENCIA_DIAS", "10")

    config = Config.desde_entorno()

    assert config.database_url == "postgresql+psycopg://u:p@db/cotizacion"
    assert config.tasa_base == Decimal("0.02")
    assert config.vigencia_dias == 10
