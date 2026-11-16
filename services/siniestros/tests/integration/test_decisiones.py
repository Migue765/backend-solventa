from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from flask.testing import FlaskClient
from siniestros.app import create_app
from siniestros.domain import Config
from siniestros.infrastructure.decisiones import (
    AsignacionSiniestroModel,
    DecisionSiniestroModel,
    ServicioDecisiones,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Audit, Claim
from siniestros.repository import fingerprint
from sqlalchemy import func, select
from sqlalchemy.orm import Session

TOKEN = "siniestros-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migraciones = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    migraciones.set_main_option("sqlalchemy.url", url)
    command.upgrade(migraciones, "head")
    return create_app(Config(url, tmp_path / "evidencias", TOKEN, "http://polizas.test"))


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def registrar(app: Flask, estados: tuple[str, ...] = ("reportado",)) -> UUID:
    siniestro_id = uuid4()
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (REPORTE - timedelta(days=1)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Lugar de prueba",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=str(siniestro_id),
                owner=str(uuid4()),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=REPORTE.isoformat(),
                estado=estados[-1],
                actualizado_en=REPORTE + timedelta(hours=len(estados) - 1),
            )
        )
        session.flush()
        session.add_all(
            CambioEstadoModel(
                siniestro_id=str(siniestro_id),
                secuencia=indice + 1,
                estado=estado,
                ocurrido_en=REPORTE + timedelta(hours=indice),
                visible_cliente=True,
            )
            for indice, estado in enumerate(estados)
        )
    return siniestro_id


def evaluacion(**cambios: object) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "evaluacionId": str(uuid4()),
        "evaluadoEn": "2026-11-10T12:00:00Z",
        "elegible": True,
        "requiereInspeccion": False,
        "fraude": {"nivel": "bajo", "bloqueante": False},
        "reglas": [{"reglaId": "cobertura-vigente", "version": 3, "resultado": "cumple"}],
        "motivo": "Evaluacion automatica sin hallazgos.",
    }
    datos.update(cambios)
    return datos


def decidir(client: FlaskClient, siniestro_id: UUID, datos: dict[str, Any]) -> Any:
    return client.post(
        f"/internal/v1/siniestros/{siniestro_id}/decisiones/automatica",
        headers=HEADERS,
        json=datos,
    )


def revisar(client: FlaskClient, siniestro_id: UUID, datos: dict[str, Any], clave: str) -> Any:
    return client.post(
        f"/internal/v1/siniestros/{siniestro_id}/decisiones/revision",
        headers={**HEADERS, "Idempotency-Key": clave},
        json=datos,
    )


def historial(app: Flask, siniestro_id: UUID) -> list[str]:
    with Session(app.extensions["siniestros_engine"]) as session:
        return list(
            session.scalars(
                select(CambioEstadoModel.estado)
                .where(CambioEstadoModel.siniestro_id == str(siniestro_id))
                .order_by(CambioEstadoModel.secuencia)
            )
        )


def contar(app: Flask, modelo: type[Any]) -> int:
    with Session(app.extensions["siniestros_engine"]) as session:
        return session.scalar(select(func.count()).select_from(modelo)) or 0


def test_aprobacion_automatica_registra_transiciones_y_auditoria(
    app: Flask, client: FlaskClient
) -> None:
    siniestro_id = registrar(app)

    respuesta = decidir(client, siniestro_id, evaluacion())

    assert respuesta.status_code == 201
    cuerpo = respuesta.get_json()
    assert cuerpo["resultado"] == "aprobado"
    assert (cuerpo["estadoAnterior"], cuerpo["estadoNuevo"]) == ("reportado", "aprobado")
    assert [(paso["de"], paso["a"]) for paso in cuerpo["transiciones"]] == [
        ("reportado", "en_evaluacion"),
        ("en_evaluacion", "aprobado"),
    ]
    assert cuerpo["actor"] == {"tipo": "sistema", "id": None}
    assert cuerpo["origen"] == "automatica"
    assert cuerpo["reglas"]["politica"] == "decision-siniestros"
    assert cuerpo["reglas"]["aplicada"] == "aprobacion_automatica"
    assert cuerpo["reglas"]["evaluacion"][0]["reglaId"] == "cobertura-vigente"
    assert cuerpo["motivo"] and cuerpo["decididaEn"].endswith("Z")
    assert cuerpo["asignacion"] is None
    assert historial(app, siniestro_id) == ["reportado", "en_evaluacion", "aprobado"]
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(Audit.result).where(Audit.action == "decidir_siniestro")) == (
            "aprobado"
        )


