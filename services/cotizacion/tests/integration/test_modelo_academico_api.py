from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.domain.tarifa import PerfilRiesgo
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    CoberturaCotizadaModel,
    CotizacionModel,
    InstantaneaDecisionModel,
    OutboxEventoModel,
    ReglaTarifaModel,
)
from sqlalchemy import event, func, select, update

from .test_solicitudes_api import DATOS_PRODUCTO, nueva_solicitud


def registrar(client, producto_id, datos=None, **cambios):
    payload = nueva_solicitud(producto_id=producto_id, datos_producto=datos, **cambios)
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.get_json()
    return payload, response.get_json()


def ofertas(client, payload, registro):
    response = client.get(
        f"/v1/solicitudes-cotizacion/{registro['id']}/ofertas",
        query_string={"clienteId": payload["clienteId"]},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


@pytest.mark.parametrize(
    ("producto", "importes", "periodicidad"),
    [
        ("automotive", ["41250.00", "47438.00", "53625.00"], "mensual"),
        ("travel", ["17600.00", "20240.00", "22880.00"], "por_viaje"),
        ("life", ["33000.00", "37950.00", "42900.00"], "mensual"),
        ("device-protection", ["19800.00", "22770.00", "25740.00"], "mensual"),
        ("parametric", ["16500.00", "18975.00", "21450.00"], "por_vuelo"),
        ("payment-protection", ["28050.00", "32258.00", "36465.00"], "mensual"),
        ("mortgage-life", ["99000.00", "113850.00", "128700.00"], "mensual"),
    ],
)
def test_importes_exactos_por_producto_y_plan(
    client, database_url, producto, importes, periodicidad
):
    payload, registro = registrar(client, producto)
    resultado = ofertas(client, payload, registro)
    planes = resultado["ofertas"]
    assert [plan["prima"]["monto"] for plan in planes] == importes
    assert all(plan["periodicidad"] == periodicidad for plan in planes)
    assert all(plan["prima"]["moneda"] == "COP" for plan in planes)
    assert registro["cotizacion"]["primaEstimada"]["monto"] == importes[0]
    for plan in planes:
        assert plan["primaMensual"] == (plan["prima"] if periodicidad == "mensual" else None)
    with crear_sesiones(database_url)() as sesion:
        snapshot = sesion.get(
            InstantaneaDecisionModel, UUID(registro["cotizacion"]["cotizacionId"])
        )
        assert snapshot.regla_version == "academico-v1"
        assert snapshot.contenido["tarificacion"]["datosProducto"] == DATOS_PRODUCTO[producto]
        assert [
            Decimal(plan["prima"]) for plan in snapshot.contenido["tarificacion"]["planes"]
        ] == [Decimal(monto) for monto in importes]
        assert snapshot.contenido["perfil"]["score"] == "0.50"


@pytest.mark.parametrize(
    ("producto", "campo", "valor", "esperada"),
    [
        ("device-protection", "purchaseValue", "9000000", "39600.00"),
        ("device-protection", "purchaseValue", "100000", "440.00"),
        ("payment-protection", "monthlyPayment", "3000000", "99000.00"),
        ("mortgage-life", "mortgageBalance", "90000000", "49500.00"),
        ("travel", "returnDate", "2026-11-01", "2200.00"),
        ("travel", "returnDate", "2026-11-16", "35200.00"),
    ],
)
def test_sensibilidad_a_datos_de_solicitud(client, producto, campo, valor, esperada):
    original_payload, original = registrar(client, producto)
    payload, nueva = registrar(client, producto, {**DATOS_PRODUCTO[producto], campo: valor})
    prima_original = ofertas(client, original_payload, original)["ofertas"][0]["prima"]
    prima_nueva = ofertas(client, payload, nueva)["ofertas"][0]["prima"]
    assert prima_nueva["monto"] == esperada
    assert prima_nueva != prima_original


def test_mismas_entradas_y_version_conservan_primas_y_hash(client):
    cliente = str(uuid4())
    _, primera = registrar(client, "mortgage-life", cliente_id=cliente)
    _, segunda = registrar(client, "mortgage-life", cliente_id=cliente)
    assert primera["cotizacion"]["primaEstimada"] == segunda["cotizacion"]["primaEstimada"]
    assert primera["cotizacion"]["decision"] == segunda["cotizacion"]["decision"]


def test_ofertas_y_reintento_leen_resultados_guardados_no_perfil_actual(client, database_url):
    payload = nueva_solicitud(producto_id="mortgage-life")
    headers = {"Idempotency-Key": str(uuid4())}
    registro = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers).get_json()
    iniciales = ofertas(client, payload, registro)
    otro_proceso = create_app(
        Config(database_url=database_url, perfil_score_simulado=Decimal("1"))
    ).test_client()
    with crear_sesiones(database_url).begin() as sesion:
        sesion.execute(update(ReglaTarifaModel).values(tasa=Decimal("0.9")))
    assert ofertas(otro_proceso, payload, registro) == iniciales
    reintento = otro_proceso.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
    assert reintento.get_json() == registro


@pytest.mark.parametrize("score", ["0", "1"])
def test_utiliza_score_y_version_del_perfil_realmente_recibido(client, database_url, score):
    calculadora = client.application.extensions["registrar_solicitud"]._calculadora
    perfil = PerfilRiesgo("perfil-prueba", Decimal(score), "modelo-prueba-v3")
    with patch.object(calculadora._perfiles, "obtener", return_value=perfil):
        payload, registro = registrar(client, "mortgage-life")
    resultado = ofertas(client, payload, registro)
    assert resultado["ofertas"][0]["prima"]["monto"] == (
        "90000.00" if score == "0" else "108000.00"
    )
    assert registro["cotizacion"]["decision"]["perfilId"] == "perfil-prueba"
    with crear_sesiones(database_url)() as sesion:
        snapshot = sesion.get(
            InstantaneaDecisionModel, UUID(registro["cotizacion"]["cotizacionId"])
        )
        assert snapshot.perfil_modelo_version == "modelo-prueba-v3"


