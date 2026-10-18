import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    DecisionSuscripcionModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
    SeleccionOfertaModel,
    SuscripcionModel,
)
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

from .test_solicitudes_api import nueva_solicitud


def preparar(client, cliente_id=None, producto_id="automotive"):
    payload = nueva_solicitud(cliente_id=cliente_id, producto_id=producto_id)
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 201
    base = f"/v1/solicitudes-cotizacion/{respuesta.get_json()['id']}"
    query = {"clienteId": payload["clienteId"]}
    oferta = client.get(base + "/ofertas", query_string=query).get_json()["ofertas"][0]
    seleccion = client.put(
        base + "/seleccion",
        query_string=query,
        json={"ofertaId": oferta["id"], "version": oferta["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert seleccion.status_code == 200
    suscripcion = client.post(
        base + "/suscripcion",
        query_string=query,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert suscripcion.status_code == 201
    return base, query, seleccion.get_json(), suscripcion.get_json()


def pagar(client, base, query, clave=None, **kwargs):
    return client.post(
        base + "/pago-simulado",
        query_string=query,
        headers={"Idempotency-Key": clave or str(uuid4())},
        **kwargs,
    )


@pytest.mark.parametrize(
    ("producto", "periodicidad"),
    [
        ("automotive", "mensual"),
        ("travel", "por_viaje"),
        ("parametric", "por_vuelo"),
        ("parametric-weather", "por_periodo"),
    ],
)
def test_pago_persiste_importe_y_periodicidad_de_la_oferta(
    client, database_url, producto, periodicidad, monkeypatch
):
    base, query, seleccion, suscripcion = preparar(client, producto_id=producto)

    def red_prohibida(*args, **kwargs):
        raise AssertionError("El pago simulado no puede usar red externa")

    monkeypatch.setattr("urllib.request.urlopen", red_prohibida)
    respuesta = pagar(client, base, query, json={})
    assert respuesta.status_code == 200, respuesta.get_json()
    pago = respuesta.get_json()
    assert pago == {
        "id": pago["id"],
        "solicitudId": seleccion["solicitudId"],
        "suscripcionId": suscripcion["id"],
        "ofertaId": seleccion["ofertaId"],
        "ofertaVersion": seleccion["version"],
        "estado": "confirmado",
        "simulada": True,
        "referenciaInterna": f"SIM-{pago['id']}",
        "importe": seleccion["prima"],
        "periodicidad": periodicidad,
        "confirmadaEn": pago["confirmadaEn"],
        "saga": {"estado": "pago_confirmado", "version": 2, "actualizadaEn": pago["confirmadaEn"]},
    }
    nuevo_cliente = create_app(Config(database_url=database_url)).test_client()
    assert nuevo_cliente.get(base + "/pago-simulado", query_string=query).get_json() == pago
    actual = nuevo_cliente.get(base + "/suscripcion", query_string=query).get_json()
    assert actual["estado"] == "aprobada"
    assert actual["saga"] == pago["saga"]
    with Session(create_engine(database_url)) as sesion:
        eventos = sesion.scalars(
            select(OutboxEventoModel).where(OutboxEventoModel.tipo == "PagoSimuladoConfirmado")
        ).all()
        assert len(eventos) == 1
        assert eventos[0].payload["simulada"] is True
        assert eventos[0].payload["importe"] == pago["importe"]


def test_distingue_pago_ausente_de_solicitud_ajena(client):
    base, query, _, _ = preparar(client)
    assert client.get(base + "/pago-simulado", query_string=query).get_json() == {
        "error": "pago_no_encontrado"
    }
    ajeno = {"clienteId": str(uuid4())}
    for operacion in (
        lambda: client.get(base + "/pago-simulado", query_string=ajeno),
        lambda: pagar(client, base, ajeno),
    ):
        respuesta = operacion()
        assert respuesta.status_code == 404
        assert respuesta.get_json()["error"] == "no_encontrado"


@pytest.mark.parametrize("datos", [{"importe": "1"}, {"tarjeta": "1234"}, [], None, ""])
def test_rechaza_datos_cliente(client, datos):
    base, query, _, _ = preparar(client)
    respuesta = pagar(client, base, query, data=json.dumps(datos), content_type="application/json")
    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "solicitud_invalida"


@pytest.mark.parametrize("clave", [None, "", "no-uuid"])
def test_requiere_clave_uuid(client, clave):
    base, query, _, _ = preparar(client)
    respuesta = client.post(
        base + "/pago-simulado",
        query_string=query,
        headers={} if clave is None else {"Idempotency-Key": clave},
    )
    assert respuesta.status_code == 400


@pytest.mark.parametrize(
    ("modelo", "valores"),
    [
        (SuscripcionModel, {"estado": "rechazada"}),
        (SuscripcionModel, {"estado": "revision"}),
        (DecisionSuscripcionModel, {"resultado": "rechazada"}),
        (SagaEmisionModel, {"estado": "fallida"}),
        (SagaEmisionModel, {"estado": "emitida"}),
        (SeleccionOfertaModel, {"version": 999}),
        (SeleccionOfertaModel, {"oferta_id": uuid4()}),
        (SeleccionOfertaModel, {"prima_mensual": 0}),
        (SeleccionOfertaModel, {"periodicidad": "unico"}),
        (CotizacionModel, {"valida_hasta": datetime.now(UTC) - timedelta(days=1)}),
    ],
)
def test_no_confirma_estados_o_selecciones_invalidos(client, database_url, modelo, valores):
    base, query, _, _ = preparar(client)
    with Session(create_engine(database_url)) as sesion, sesion.begin():
        sesion.execute(update(modelo).values(**valores))
    respuesta = pagar(client, base, query)
    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "pago_no_permitido"
    with Session(create_engine(database_url)) as sesion:
        assert sesion.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 0


def test_sin_suscripcion_no_paga(client):
    payload = nueva_solicitud()
    creada = client.post(
        "/v1/solicitudes-cotizacion", json=payload, headers={"Idempotency-Key": str(uuid4())}
    )
    base = f"/v1/solicitudes-cotizacion/{creada.get_json()['id']}"
    assert pagar(client, base, {"clienteId": payload["clienteId"]}).status_code == 404


def test_replay_sobrevive_expiracion_y_avance_saga(client, database_url):
    base, query, _, _ = preparar(client)
    clave = str(uuid4())
    pago = pagar(client, base, query, clave).get_json()
    with Session(create_engine(database_url)) as sesion, sesion.begin():
        sesion.execute(
            update(CotizacionModel).values(valida_hasta=datetime.now(UTC) - timedelta(days=1))
        )
        sesion.execute(update(SagaEmisionModel).values(estado="emitida", version=3))
    for otra_clave in (clave, str(uuid4())):
        replay = pagar(client, base, query, otra_clave)
        assert replay.status_code == 200
        assert replay.get_json() == pago
    assert client.get(base + "/pago-simulado", query_string=query).get_json() == pago
    with Session(create_engine(database_url)) as sesion:
        assert sesion.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
        saga = sesion.get(SagaEmisionModel, UUID(pago["suscripcionId"]))
        assert (saga.estado, saga.version) == ("emitida", 3)


def test_clave_no_se_reutiliza_en_otra_solicitud(client):
    cliente_id = str(uuid4())
    base, query, _, _ = preparar(client, cliente_id)
    otra_base, _, _, _ = preparar(client, cliente_id)
    clave = str(uuid4())
    assert pagar(client, base, query, clave).status_code == 200
    assert pagar(client, otra_base, query, clave).status_code == 409


@pytest.mark.parametrize("misma_clave", [True, False])
def test_peticiones_concurrentes_no_duplican_pago_ni_evento(client, database_url, misma_clave):
    base, query, _, _ = preparar(client)
    barrera = Barrier(6)
    clave = str(uuid4())

    def confirmar(_):
        barrera.wait(timeout=15)
        with client.application.test_client() as concurrente:
            respuesta = pagar(concurrente, base, query, clave if misma_clave else str(uuid4()))
            assert respuesta.status_code == 200, respuesta.get_json()
            return respuesta.get_json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        pagos = list(pool.map(confirmar, range(6)))
    assert all(pago == pagos[0] for pago in pagos)
    with Session(create_engine(database_url)) as sesion:
        assert sesion.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
        assert (
            sesion.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "PagoSimuladoConfirmado")
            )
            == 1
        )
        assert sesion.scalar(select(SagaEmisionModel.version)) == 2


def test_error_outbox_revierte_pago_y_saga(client, database_url):
    base, query, _, _ = preparar(client)

    def fallar(*_):
        raise RuntimeError("Fallo controlado de persistencia")

    event.listen(OutboxEventoModel, "before_insert", fallar)
    try:
        assert pagar(client, base, query).status_code == 500
    finally:
        event.remove(OutboxEventoModel, "before_insert", fallar)
    with Session(create_engine(database_url)) as sesion:
        assert sesion.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 0
        saga = sesion.scalar(select(SagaEmisionModel))
        assert (saga.estado, saga.version) == ("pago_pendiente", 1)
    assert pagar(client, base, query).status_code == 200


def test_clave_concurrente_en_distintas_solicitudes_no_confirma_ambas(client, database_url):
    cliente_id = str(uuid4())
    bases = [preparar(client, cliente_id)[0] for _ in range(2)]
    query = {"clienteId": cliente_id}
    clave = str(uuid4())
    barrera = Barrier(2)

    def confirmar(base):
        barrera.wait(timeout=15)
        with client.application.test_client() as concurrente:
            return pagar(concurrente, base, query, clave).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = list(pool.map(confirmar, bases))
    assert sorted(resultados) == [200, 409]
    with Session(create_engine(database_url)) as sesion:
        assert sesion.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
        assert sorted(sesion.scalars(select(SagaEmisionModel.version))) == [1, 2]
