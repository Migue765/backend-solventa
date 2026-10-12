from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.decision import construir_instantanea
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa

AHORA = datetime(2026, 10, 1, tzinfo=timezone.utc)
REGLA = ReglaTarifa(uuid4(), "v1", uuid4(), Decimal("0.01"), Decimal("0.1"), AHORA, None)
PERFIL = PerfilRiesgo("perfil-1", Decimal("0.5"), "modelo-1")


def cotizacion_calculada(prima="100", cobertura_id=None):
    cobertura = CoberturaCotizada(
        cobertura_id or uuid4(), Dinero(Decimal("1000"), "COP"), Dinero.cero("COP")
    )
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[cobertura])
    cotizacion.calcular(
        {cobertura.cobertura_id: Dinero(Decimal(prima), "COP")}, AHORA, timedelta(days=30)
    )
    return cotizacion


def test_la_instantanea_registra_versiones_y_referencias():
    cotizacion = cotizacion_calculada()

    instantanea = construir_instantanea(cotizacion, REGLA, PERFIL, AHORA)

    assert instantanea.regla_version == "v1"
    assert instantanea.perfil_modelo_version == "modelo-1"
    assert instantanea.contenido["resultado"]["primaEstimada"] == "100.00"
    assert instantanea.contenido["cotizacionId"] == str(cotizacion.cotizacion_id)


def test_el_hash_de_decision_no_depende_de_identificadores_ni_fecha():
    cobertura_id = uuid4()

    a = construir_instantanea(cotizacion_calculada(cobertura_id=cobertura_id), REGLA, PERFIL, AHORA)
    b = construir_instantanea(
        cotizacion_calculada(cobertura_id=cobertura_id), REGLA, PERFIL, AHORA + timedelta(days=1)
    )

    assert a.hash_decision == b.hash_decision
    assert a.hash_contenido != b.hash_contenido


def test_otra_prima_cambia_el_hash_de_decision():
    cobertura_id = uuid4()

    a = construir_instantanea(cotizacion_calculada("100", cobertura_id), REGLA, PERFIL, AHORA)
    b = construir_instantanea(cotizacion_calculada("101", cobertura_id), REGLA, PERFIL, AHORA)

    assert a.hash_decision != b.hash_decision


def test_una_prima_de_cero_no_es_valida():
    with pytest.raises(ReglaDeNegocioError, match="mayor a cero"):
        cotizacion_calculada(prima="0")


def test_la_decision_se_registra_una_sola_vez():
    cotizacion = cotizacion_calculada()
    instantanea = construir_instantanea(cotizacion, REGLA, PERFIL, AHORA)
    cotizacion.registrar_decision(instantanea)

    with pytest.raises(ReglaDeNegocioError, match="ya fue registrada"):
        cotizacion.registrar_decision(instantanea)
