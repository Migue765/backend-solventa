from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    InstantaneaDecisionModel,
    OfertaSocioModel,
    OutboxEventoModel,
)
from cotizacion.infrastructure.persistence.repositorio_ofertas_socios import (
    RepositorioOfertasSociosSql,
)
from sqlalchemy import func, select, update

from .test_operaciones_hipotecarias_api import INTERNAL_TOKEN, ProfilingServiceStub
from .test_operaciones_socios_api import PATH, app_for, payload, post

AUTH = {"Authorization": "Bearer " + INTERNAL_TOKEN}


class CountingProfiling(ProfilingServiceStub):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def __call__(self, request, timeout):
        if request.data is None:
            self.reads += 1
        return super().__call__(request, timeout)


def oferta_path(body: dict, socio: str = "BANCO-001") -> str:
    return f"{PATH}/{body['operationId']}/oferta?socioId={socio}"


def operacion(database_url, monkeypatch, **cambios):
    profiling = CountingProfiling()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {**payload(), **cambios}
    assert post(app, body).status_code == 201
    return app, body, profiling


def conteo(database_url, modelo) -> int:
    with crear_sesiones(database_url, 1, 0)() as sesion:
        return sesion.scalar(select(func.count()).select_from(modelo))


def test_calcula_explica_persiste_y_recupera_la_misma_oferta(database_url, monkeypatch):
    app, body, profiling = operacion(database_url, monkeypatch)
    lecturas_previas = profiling.reads

    primera = app.test_client().post(oferta_path(body), headers=AUTH)

    assert primera.status_code == 201, primera.get_json()
    assert primera.headers["Cache-Control"] == "no-store"
    oferta = primera.get_json()
    assert oferta["operationId"] == body["operationId"]
    assert oferta["estado"] == "vigente"
    assert oferta["decision"]["reglaVersion"] == "academico-v1"
    assert oferta["riesgo"]["score"] == "0.35"
    assert oferta["riesgo"]["nivel"] == "A"
    assert oferta["riesgo"]["consentimientoId"] == body["consentimientoId"]
    assert {f["nombre"] for f in oferta["fuentes"]} == {"open_finance", "open_data"}
    assert oferta["variables"]["baseAsegurada"] == "25000000"
    assert [o["recomendada"] for o in oferta["ofertas"]] == [True, False, False]
    esencial = oferta["ofertas"][0]
    # 25.000.000 * 0.0015 * (1 + 0.20 * 0.35) * 1.00 = 40.125 COP: el score se aplica una vez.
    assert esencial["prima"] == {"monto": "40125.00", "moneda": "COP"}
    assert {f["codigo"]: f["valor"] for f in esencial["factores"]}["ajuste_riesgo"] == "1.07"
    assert esencial["coberturas"] and esencial["condiciones"]
    assert profiling.reads == lecturas_previas + 1

    reiniciada = app_for(database_url)
    reintento = reiniciada.test_client().post(oferta_path(body), headers=AUTH)
    consulta = reiniciada.test_client().get(oferta_path(body), headers=AUTH)

    assert reintento.status_code == 201
    assert reintento.get_json() == oferta
    assert consulta.get_json() == oferta
    assert profiling.reads == lecturas_previas + 1
    assert conteo(database_url, OfertaSocioModel) == 1
    with crear_sesiones(database_url, 1, 0)() as sesion:
        eventos = sesion.scalars(
            select(OutboxEventoModel.tipo).where(OutboxEventoModel.tipo == "OfertaSocioRegistrada")
        ).all()
    assert eventos == ["OfertaSocioRegistrada"]


def test_oferta_climatica_explica_su_propio_modelo(database_url, monkeypatch):
    app, body, _ = operacion(
        database_url,
        monkeypatch,
        productoId="parametric-weather",
        datosProducto={
            "location": "Bogota",
            "weatherEvent": "heavy-rain",
            "startDate": "2030-01-01",
            "endDate": "2030-01-10",
            "insuredAmount": "10000000",
        },
    )

    respuesta = app.test_client().post(oferta_path(body), headers=AUTH)

    assert respuesta.status_code == 201, respuesta.get_json()
    oferta = respuesta.get_json()
    assert oferta["decision"]["modelo"] == "clima-v1"
    assert len(oferta["ofertas"]) == 3
    factores = {f["codigo"]: f["valor"] for f in oferta["ofertas"][0]["factores"]}
    assert factores["tasa_diaria"] == "0.0001"
    assert factores["ajuste_riesgo"] == "1.00"