def test_estado_consultable_por_el_cliente_tras_la_decision(
    app: Flask, client: FlaskClient
) -> None:
    siniestro_id = registrar(app, ("reportado", "en_evaluacion"))
    decidir(client, siniestro_id, evaluacion(requiereInspeccion=True))
    with Session(app.extensions["siniestros_engine"]) as session:
        owner = session.get(Claim, str(siniestro_id))
        assert owner is not None
        cliente = owner.owner

    estado = client.get(
        f"/v1/siniestros/{siniestro_id}/estado",
        headers={**HEADERS, "X-Cliente-ID": cliente},
    )

    assert estado.status_code == 200
    assert estado.get_json()["estado"] == "en_inspeccion"
    assert [paso["estado"] for paso in estado.get_json()["historial"]] == [
        "reportado",
        "en_evaluacion",
        "en_inspeccion",
    ]


def test_inspeccion_se_asigna_a_perito_y_aparece_en_su_bandeja(
    app: Flask, client: FlaskClient
) -> None:
    siniestro_id = registrar(app, ("reportado", "en_evaluacion"))
    perito = uuid4()

    cuerpo = decidir(
        client, siniestro_id, evaluacion(requiereInspeccion=True, peritoId=str(perito))
    ).get_json()

    assert cuerpo["estadoNuevo"] == "en_inspeccion"
    assert cuerpo["asignacion"]["tipo"] == "perito"
    assert cuerpo["asignacion"]["destino"] == str(perito)
    bandeja = client.get(f"/internal/v1/peritos/{perito}/asignaciones", headers=HEADERS)
    assert bandeja.status_code == 200
    assert [a["siniestroId"] for a in bandeja.get_json()["asignaciones"]] == [str(siniestro_id)]
    assert bandeja.get_json()["asignaciones"][0]["estadoSiniestro"] == "en_inspeccion"


def test_fraude_bloqueante_no_aprueba_y_va_a_cola_de_fraude(
    app: Flask, client: FlaskClient
) -> None:
    siniestro_id = registrar(app)

    cuerpo = decidir(
        client, siniestro_id, evaluacion(fraude={"nivel": "alto", "bloqueante": True})
    ).get_json()

    assert cuerpo["resultado"] == "bloqueado_fraude"
    assert cuerpo["estadoNuevo"] == "en_revision"
    assert "aprobado" not in historial(app, siniestro_id)
    cola = client.get("/internal/v1/colas-revision/fraude/asignaciones", headers=HEADERS)
    assert [a["siniestroId"] for a in cola.get_json()["asignaciones"]] == [str(siniestro_id)]


def test_reintento_no_duplica_aprobacion_ni_asignacion(app: Flask, client: FlaskClient) -> None:
    siniestro_id = registrar(app)
    datos = evaluacion(requiereInspeccion=True)

    primera = decidir(client, siniestro_id, datos)
    segunda = decidir(client, siniestro_id, datos)

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert primera.get_json() == segunda.get_json()
    assert contar(app, DecisionSiniestroModel) == 1
    assert contar(app, AsignacionSiniestroModel) == 1
    assert historial(app, siniestro_id) == ["reportado", "en_evaluacion", "en_inspeccion"]
    conflicto = decidir(client, siniestro_id, {**datos, "motivo": "Otra evaluacion distinta."})
    assert conflicto.status_code == 409
    assert conflicto.get_json()["code"] == "idempotencia_conflictiva"


def test_nueva_evaluacion_sobre_caso_decidido_se_rechaza(app: Flask, client: FlaskClient) -> None:
    siniestro_id = registrar(app)
    assert decidir(client, siniestro_id, evaluacion()).status_code == 201

    respuesta = decidir(client, siniestro_id, evaluacion())

    assert respuesta.status_code == 409
    assert respuesta.get_json()["code"] == "transicion_no_permitida"
    assert contar(app, DecisionSiniestroModel) == 1


@pytest.mark.parametrize(
    "estados",
    [("reportado",), ("reportado", "en_evaluacion"), ("reportado", "en_evaluacion", "aprobado")],
)
def test_revision_humana_no_omite_transiciones_obligatorias(
    app: Flask, client: FlaskClient, estados: tuple[str, ...]
) -> None:
    siniestro_id = registrar(app, estados)

    respuesta = revisar(
        client,
        siniestro_id,
        {"revisorId": str(uuid4()), "resultado": "aprobado", "motivo": "Intento de atajo."},
        str(uuid4()),
    )

    assert respuesta.status_code == 409
    assert respuesta.get_json()["code"] == "transicion_no_permitida"
    assert historial(app, siniestro_id) == list(estados)


