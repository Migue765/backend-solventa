import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.decision import construir_instantanea, contenido_integro
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import EntradaOfertaInvalidaError
from cotizacion.domain.oferta_socio import factores_planes, hash_oferta
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa

AHORA = datetime(2026, 10, 28, tzinfo=timezone.utc)


def decision_academica(score: str = "0.50", primas=("99000", "113850", "128700")) -> dict:
    return {
        "perfil": {"score": score},
        "regla": {"tasa": "0.0005", "factorPerfil": "0.20"},
        "tarificacion": {
            "modelo": "academico-v1",
            "baseAsegurada": "180000000",
            "duracion": 1,
            "periodicidad": "mensual",
            "redondeo": "COP entero; ROUND_HALF_UP",
            "planes": [
                {"codigo": codigo, "nombre": f"Vida {codigo}", "factor": factor, "prima": prima}
                for (codigo, factor), prima in zip(
                    (("esencial", "1.00"), ("integral", "1.15"), ("premium", "1.30")),
                    primas,
                    strict=True,
                )
            ],
        },
    }


def test_explica_los_planes_academicos_sin_volver_a_aplicar_el_score():
    factores = factores_planes(decision_academica())

    assert len(factores) == 3
    esencial = {f["codigo"]: f for f in factores[0]}
    assert esencial["base_asegurada"]["valor"] == "180000000"
    assert esencial["ajuste_riesgo"]["valor"] == "1.1"
    assert "nivel A" in esencial["ajuste_riesgo"]["descripcion"]
    assert {f["codigo"]: f["valor"] for f in factores[2]}["factor_plan"] == "1.30"


def test_nivel_de_riesgo_sigue_al_score():
    factores = factores_planes(decision_academica("0.80", primas=("104400", "120060", "135720")))

    assert "nivel C" in {f["codigo"]: f for f in factores[0]}["ajuste_riesgo"]["descripcion"]


@pytest.mark.parametrize(
    "alterar",
    [
        lambda d: d["tarificacion"]["planes"][1].update(prima="113851"),
        lambda d: d["perfil"].update(score="0.10"),
        lambda d: d["regla"].update(factorPerfil="0.30"),
    ],
)
def test_una_prima_no_reproducible_es_inconsistente(alterar):
    decision = decision_academica()
    alterar(decision)

    with pytest.raises(EntradaOfertaInvalidaError) as error:
        factores_planes(decision)

    assert error.value.codigo == "decision_inconsistente"


@pytest.mark.parametrize(
    ("cambio", "codigo"),
    [
        ({"tarificacion": None}, "decision_sin_tarificacion"),
        ({"tarificacion": {"modelo": "otro", "planes": [{}]}}, "modelo_no_soportado"),
    ],
)
def test_entradas_ausentes_o_desconocidas_no_producen_oferta(cambio, codigo):
    with pytest.raises(EntradaOfertaInvalidaError) as error:
        factores_planes({**decision_academica(), **cambio})

    assert error.value.codigo == codigo


def test_climatico_rechaza_un_factor_de_riesgo_distinto_al_del_perfil():
    decision = {
        "perfil": {"score": "0.90"},
        "tarificacion": {
            "modelo": "clima-v1",
            "factorRiesgoOfertas": "1.00",
            "planes": [{"codigo": "integral"}],
        },
    }

    with pytest.raises(EntradaOfertaInvalidaError):
        factores_planes(decision)


def test_hash_de_oferta_es_canonico():
    contenido = {"b": [1, 2], "a": {"y": "1", "x": "2"}}

    assert hash_oferta(contenido) == hash_oferta(copy.deepcopy(dict(reversed(contenido.items()))))
    assert hash_oferta(contenido) != hash_oferta({**contenido, "b": [2, 1]})


def test_contenido_integro_detecta_alteraciones():
    cobertura = CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), "COP"), Dinero.cero("COP"))
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[cobertura])
    cotizacion.calcular(
        {cobertura.cobertura_id: Dinero(Decimal("10"), "COP")}, AHORA, timedelta(days=1)
    )
    regla = ReglaTarifa(uuid4(), "v1", uuid4(), Decimal("0.01"), Decimal("0.1"), AHORA, None)
    instantanea = construir_instantanea(
        cotizacion, regla, PerfilRiesgo("p", Decimal("0.5"), "m"), AHORA
    )

    assert contenido_integro(instantanea)
    alterada = copy.deepcopy(instantanea.contenido)
    alterada["regla"]["tasa"] = "0.02"
    assert not contenido_integro(replace(instantanea, contenido=alterada))
