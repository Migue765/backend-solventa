from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from cotizacion.domain.errors import ErrorConsultaPerfil
from cotizacion.domain.ofertas import EstadoOferta
from cotizacion.infrastructure.persistence import repositorio_cotizaciones
from cotizacion.infrastructure.persistence.models import (
    SeleccionOfertaModel,
    SeleccionProvisionalModel,
    SuscripcionModel,
)
from sqlalchemy import event, func, select

from .test_solicitudes_api import nueva_solicitud


@pytest.fixture(autouse=True)
def cerrar_recursos_propios(client):
    yield
    client.application.extensions["repositorio_cotizaciones"]._sesiones.kw["bind"].dispose()


def contar(client, modelo, *condiciones):
    with client.application.extensions["repositorio_cotizaciones"]._sesiones() as sesion:
        return sesion.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def preparar(client, cliente_id=None):
    payload = nueva_solicitud(cliente_id=cliente_id)
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 201
    url = f"/v1/solicitudes-cotizacion/{respuesta.get_json()['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"]
    return url, query, ofertas


def elegir(client, url, query, oferta, revision, clave=None):
    return client.put(
        f"{url}/seleccion-provisional",
        query_string=query,
        json={"ofertaId": oferta["id"], "version": oferta["version"], "revisionEsperada": revision},
        headers={"Idempotency-Key": clave or str(uuid4())},
    )


