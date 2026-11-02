from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from cotizacion.api.schemas import CrearCotizacionRequest, cotizacion_a_json, pago_simulado_a_json
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.pago import PagoSimulado, PagoSimuladoRechazado
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import _fecha_dashboard
from pydantic import ValidationError


def cuerpo(**cambios):
    datos = {
        "clienteId": str(uuid4()),
        "productoId": str(uuid4()),
        "perfilId": "perfil-1",
        "moneda": "COP",
        "coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "1000"}],
    }
    datos.update(cambios)
    return datos


def test_convierte_solicitud_en_comando():
    datos = cuerpo()

    comando = CrearCotizacionRequest.model_validate(datos).a_comando()

    assert str(comando.cliente_id) == datos["clienteId"]
    assert comando.perfil_id == "perfil-1"
    assert comando.moneda == "COP"
    assert comando.coberturas[0].suma_asegurada == Decimal("1000")
    assert comando.coberturas[0].deducible == Decimal("0")


@pytest.mark.parametrize(
    "cambios",
    [
        {"clienteId": "no-es-uuid"},
        {"moneda": "pesos"},
        {"perfilId": ""},
        {"coberturas": []},
        {"coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "0"}]},
        {"coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "10", "deducible": "-1"}]},
        {"campoDesconocido": 1},
    ],
)
def test_rechaza_solicitudes_invalidas(cambios):
    with pytest.raises(ValidationError):
        CrearCotizacionRequest.model_validate(cuerpo(**cambios))


def test_serializa_cotizacion_en_camel_case_y_montos_como_texto():
    cobertura = CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), "COP"), Dinero.cero("COP"))
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[cobertura])
    ahora = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cotizacion.calcular(
        {cobertura.cobertura_id: Dinero(Decimal("15"), "COP")}, ahora, timedelta(days=30)
    )

    json = cotizacion_a_json(cotizacion)

    assert json["cotizacionId"] == str(cotizacion.cotizacion_id)
    assert json["estado"] == "CALCULADA"
    assert json["primaEstimada"] == {"monto": "15.00", "moneda": "COP"}
    assert json["validaHasta"] == "2026-10-31T00:00:00+00:00"
    assert json["coberturas"][0]["sumaAsegurada"] == {"monto": "1000.00", "moneda": "COP"}


@pytest.mark.parametrize("desfase", [-5, 0, 5.5])
@pytest.mark.parametrize("rechazado", [False, True])
def test_snapshot_pago_es_identico_al_recuperar_otro_huso_horario(desfase, rechazado):
    instante = datetime(2026, 11, 1, 2, 3, 4, 123456, tzinfo=UTC)
    en_base = instante.astimezone(timezone(timedelta(hours=desfase)))
    campos = {
        "pago_id": uuid4(),
        "solicitud_id": "AQT-2026-ABCDEF1234",
        "suscripcion_id": uuid4(),
        "oferta_id": uuid4(),
        "oferta_version": 1,
        "importe": Dinero(Decimal("100.00"), "COP"),
        "periodicidad": "mensual",
    }
    if rechazado:
        pago = PagoSimuladoRechazado(
            **campos, rechazada_en=instante, saga_version=1, saga_actualizada_en=instante
        )
        recuperado = replace(pago, rechazada_en=en_base, saga_actualizada_en=en_base)
    else:
        pago = PagoSimulado(**campos, confirmada_en=instante, saga_version=2)
        recuperado = replace(pago, confirmada_en=en_base)
    esperado = pago_simulado_a_json(pago)
    assert esperado["saga"]["actualizadaEn"] == "2026-11-01T02:03:04.123456Z"
    assert pago_simulado_a_json(recuperado) == esperado


@pytest.mark.parametrize("desfase", [None, -5, 0, 5.5])
def test_dashboard_normaliza_el_instante_utc_sin_cambiarlo(desfase):
    instante = datetime(2026, 11, 1, 2, 3, 4, 123456, tzinfo=UTC)
    en_base = (
        instante.replace(tzinfo=None)
        if desfase is None
        else instante.astimezone(timezone(timedelta(hours=desfase)))
    )
    assert _fecha_dashboard(en_base) == "2026-11-01T02:03:04.123456Z"
