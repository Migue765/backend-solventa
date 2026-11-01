from decimal import Decimal

import pytest
from cotizacion.config import Config
from cotizacion.domain.pago import ResultadoSimuladorPago
from cotizacion.infrastructure.pago_simulado import SimuladorPago

VARIABLES = (
    "DATABASE_URL",
    "COTIZACION_VIGENCIA_DIAS",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "COTIZACION_REGLAS_CACHE_SEGUNDOS",
    "COTIZACION_PERFIL_SCORE_SIMULADO",
    "PAGO_SIMULADO_RESULTADO",
    "COTIZACION_CONCILIACION_EMISION_HABILITADA",
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


@pytest.mark.parametrize("resultado", ["confirmado", "rechazado"])
def test_resultado_simulado_se_configura_solo_en_servidor(monkeypatch, resultado):
    monkeypatch.setenv("PAGO_SIMULADO_RESULTADO", resultado)
    config = Config.desde_entorno()
    assert SimuladorPago(config.pago_simulado_resultado).evaluar() == ResultadoSimuladorPago(
        resultado
    )


@pytest.mark.parametrize("resultado", ["", "aprobado", "pendiente", "RECHAZADO", "false"])
def test_rechaza_configuracion_invalida_del_simulador(monkeypatch, resultado):
    monkeypatch.setenv("PAGO_SIMULADO_RESULTADO", resultado)
    with pytest.raises(ValueError, match="PAGO_SIMULADO_RESULTADO"):
        Config.desde_entorno()
    with pytest.raises(ValueError):
        SimuladorPago(resultado)


@pytest.mark.parametrize("value", ["", "1", "yes", "academica", "ninguna"])
def test_conciliacion_rechaza_flag_invalido(monkeypatch, value):
    monkeypatch.setenv("COTIZACION_CONCILIACION_EMISION_HABILITADA", value)
    with pytest.raises(ValueError, match="CONCILIACION"):
        Config.desde_entorno()


def test_conciliacion_opt_in_requiere_integracion_polizas(monkeypatch):
    monkeypatch.setenv("COTIZACION_CONCILIACION_EMISION_HABILITADA", "true")
    monkeypatch.delenv("POLIZAS_SERVICE_URL", raising=False)
    with pytest.raises(ValueError, match="POLIZAS_SERVICE_URL"):
        Config.desde_entorno()
    monkeypatch.setenv("POLIZAS_SERVICE_URL", "http://127.0.0.1:5004")
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="INTERNAL_API_TOKEN"):
        Config.desde_entorno()
    monkeypatch.setenv("INTERNAL_API_TOKEN", "solo-pruebas-locales")
    assert Config.desde_entorno().conciliacion_emision_habilitada is True
