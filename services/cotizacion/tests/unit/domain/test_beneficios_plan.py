from decimal import Decimal

import pytest
from cotizacion.domain.beneficios_plan import NIVELES, VERSION_BENEFICIOS, beneficios_plan
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.productos import PRODUCTOS, buscar_producto


@pytest.mark.parametrize(
    "producto",
    [p for p in PRODUCTOS if p.producto_id != "parametric-weather"],
    ids=lambda p: p.producto_id,
)
def test_beneficios_crecen_sin_inventar_coberturas_del_producto(producto):
    planes = [beneficios_plan(producto, {}, nivel, Decimal("25000000")) for nivel in NIVELES]
    catalogo = [f"{c.nombre}: {c.descripcion}" for c in producto.coberturas]
    for indice, plan in enumerate(planes):
        assert plan["version"] == VERSION_BENEFICIOS
        assert plan["coberturas"] == catalogo[: indice + 1]
        assert len(plan["beneficiosAdicionales"]) == indice + 1
        assert len(set(plan["beneficiosAdicionales"])) == indice + 1
        assert "Base asegurada: $25.000.000 COP" in plan["coberturaPrincipal"]
        assert all(c.nombre in plan["descripcion"] for c in producto.coberturas[: indice + 1])
        if indice:
            anterior = planes[indice - 1]
            assert set(anterior["coberturas"]) <= set(plan["coberturas"])
            assert set(anterior["beneficiosAdicionales"]) < set(plan["beneficiosAdicionales"])
            assert anterior["descripcion"] != plan["descripcion"]
    assert len({p["coberturaPrincipal"] for p in planes}) == min(len(catalogo), 3)


@pytest.mark.parametrize(
    ("modalidad", "cantidades"),
    [
        ("delay-cancellation", [1, 2, 2]),
        ("missed-connection", [1, 1, 1]),
        ("comprehensive", [2, 3, 3]),
    ],
)
def test_vuelos_respetan_modalidad_y_no_inventan_disparadores(modalidad, cantidades):
    producto = buscar_producto("parametric")
    assert producto is not None
    planes = [
        beneficios_plan(producto, {"coverageType": modalidad}, nivel, Decimal("1500000"))
        for nivel in NIVELES
    ]
    assert [len(p["coberturas"]) for p in planes] == cantidades
    assert [len(set(p["beneficiosAdicionales"])) for p in planes] == [1, 2, 3]
    for plan in planes:
        texto = " ".join(plan["coberturas"])
        assert ("Pérdida de conexión" in texto) == (modalidad != "delay-cancellation")
        assert "tres horas" not in texto
        if modalidad == "missed-connection":
            assert "Cancelación" not in texto and "Retraso de vuelo" not in texto
        elif modalidad == "comprehensive":
            assert "Cancelación" in texto
        if modalidad != "delay-cancellation":
            assert any("culpa del pasajero" in c for c in plan["condiciones"])


@pytest.mark.parametrize(
    ("codigo", "datos"),
    [("inexistente", {}), ("premium", {"coverageType": "inexistente"})],
)
def test_rechaza_nivel_y_modalidad_desconocidos(codigo, datos):
    producto = buscar_producto("parametric")
    assert producto is not None
    with pytest.raises(ReglaDeNegocioError):
        beneficios_plan(producto, datos, codigo, Decimal("1500000"))


@pytest.mark.parametrize("evento", ["heavy-rain", "drought"])
def test_clima_amplia_orientacion_sin_cambiar_evento_monto_o_periodo(evento):
    producto = buscar_producto("parametric-weather")
    assert producto is not None
    datos = {
        "weatherEvent": evento,
        "location": "Medellín, Antioquia",
        "insuredAmount": "10000000",
        "startDate": "2030-01-01",
        "endDate": "2030-01-30",
    }
    planes = [
        beneficios_plan(producto, datos, nivel, Decimal("10000000"))
        for nivel in ("esencial", "plus", "integral")
    ]
    assert [len(p["beneficiosAdicionales"]) for p in planes] == [1, 2, 3]
    assert len({tuple(p["coberturas"]) for p in planes}) == 1
    for plan in planes:
        assert len(plan["coberturas"]) == 1
        assert "$10.000.000 COP" in plan["coberturaPrincipal"]
        assert plan["deducible"] == "Sin deducible"
        assert plan["vigencia"] == "Del 2030-01-01 al 2030-01-30 (30 días inclusive)"
        assert "Medellín, Antioquia" in plan["descripcion"]
    assert set(planes[0]["beneficiosAdicionales"]) < set(planes[1]["beneficiosAdicionales"])
    assert set(planes[1]["beneficiosAdicionales"]) < set(planes[2]["beneficiosAdicionales"])


def test_rechaza_evento_climatico_desconocido():
    producto = buscar_producto("parametric-weather")
    assert producto is not None
    with pytest.raises(ReglaDeNegocioError):
        beneficios_plan(producto, {"weatherEvent": "otro"}, "esencial", Decimal("10000000"))