def test_operacion_ajena_o_inexistente_y_oferta_pendiente(database_url, monkeypatch):
    app, body, _ = operacion(database_url, monkeypatch)
    cliente = app.test_client()

    assert cliente.get(oferta_path(body), headers=AUTH).get_json()["error"] == (
        "oferta_no_encontrada"
    )
    ajena = cliente.post(oferta_path(body, socio="OTHER"), headers=AUTH)
    assert ajena.status_code == 404
    assert ajena.get_json()["error"] == "operacion_no_encontrada"
    assert cliente.post(oferta_path(body), headers={}).status_code == 401
    assert cliente.post(f"{PATH}/{body['operationId']}/oferta", headers=AUTH).status_code == 400
    assert conteo(database_url, OfertaSocioModel) == 0


def test_cotizacion_vencida_no_produce_oferta(database_url, monkeypatch):
    app, body, _ = operacion(database_url, monkeypatch)
    with crear_sesiones(database_url, 1, 0).begin() as sesion:
        sesion.execute(
            update(CotizacionModel).values(valida_hasta=datetime.now(UTC) - timedelta(minutes=1))
        )

    respuesta = app.test_client().post(oferta_path(body), headers=AUTH)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "cotizacion_vencida"
    assert conteo(database_url, OfertaSocioModel) == 0


def test_instantanea_alterada_no_produce_oferta(database_url, monkeypatch):
    app, body, _ = operacion(database_url, monkeypatch)
    with crear_sesiones(database_url, 1, 0).begin() as sesion:
        instantanea = sesion.scalars(select(InstantaneaDecisionModel)).one()
        contenido = dict(instantanea.contenido)
        contenido["regla"] = {**contenido["regla"], "tasa": "0.0001"}
        instantanea.contenido = contenido

    respuesta = app.test_client().post(oferta_path(body), headers=AUTH)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "decision_inconsistente"
    assert conteo(database_url, OfertaSocioModel) == 0


def test_perfil_vencido_no_produce_oferta(database_url, monkeypatch):
    app, body, profiling = operacion(database_url, monkeypatch)
    for perfil in profiling.profiles.values():
        perfil["vigenteHasta"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    respuesta = app.test_client().post(oferta_path(body), headers=AUTH)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "perfil_vencido"
    assert conteo(database_url, OfertaSocioModel) == 0


def test_fuente_vencida_no_produce_oferta(database_url, monkeypatch):
    app, body, profiling = operacion(database_url, monkeypatch)
    for perfil in profiling.profiles.values():
        perfil["fuentes"][0]["vigenteHasta"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()

    respuesta = app.test_client().post(oferta_path(body), headers=AUTH)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "fuente_vencida"
    assert conteo(database_url, OfertaSocioModel) == 0


def test_guardado_concurrente_devuelve_la_oferta_ya_confirmada(database_url, monkeypatch):
    app, body, _ = operacion(database_url, monkeypatch)
    oferta = app.test_client().post(oferta_path(body), headers=AUTH).get_json()
    repositorio = RepositorioOfertasSociosSql(crear_sesiones(database_url, 1, 0))
    guardada = repositorio.buscar("BANCO-001", UUID(body["operationId"]))
    assert guardada is not None

    duplicada = replace(guardada, calculada_en=guardada.calculada_en + timedelta(seconds=5))
    resultado = repositorio.guardar(duplicada)

    assert resultado.hash_contenido == oferta["hashContenido"]
    assert resultado.calculada_en == guardada.calculada_en
    assert conteo(database_url, OfertaSocioModel) == 1
    assert Decimal(oferta["ofertas"][1]["prima"]["monto"]) > Decimal(
        oferta["ofertas"][0]["prima"]["monto"]
    )
