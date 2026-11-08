from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.persistence.models import (
    DecisionSuscripcionModel,
    InstantaneaDecisionModel,
    OutboxEventoModel,
    SagaEmisionModel,
    SeleccionOfertaModel,
    SuscripcionModel,
)
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from .test_solicitudes_api import nueva_solicitud


def aceptar(client, producto="automotive", cliente_id=None):
    payload = nueva_solicitud(producto_id=producto, cliente_id=cliente_id)
    creada = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert creada.status_code == 201
    base = f"/v1/solicitudes-cotizacion/{creada.get_json()['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(base + "/ofertas", query_string=query).get_json()
    oferta = ofertas["ofertas"][0]
    seleccion = client.put(
        base + "/seleccion",
        query_string=query,
        json={"ofertaId": oferta["id"], "version": oferta["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert seleccion.status_code == 200
    return base, query, datetime.fromisoformat(ofertas["vigenteHasta"].replace("Z", "+00:00"))


def suscribir(client, base, query, clave=None):
    return client.post(
        base + "/suscripcion",
        query_string=query,
        headers={"Idempotency-Key": clave or str(uuid4())},
    )


def sin_efectos(database_url):
    with Session(create_engine(database_url)) as session:
        for modelo in (SuscripcionModel, DecisionSuscripcionModel, SagaEmisionModel):
            assert session.scalar(select(func.count()).select_from(modelo)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "SuscripcionAprobada")
            )
            == 0
        )


@pytest.mark.parametrize("producto", ["automotive", "parametric-weather"])
@pytest.mark.parametrize("segundos", [0, 1])
def test_no_suscribe_oferta_vencida(client, database_url, monkeypatch, producto, segundos):
    base, query, vencimiento = aceptar(client, producto)
    monkeypatch.setattr(
        client.application.extensions["procesar_suscripcion"],
        "_reloj",
        lambda: vencimiento + timedelta(seconds=segundos),
    )
    respuesta = suscribir(client, base, query)
    assert respuesta.status_code == 422
    assert respuesta.get_json() == {"error": "oferta_no_disponible"}
    assert client.get(base + "/suscripcion", query_string=query).status_code == 404
    sin_efectos(database_url)


@pytest.mark.parametrize(
    ("modelo", "valores"),
    [
        (SeleccionOfertaModel, {"oferta_id": uuid4()}),
        (SeleccionOfertaModel, {"version": 999}),
        (SeleccionOfertaModel, {"prima_mensual": 1}),
        (SeleccionOfertaModel, {"periodicidad": "por_viaje"}),
        (InstantaneaDecisionModel, {"hash_contenido": "0" * 64}),
        (InstantaneaDecisionModel, {"hash_decision": "0" * 64}),
        (InstantaneaDecisionModel, {"regla_version": "otra-version"}),
        (InstantaneaDecisionModel, {"regla_id": uuid4()}),
        (InstantaneaDecisionModel, {"perfil_id": str(uuid4())}),
        (InstantaneaDecisionModel, {"perfil_modelo_version": "otra-version"}),
    ],
)
def test_no_suscribe_seleccion_o_decision_inconsistente(client, database_url, modelo, valores):
    base, query, _ = aceptar(client)
    with Session(create_engine(database_url)) as session, session.begin():
        session.execute(update(modelo).values(**valores))
    respuesta = suscribir(client, base, query)
    assert respuesta.status_code == 422
    assert respuesta.get_json() == {"error": "oferta_no_disponible"}
    sin_efectos(database_url)


def test_revalida_vigencia_despues_de_consultar_ofertas(client, database_url, monkeypatch):
    base, query, vencimiento = aceptar(client)
    tiempos = iter((vencimiento - timedelta(seconds=1), vencimiento))
    monkeypatch.setattr(
        client.application.extensions["procesar_suscripcion"], "_reloj", lambda: next(tiempos)
    )
    respuesta = suscribir(client, base, query)
    assert respuesta.status_code == 422
    assert respuesta.get_json() == {"error": "oferta_no_disponible"}
    sin_efectos(database_url)


def test_replay_no_revalida_vigencia_ni_perfil(client, database_url, monkeypatch):
    base, query, vencimiento = aceptar(client)
    clave = str(uuid4())
    original = suscribir(client, base, query, clave)
    assert original.status_code == 201
    reiniciado = create_app(Config(database_url=database_url)).test_client()
    proceso = reiniciado.application.extensions["procesar_suscripcion"]
    monkeypatch.setattr(proceso, "_reloj", lambda: vencimiento + timedelta(seconds=1))

    def no_revalidar(*args):
        raise AssertionError("Un replay no debe consultar el perfil ni recalcular ofertas.")

    monkeypatch.setattr("cotizacion.application.ofertas.validar_perfil", no_revalidar)
    monkeypatch.setattr(proceso._ofertas, "listar", no_revalidar)
    for otra_clave in (clave, str(uuid4())):
        replay = suscribir(reiniciado, base, query, otra_clave)
        assert replay.status_code == 201
        assert replay.get_json() == original.get_json()
    with Session(create_engine(database_url)) as session:
        for modelo in (SuscripcionModel, DecisionSuscripcionModel, SagaEmisionModel):
            assert session.scalar(select(func.count()).select_from(modelo)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "SuscripcionAprobada")
            )
            == 1
        )


def test_idempotencia_no_se_reutiliza_en_otra_solicitud(client):
    cliente_id = str(uuid4())
    base, query, _ = aceptar(client, cliente_id=cliente_id)
    otra_base, _, _ = aceptar(client, cliente_id=cliente_id)
    clave = str(uuid4())
    assert suscribir(client, base, query, clave).status_code == 201
    assert suscribir(client, otra_base, query, clave).status_code == 409
