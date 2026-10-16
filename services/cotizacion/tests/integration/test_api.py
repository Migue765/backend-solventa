from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.perfil_simulado import ProveedorPerfilSimulado
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    InstantaneaDecisionModel,
    OutboxEventoModel,
    ReglaTarifaModel,
)
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from sqlalchemy import func, select, update

PRODUCTO_AUTOMOTRIZ = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(database_url):
    app = create_app(
        Config(database_url=database_url, vigencia_dias=15, internal_api_token="test-internal"),
        perfiles=ProveedorPerfilSimulado(Decimal("0.5")),
    )
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-internal"
    return client


def solicitud(**cambios):
    cuerpo = {
        "clienteId": str(uuid4()),
        "productoId": PRODUCTO_AUTOMOTRIZ,
        "perfilId": "perfil-1",
        "moneda": "COP",
        "coberturas": [
            {"coberturaId": str(uuid4()), "sumaAsegurada": "2000000", "deducible": "200000"}
        ],
    }
    cuerpo.update(cambios)
    return cuerpo


def contar(database_url, modelo):
    with crear_sesiones(database_url)() as sesion:
        return sesion.scalar(select(func.count()).select_from(modelo))


def test_ping(client):
    respuesta = client.get("/ping")

    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"status": "ok"}


def test_el_flujo_anterior_no_cambia_hasta_habilitar_hu11(database_url):
    client = create_app(Config(database_url=database_url)).test_client()
    respuesta = client.post("/v1/cotizaciones", json=solicitud())
    assert respuesta.status_code == 201
    assert respuesta.get_json()["decision"]["perfilId"] == "perfil-1"
    assert (
        client.get(
            "/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234/perfilamiento",
            query_string={"clienteId": str(uuid4())},
        ).status_code
        == 503
    )


def test_crear_cotizacion_la_persiste(client, database_url):
    cuerpo = solicitud()

    respuesta = client.post("/v1/cotizaciones", json=cuerpo)

    assert respuesta.status_code == 201
    datos = respuesta.get_json()
    assert datos["clienteId"] == cuerpo["clienteId"]
    assert datos["estado"] == "CALCULADA"
    # (2.000.000 - 200.000) * 0,015 * (1 + 0,5 * 0,10)
    assert datos["primaEstimada"] == {"monto": "28350.00", "moneda": "COP"}
    assert datos["decision"]["reglaVersion"] == "tarifa-2026.1"
    assert datos["decision"]["perfilId"] == "perfil-1"

    guardada = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url)).obtener(
        UUID(datos["cotizacionId"])
    )
    assert guardada is not None
    assert guardada.prima_estimada.monto == Decimal("28350.00")


def test_cuerpo_que_no_es_json(client):
    respuesta = client.post("/v1/cotizaciones", data="hola", content_type="text/plain")

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "solicitud_invalida"


def test_solicitud_invalida(client):
    respuesta = client.post("/v1/cotizaciones", json=solicitud(moneda="pesos"))

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "solicitud_invalida"


def test_regla_de_negocio_violada(client):
    cuerpo = solicitud(
        coberturas=[{"coberturaId": str(uuid4()), "sumaAsegurada": "100", "deducible": "500"}]
    )

    respuesta = client.post("/v1/cotizaciones", json=cuerpo)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "regla_de_negocio"


def test_datos_incompletos_no_crean_una_cotizacion_parcial(client, database_url):
    sin_perfil = solicitud()
    del sin_perfil["perfilId"]
    fuera_de_rango = solicitud(
        coberturas=[{"coberturaId": str(uuid4()), "sumaAsegurada": "-5", "deducible": "0"}]
    )

    respuestas = [
        client.post("/v1/cotizaciones", json=sin_perfil),
        client.post("/v1/cotizaciones", json=fuera_de_rango),
    ]

    assert [r.status_code for r in respuestas] == [400, 400]
    assert contar(database_url, CotizacionModel) == 0
    assert contar(database_url, InstantaneaDecisionModel) == 0
    assert contar(database_url, OutboxEventoModel) == 0


def test_producto_sin_regla_vigente_se_rechaza_sin_persistir(client, database_url):
    respuesta = client.post("/v1/cotizaciones", json=solicitud(productoId=str(uuid4())))

    assert respuesta.status_code == 422
    assert "reglas de tarificación vigentes" in respuesta.get_json()["mensaje"]
    assert contar(database_url, CotizacionModel) == 0


def test_regla_vencida_se_rechaza(database_url):
    with crear_sesiones(database_url).begin() as sesion:
        sesion.execute(
            update(ReglaTarifaModel).values(vigente_hasta=ReglaTarifaModel.vigente_desde)
        )
    client = create_app(
        Config(database_url=database_url, internal_api_token="test-internal"),
        perfiles=ProveedorPerfilSimulado(Decimal("0.5")),
    ).test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-internal"

    respuesta = client.post("/v1/cotizaciones", json=solicitud())

    assert respuesta.status_code == 422
    assert contar(database_url, CotizacionModel) == 0


def test_mismas_entradas_y_version_producen_el_mismo_resultado(client):
    cuerpo = solicitud()

    primera = client.post("/v1/cotizaciones", json=cuerpo).get_json()
    segunda = client.post("/v1/cotizaciones", json=cuerpo).get_json()

    assert primera["primaEstimada"] == segunda["primaEstimada"]
    assert primera["decision"]["hashDecision"] == segunda["decision"]["hashDecision"]
    assert primera["cotizacionId"] != segunda["cotizacionId"]


def test_un_calculo_exitoso_deja_trazabilidad(client, database_url):
    datos = client.post("/v1/cotizaciones", json=solicitud()).get_json()

    with crear_sesiones(database_url)() as sesion:
        instantanea = sesion.get(InstantaneaDecisionModel, UUID(datos["cotizacionId"]))
        evento = sesion.scalar(select(OutboxEventoModel))

    assert instantanea is not None
    assert instantanea.regla_version == "tarifa-2026.1"
    assert instantanea.perfil_modelo_version == "perfil-simulado-1"
    assert instantanea.hash_decision == datos["decision"]["hashDecision"]
    assert instantanea.contenido["regla"]["version"] == "tarifa-2026.1"
    assert evento.tipo == "InstantaneaDecisionRegistrada"
    assert evento.payload["hashDecision"] == instantanea.hash_decision
    assert evento.publicado_en is None


def test_propaga_el_correlation_id(client):
    respuesta = client.post(
        "/v1/cotizaciones",
        json=solicitud(moneda="pesos"),
        headers={"X-Correlation-Id": "corr-123"},
    )

    assert respuesta.headers["X-Correlation-Id"] == "corr-123"


def test_error_inesperado_devuelve_error_controlado(database_url):
    app = create_app(Config(database_url=database_url, internal_api_token="test-internal"))

    def fallar(*_args, **_kwargs):
        raise RuntimeError("falla simulada")

    app.extensions["crear_cotizacion"].ejecutar = fallar

    respuesta = app.test_client().post(
        "/v1/cotizaciones",
        json=solicitud(),
        headers={"Authorization": "Bearer test-internal"},
    )

    assert respuesta.status_code == 500
    cuerpo = respuesta.get_json()
    assert cuerpo["error"] == "error_interno"
    assert cuerpo["correlationId"] == respuesta.headers["X-Correlation-Id"]
    assert "falla simulada" not in respuesta.get_data(as_text=True)