@pytest.mark.parametrize("producto", ["travel", "parametric", "mortgage-life"])
def test_seleccion_persiste_prima_y_periodicidad(client, database_url, producto):
    payload, registro = registrar(client, producto)
    plan = ofertas(client, payload, registro)["ofertas"][2]
    url = f"/v1/solicitudes-cotizacion/{registro['id']}/seleccion"
    query = {"clienteId": payload["clienteId"]}
    headers = {"Idempotency-Key": str(uuid4())}
    body = {"ofertaId": plan["id"], "version": plan["version"]}
    response = client.put(url, query_string=query, headers=headers, json=body)
    assert response.status_code == 200
    seleccion = response.get_json()
    assert seleccion["prima"] == plan["prima"]
    assert seleccion["periodicidad"] == plan["periodicidad"]
    assert seleccion["primaMensual"] == plan["primaMensual"]
    nuevo_client = create_app(Config(database_url=database_url)).test_client()
    assert nuevo_client.get(url, query_string=query).get_json() == seleccion
    assert (
        nuevo_client.put(url, query_string=query, headers=headers, json=body).get_json()
        == seleccion
    )


@pytest.mark.parametrize("tipo_fallo", ["sin_perfil", "score_invalido", "sin_regla", "outbox"])
def test_error_controlado_y_transaccion_sin_resultados_parciales(client, database_url, tipo_fallo):
    calculadora = client.application.extensions["registrar_solicitud"]._calculadora
    if tipo_fallo == "sin_regla":
        with crear_sesiones(database_url).begin() as sesion:
            sesion.execute(
                update(ReglaTarifaModel).values(vigente_hasta=datetime(2026, 1, 1, tzinfo=UTC))
            )
    perfil = (
        None
        if tipo_fallo == "sin_perfil"
        else PerfilRiesgo("perfil-prueba", Decimal("2"), "prueba")
        if tipo_fallo == "score_invalido"
        else PerfilRiesgo("perfil-prueba", Decimal("0.5"), "prueba")
    )

    def fallar_outbox(*_args):
        raise RuntimeError("Fallo intencional de outbox")

    if tipo_fallo == "outbox":
        event.listen(OutboxEventoModel, "before_insert", fallar_outbox)
    try:
        with patch.object(calculadora._perfiles, "obtener", return_value=perfil):
            response = client.post(
                "/v1/solicitudes-cotizacion",
                json=nueva_solicitud(producto_id="mortgage-life"),
                headers={"Idempotency-Key": str(uuid4())},
            )
    finally:
        if tipo_fallo == "outbox":
            event.remove(OutboxEventoModel, "before_insert", fallar_outbox)
    assert response.status_code == (500 if tipo_fallo == "outbox" else 422)
    assert "correlationId" in response.get_json()
    with crear_sesiones(database_url)() as sesion:
        for modelo in (
            CotizacionModel,
            CoberturaCotizadaModel,
            InstantaneaDecisionModel,
            OutboxEventoModel,
        ):
            assert sesion.scalar(select(func.count()).select_from(modelo)) == 0


def test_importe_fuera_de_rango_rechaza_sin_persistir(client, database_url):
    datos = {**DATOS_PRODUCTO["payment-protection"], "monthlyPayment": "9999999999999999"}
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(producto_id="payment-protection", datos_producto=datos),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422
    with crear_sesiones(database_url)() as sesion:
        assert sesion.scalar(select(func.count()).select_from(CotizacionModel)) == 0


def test_seleccion_con_importe_superior_a_precision_legada(client, database_url):
    payload, registro = registrar(
        client,
        "mortgage-life",
        {**DATOS_PRODUCTO["mortgage-life"], "mortgageBalance": "9000000000"},
    )
    plan = ofertas(client, payload, registro)["ofertas"][0]
    assert plan["prima"]["monto"] == "4950000.00"
    url = f"/v1/solicitudes-cotizacion/{registro['id']}/seleccion"
    query = {"clienteId": payload["clienteId"]}
    response = client.put(
        url,
        query_string=query,
        headers={"Idempotency-Key": str(uuid4())},
        json={"ofertaId": plan["id"], "version": plan["version"]},
    )
    assert response.status_code == 200
    otro_proceso = create_app(Config(database_url=database_url)).test_client()
    assert otro_proceso.get(url, query_string=query).get_json()["prima"] == plan["prima"]


@pytest.mark.parametrize(
    ("producto", "campo", "valor"),
    [
        ("mortgage-life", "birthDate", "1990-01-01"),
        ("travel", "destinationCountryCode", "CO"),
        ("parametric", "airline", "Otra aerolinea"),
    ],
)
def test_campos_declarativos_no_anaden_factores_de_precio(client, producto, campo, valor):
    payload, primera = registrar(client, producto)
    otros_datos = {**DATOS_PRODUCTO[producto], campo: valor}
    otro_payload, segunda = registrar(client, producto, otros_datos)
    assert [p["prima"] for p in ofertas(client, payload, primera)["ofertas"]] == [
        p["prima"] for p in ofertas(client, otro_payload, segunda)["ofertas"]
    ]