def test_preseleccionar_no_acepta_ni_inicia_suscripcion(client):
    url, query, ofertas = preparar(client)
    vacia = client.get(f"{url}/seleccion-provisional", query_string=query)
    assert vacia.status_code == 200
    assert vacia.get_json()["seleccion"] is None
    seleccion = elegir(client, url, query, ofertas[0], 0)
    assert seleccion.status_code == 200
    assert seleccion.get_json()["revision"] == 1
    assert client.get(f"{url}/seleccion", query_string=query).status_code == 404
    assert (
        client.post(
            f"{url}/suscripcion", query_string=query, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 404
    )
    actuales = client.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"]
    assert all(oferta["estado"] == "disponible" for oferta in actuales)
    assert contar(client, SeleccionProvisionalModel) == 1
    assert contar(client, SeleccionOfertaModel) == contar(client, SuscripcionModel) == 0


def test_cambiar_y_repetir_clave_antigua_no_reactiva_seleccion(client):
    url, query, ofertas = preparar(client)
    clave = str(uuid4())
    primera = elegir(client, url, query, ofertas[0], 0, clave)
    assert primera.status_code == 200
    segunda = elegir(client, url, query, ofertas[1], 1)
    assert segunda.status_code == 200
    repetida = elegir(client, url, query, ofertas[0], 0, clave)
    assert repetida.status_code == 200
    assert repetida.get_json() == primera.get_json()
    actual = client.get(f"{url}/seleccion-provisional", query_string=query).get_json()
    assert actual["seleccion"] == segunda.get_json()
    assert actual["seleccion"]["revision"] == 2
    assert actual["aceptacion"] is None
    assert elegir(client, url, query, ofertas[1], 0, clave).status_code == 409
    assert contar(client, SeleccionProvisionalModel) == 2
    assert contar(client, SeleccionProvisionalModel, SeleccionProvisionalModel.activa) == 1


def test_aceptar_exige_referencia_y_revision_provisional_actual(client):
    url, query, ofertas = preparar(client)
    assert elegir(client, url, query, ofertas[0], 0).status_code == 200
    assert elegir(client, url, query, ofertas[1], 1).status_code == 200
    for oferta, revision in [(ofertas[0], 1), (ofertas[1], 1), (ofertas[0], 2)]:
        rechazada = client.put(
            f"{url}/seleccion",
            query_string=query,
            json={"ofertaId": oferta["id"], "version": 1, "revisionSeleccion": revision},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert rechazada.status_code == 409
        assert rechazada.get_json()["error"] == "seleccion_desactualizada"
    aceptada = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[1]["id"], "version": 1, "revisionSeleccion": 2},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert aceptada.status_code == 200
    assert "revision" not in aceptada.get_json()
    assert (
        client.put(
            f"{url}/seleccion",
            query_string=query,
            json={"ofertaId": ofertas[1]["id"], "version": 1},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 409
    )
    assert elegir(client, url, query, ofertas[0], 2).status_code == 409
    assert (
        client.post(
            f"{url}/suscripcion", query_string=query, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 201
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"revisionEsperada": -1},
        {"revisionEsperada": True},
        {"revisionEsperada": None},
        {"revisionEsperada": "0"},
        {"clienteId": str(uuid4())},
        {"ofertaId": "invalida"},
    ],
)
def test_preseleccion_rechaza_payload_invalido(client, changes):
    url, query, ofertas = preparar(client)
    respuesta = client.put(
        f"{url}/seleccion-provisional",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 1, "revisionEsperada": 0, **changes},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 400
    assert contar(client, SeleccionProvisionalModel) == 0


def test_preseleccion_aisla_propietario_y_revalida_autorizacion(client, monkeypatch):
    url, query, ofertas = preparar(client)
    clave = str(uuid4())
    assert elegir(client, url, query, ofertas[0], 0, clave).status_code == 200
    otro = {"clienteId": str(uuid4())}
    assert elegir(client, url, otro, ofertas[0], 0, clave).status_code == 404
    assert client.get(f"{url}/seleccion-provisional", query_string=otro).status_code == 404
    servicio = client.application.extensions["gestionar_ofertas"]
    monkeypatch.setattr(servicio, "_perfiles", object())

    def denegar(*_args):
        raise ErrorConsultaPerfil("consentimiento_no_vigente", 403)

    monkeypatch.setattr("cotizacion.application.ofertas.validar_perfil", denegar)
    assert elegir(client, url, query, ofertas[0], 0, clave).status_code == 403
    assert client.get(f"{url}/seleccion-provisional", query_string=query).status_code == 403
    assert contar(client, SeleccionProvisionalModel) == 1


def test_preseleccion_vencida_no_se_renueva_por_replay(client, monkeypatch):
    url, query, ofertas = preparar(client)
    clave = str(uuid4())
    primera = elegir(client, url, query, ofertas[0], 0, clave)
    servicio = client.application.extensions["gestionar_ofertas"]
    monkeypatch.setattr(servicio, "_reloj", lambda: datetime(2100, 1, 1, tzinfo=UTC))
    assert elegir(client, url, query, ofertas[1], 1).status_code == 422
    assert elegir(client, url, query, ofertas[0], 0, clave).get_json() == primera.get_json()
    estado = client.get(f"{url}/seleccion-provisional", query_string=query).get_json()
    assert estado["estadoOferta"] == "vencida"
    assert estado["seleccion"] == primera.get_json()
    assert (
        client.put(
            f"{url}/seleccion",
            query_string=query,
            json={"ofertaId": ofertas[0]["id"], "version": 1, "revisionSeleccion": 1},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 422
    )
    assert contar(client, SeleccionOfertaModel) == 0


def test_preseleccion_retirada_se_rechaza_sin_otra_seleccion(client, monkeypatch):
    url, query, ofertas = preparar(client)
    servicio = client.application.extensions["gestionar_ofertas"]
    listar = servicio.listar

    def retiradas(*args):
        resultado = listar(*args)
        return replace(
            resultado,
            ofertas=tuple(
                replace(oferta, estado=EstadoOferta.RETIRADA) for oferta in resultado.ofertas
            ),
        )

    monkeypatch.setattr(servicio, "listar", retiradas)
    assert elegir(client, url, query, ofertas[0], 0).status_code == 422
    assert contar(client, SeleccionProvisionalModel) == 0


def test_aceptacion_sin_revision_no_puede_ignorar_preseleccion(client):
    url, query, ofertas = preparar(client)
    assert elegir(client, url, query, ofertas[0], 0).status_code == 200
    rechazada = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 1},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert rechazada.status_code == 409
    assert contar(client, SeleccionOfertaModel) == 0


@pytest.mark.parametrize("revision", [None, True, 0, "1"])
def test_aceptacion_rechaza_revision_invalida(client, revision):
    url, query, ofertas = preparar(client)
    assert (
        client.put(
            f"{url}/seleccion",
            query_string=query,
            json={"ofertaId": ofertas[0]["id"], "version": 1, "revisionSeleccion": revision},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 400
    )
    assert contar(client, SeleccionOfertaModel) == 0


def test_revision_y_claves_sobreviven_nueva_instancia(client, database_url):
    from cotizacion.app import create_app
    from cotizacion.config import Config

    url, query, ofertas = preparar(client)
    clave = str(uuid4())
    primera = elegir(client, url, query, ofertas[0], 0, clave).get_json()
    segunda = elegir(client, url, query, ofertas[1], 1).get_json()
    nueva = create_app(Config(database_url=database_url))
    try:
        con_otra_conexion = nueva.test_client()
        assert elegir(con_otra_conexion, url, query, ofertas[0], 0, clave).get_json() == primera
        actual = con_otra_conexion.get(
            f"{url}/seleccion-provisional", query_string=query
        ).get_json()
        assert actual["seleccion"] == segunda
        tercera = elegir(con_otra_conexion, url, query, ofertas[0], 2).get_json()
        assert tercera["revision"] == 3 and tercera["ofertaId"] == primera["ofertaId"]
        assert contar(client, SeleccionProvisionalModel, SeleccionProvisionalModel.activa) == 1
    finally:
        nueva.extensions["repositorio_cotizaciones"]._sesiones.kw["bind"].dispose()


def test_clave_compartida_no_puede_mutar_otra_operacion_del_cliente(client, monkeypatch):
    url1, query, ofertas1 = preparar(client)
    url2, _, ofertas2 = preparar(client, query["clienteId"])
    barrera = Barrier(2)
    bloquear = repositorio_cotizaciones._bloquear_seleccion

    def sincronizar(*args):
        barrera.wait(timeout=10)
        return bloquear(*args)

    monkeypatch.setattr(repositorio_cotizaciones, "_bloquear_seleccion", sincronizar)
    clave = str(uuid4())

    def ejecutar(url, oferta):
        with client.application.test_client() as paralelo:
            return elegir(paralelo, url, query, oferta, 0, clave)

    with ThreadPoolExecutor(max_workers=2) as ejecutor:
        a = ejecutor.submit(ejecutar, url1, ofertas1[0])
        b = ejecutor.submit(ejecutar, url2, ofertas2[0])
        respuestas = [a.result(timeout=15), b.result(timeout=15)]
    assert sorted(res.status_code for res in respuestas) == [200, 409]
    assert (
        next(res for res in respuestas if res.status_code == 409).get_json()["error"]
        == "conflicto_idempotencia"
    )
    assert contar(client, SeleccionProvisionalModel) == 1


def test_la_misma_clave_de_otro_cliente_no_se_filtra_ni_colisiona(client):
    url1, query1, ofertas1 = preparar(client)
    url2, query2, ofertas2 = preparar(client)
    clave = str(uuid4())
    assert elegir(client, url1, query1, ofertas1[0], 0, clave).status_code == 200
    assert elegir(client, url2, query2, ofertas2[0], 0, clave).status_code == 200
    assert contar(client, SeleccionProvisionalModel) == 2


def test_legacy_terminal_permanece_inalterada_sin_preseleccion(client):
    url, query, ofertas = preparar(client)
    clave = str(uuid4())
    payload = {"ofertaId": ofertas[0]["id"], "version": 1}
    aceptada = client.put(
        f"{url}/seleccion",
        query_string=query,
        json=payload,
        headers={"Idempotency-Key": clave},
    )
    assert aceptada.status_code == 200
    for oferta in ofertas[:2]:
        assert elegir(client, url, query, oferta, 0).status_code == 409
    assert (
        client.put(
            f"{url}/seleccion",
            query_string=query,
            json=payload,
            headers={"Idempotency-Key": clave},
        ).get_json()
        == aceptada.get_json()
    )
    estado = client.get(f"{url}/seleccion-provisional", query_string=query).get_json()
    assert estado["seleccion"] is None
    assert estado["aceptacion"] == aceptada.get_json()
    assert contar(client, SeleccionProvisionalModel) == 0


def test_rollback_conserva_ultima_activa_y_no_reserva_clave_fallida(client):
    url, query, ofertas = preparar(client)
    primera = elegir(client, url, query, ofertas[0], 0).get_json()
    motor = client.application.extensions["repositorio_cotizaciones"]._sesiones.kw["bind"]
    clave = str(uuid4())

    def fallar(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO selecciones_provisionales"):
            raise RuntimeError("fallo_controlado_antes_de_commit")

    event.listen(motor, "before_cursor_execute", fallar)
    try:
        assert elegir(client, url, query, ofertas[1], 1, clave).status_code == 500
    finally:
        event.remove(motor, "before_cursor_execute", fallar)
    estado = client.get(f"{url}/seleccion-provisional", query_string=query).get_json()
    assert estado["seleccion"] == primera
    assert contar(client, SeleccionProvisionalModel) == 1
    assert elegir(client, url, query, ofertas[1], 1, clave).status_code == 200


@pytest.mark.parametrize("escenario", ["cambios", "replay", "aceptacion", "legacy"])
def test_concurrencia_sql_serializa_seleccion_y_aceptacion(client, monkeypatch, escenario):
    url, query, ofertas = preparar(client)
    if escenario != "legacy":
        assert elegir(client, url, query, ofertas[0], 0).status_code == 200
    barrera = Barrier(2)
    bloquear = repositorio_cotizaciones._bloquear_seleccion

    def sincronizar(*args):
        barrera.wait(timeout=10)
        return bloquear(*args)

    monkeypatch.setattr(repositorio_cotizaciones, "_bloquear_seleccion", sincronizar)
    clave = str(uuid4())

    def ejecutar(indice):
        with client.application.test_client() as paralelo:
            if escenario == "legacy" or (escenario == "aceptacion" and indice == 0):
                payload = {"ofertaId": ofertas[indice]["id"], "version": 1}
                if escenario == "aceptacion":
                    payload["revisionSeleccion"] = 1
                return paralelo.put(
                    f"{url}/seleccion",
                    query_string=query,
                    json=payload,
                    headers={"Idempotency-Key": str(uuid4())},
                )
            oferta = ofertas[1] if escenario == "replay" else ofertas[indice + 1]
            return elegir(paralelo, url, query, oferta, 1, clave if escenario == "replay" else None)

    with ThreadPoolExecutor(max_workers=2) as ejecutor:
        futures = [ejecutor.submit(ejecutar, indice) for indice in range(2)]
        respuestas = [future.result(timeout=15) for future in futures]
    esperados = (
        [200, 200] if escenario == "replay" else [200, 422 if escenario == "legacy" else 409]
    )
    assert sorted(res.status_code for res in respuestas) == esperados
    if escenario == "replay":
        assert respuestas[0].get_json() == respuestas[1].get_json()
    assert contar(client, SeleccionProvisionalModel, SeleccionProvisionalModel.activa) <= 1
    assert contar(client, SeleccionOfertaModel) <= 1
    assert contar(client, SuscripcionModel) == 0
    if escenario == "aceptacion":
        estado = client.get(f"{url}/seleccion-provisional", query_string=query).get_json()
        if estado["aceptacion"]:
            assert estado["aceptacion"]["ofertaId"] == ofertas[0]["id"]
            assert estado["seleccion"]["revision"] == 1
        else:
            assert estado["seleccion"]["revision"] == 2