def test_revision_humana_resuelve_y_cierra_la_asignacion(app: Flask, client: FlaskClient) -> None:
    siniestro_id = registrar(app)
    decidir(client, siniestro_id, evaluacion(fraude={"nivel": "medio", "bloqueante": False}))
    revisor = uuid4()
    clave = str(uuid4())
    datos = {
        "revisorId": str(revisor),
        "resultado": "aprobado",
        "motivo": "Soportes verificados por el analista.",
    }

    primera = revisar(client, siniestro_id, datos, clave)
    repetida = revisar(client, siniestro_id, datos, clave)

    assert (primera.status_code, repetida.status_code) == (201, 200)
    cuerpo = primera.get_json()
    assert cuerpo["actor"] == {"tipo": "revisor", "id": str(revisor)}
    assert cuerpo["origen"] == "humana"
    assert (cuerpo["estadoAnterior"], cuerpo["estadoNuevo"]) == ("en_revision", "aprobado")
    assert cuerpo["motivo"] == datos["motivo"]
    consulta = client.get(f"/internal/v1/siniestros/{siniestro_id}/decisiones", headers=HEADERS)
    resumen = consulta.get_json()
    assert resumen["estado"] == "aprobado"
    assert resumen["asignacionActiva"] is None
    assert [d["resultado"] for d in resumen["decisiones"]] == ["revision", "aprobado"]
    assert resumen["decisiones"][0]["asignacion"]["estado"] == "cerrada"
    cola = client.get("/internal/v1/colas-revision/revision/asignaciones", headers=HEADERS)
    assert cola.get_json()["asignaciones"] == []
    assert historial(app, siniestro_id) == [
        "reportado",
        "en_evaluacion",
        "en_revision",
        "aprobado",
    ]


def test_revision_reenruta_a_inspeccion_sin_duplicar_asignacion_activa(
    app: Flask, client: FlaskClient
) -> None:
    siniestro_id = registrar(app)
    decidir(client, siniestro_id, evaluacion(elegible=False))

    respuesta = revisar(
        client,
        siniestro_id,
        {
            "revisorId": str(uuid4()),
            "resultado": "inspeccion",
            "motivo": "Se requiere visita tecnica.",
        },
        str(uuid4()),
    )

    assert respuesta.status_code == 201
    resumen = client.get(
        f"/internal/v1/siniestros/{siniestro_id}/decisiones", headers=HEADERS
    ).get_json()
    assert resumen["asignacionActiva"]["destino"] == "inspeccion"
    with Session(app.extensions["siniestros_engine"]) as session:
        activas = session.scalar(
            select(func.count())
            .select_from(AsignacionSiniestroModel)
            .where(AsignacionSiniestroModel.estado == "activa")
        )
    assert activas == 1


def test_revision_exige_clave_de_idempotencia(app: Flask, client: FlaskClient) -> None:
    siniestro_id = registrar(app, ("reportado", "en_evaluacion", "en_revision"))
    respuesta = client.post(
        f"/internal/v1/siniestros/{siniestro_id}/decisiones/revision",
        headers=HEADERS,
        json={"revisorId": str(uuid4()), "resultado": "rechazado", "motivo": "Sin cobertura."},
    )
    assert respuesta.status_code == 400


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", "/internal/v1/siniestros/{id}/decisiones/automatica"),
        ("get", "/internal/v1/siniestros/{id}/decisiones"),
        ("get", "/internal/v1/colas-revision/revision/asignaciones"),
    ],
)
def test_operaciones_de_decision_exigen_credencial_interna(
    client: FlaskClient, metodo: str, ruta: str
) -> None:
    respuesta = getattr(client, metodo)(ruta.format(id=uuid4()), json=evaluacion())
    assert respuesta.status_code == 401


def test_errores_de_entrada_y_recursos(client: FlaskClient) -> None:
    assert decidir(client, uuid4(), evaluacion()).status_code == 404
    assert decidir(client, uuid4(), {"elegible": True}).status_code == 400
    assert (
        client.get("/internal/v1/colas-revision/otra/asignaciones", headers=HEADERS).status_code
        == 404
    )
    assert (
        client.get(f"/internal/v1/siniestros/{uuid4()}/decisiones", headers=HEADERS).status_code
        == 404
    )


def test_servicio_en_proceso_para_la_evaluacion_automatica(app: Flask) -> None:
    from siniestros.domain.decision import ResultadoEvaluacion

    servicio: ServicioDecisiones = app.extensions["decisiones_siniestro_service"]
    siniestro_id = registrar(app, ("reportado", "en_evaluacion"))

    registro, status = servicio.decidir_automaticamente(
        siniestro_id,
        ResultadoEvaluacion.model_validate(evaluacion(requiereRevisionHumana=True)),
        str(uuid4()),
    )

    assert status == 201
    assert registro["estadoNuevo"] == "en_revision"
    assert registro["asignacion"]["destino"] == "revision"
