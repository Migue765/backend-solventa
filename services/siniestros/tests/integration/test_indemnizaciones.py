from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from siniestros import payment_worker
from siniestros.app import create_app
from siniestros.domain import Config
from siniestros.domain.indemnizaciones import EfectoProveedor, OrdenIndemnizacion
from siniestros.domain.pagos_parametricos import RespuestaPagoAmbigua, ResultadoProveedorPago
from siniestros.infrastructure.indemnizaciones import (
    ServicioIndemnizaciones,
    SimuladorIndemnizacion,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import (
    Audit,
    Claim,
    Indemnization,
    IndemnizationAttempt,
    IndemnizationEffect,
    NotificationOutbox,
)
from siniestros.repository import fingerprint
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

TOKEN = "siniestros-test-internal"
INTERNAL_HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
APROBADO = [
    ("reportado", timedelta(0), True),
    ("en_evaluacion", timedelta(hours=1), True),
    ("aprobado", timedelta(hours=3), True),
]


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migrations = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    return url


@pytest.fixture
def app(tmp_path: Path, database_url: str) -> Flask:
    return create_app(Config(database_url, tmp_path / "evidencias", TOKEN, "http://polizas.test"))


def registrar(
    app: Flask,
    cambios: list[tuple[str, timedelta, bool]] = APROBADO,
    tipo: str | None = "asistido",
) -> tuple[UUID, UUID, UUID]:
    siniestro_id, cliente_id, poliza_id = uuid4(), uuid4(), uuid4()
    payload = {
        "polizaId": str(poliza_id),
        "fechaEvento": (REPORTE - timedelta(days=1)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Lugar de prueba",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=str(siniestro_id),
                owner=str(cliente_id),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=REPORTE.isoformat(),
                tipo=tipo,
                estado=cambios[-1][0],
                actualizado_en=REPORTE + cambios[-1][1],
            )
        )
        session.flush()
        session.add_all(
            CambioEstadoModel(
                siniestro_id=str(siniestro_id),
                secuencia=secuencia,
                estado=estado,
                ocurrido_en=REPORTE + desfase,
                visible_cliente=visible,
            )
            for secuencia, (estado, desfase, visible) in enumerate(cambios, start=1)
        )
    return siniestro_id, cliente_id, poliza_id


def solicitud(beneficiario: UUID | None = None, monto: str = "1500000.00") -> dict[str, object]:
    return {
        "beneficiarioId": str(beneficiario or uuid4()),
        "indemnizacion": {"monto": monto, "moneda": "COP"},
    }


def solicitar(
    app: Flask,
    siniestro_id: UUID,
    payload: dict[str, object],
    operacion: UUID | None = None,
) -> TestResponse:
    return app.test_client().post(
        f"/internal/v1/siniestros/{siniestro_id}/indemnizacion",
        headers={**INTERNAL_HEADERS, "Idempotency-Key": str(operacion or uuid4())},
        json=payload,
    )


def procesar(app: Flask, ruta: str = "procesamiento") -> TestResponse:
    return app.test_client().post(
        f"/internal/v1/indemnizaciones/{ruta}",
        headers=INTERNAL_HEADERS,
        json={"limite": 10},
    )


def detalle(app: Flask, siniestro_id: UUID) -> TestResponse:
    return app.test_client().get(
        f"/internal/v1/siniestros/{siniestro_id}/indemnizacion",
        headers=INTERNAL_HEADERS,
    )


def conteo(app: Flask, modelo: type) -> int:
    with Session(app.extensions["siniestros_engine"]) as session:
        return int(session.scalar(select(func.count()).select_from(modelo)) or 0)


def test_indemniza_siniestro_aprobado_y_asocia_resultado_auditable(app: Flask) -> None:
    siniestro_id, cliente_id, poliza_id = registrar(app)
    beneficiario = uuid4()
    operacion = uuid4()

    aceptada = solicitar(app, siniestro_id, solicitud(beneficiario), operacion)
    lote = procesar(app)
    consulta = detalle(app, siniestro_id)

    assert aceptada.status_code == 202
    assert aceptada.get_json()["estado"] == "pendiente"
    assert lote.status_code == 200
    assert lote.get_json() == {"confirmados": 1, "rechazados": 0, "pendientes": 0}
    assert consulta.status_code == 200
    resultado = consulta.get_json()
    assert resultado["indemnizacionId"] == aceptada.get_json()["indemnizacionId"]
    assert resultado["siniestroId"] == str(siniestro_id)
    assert resultado["operacionId"] == str(operacion)
    assert resultado["clienteId"] == str(cliente_id)
    assert resultado["polizaId"] == str(poliza_id)
    assert resultado["beneficiarioId"] == str(beneficiario)
    assert resultado["indemnizacion"] == {"monto": "1500000.00", "moneda": "COP"}
    assert resultado["estado"] == "confirmado"
    assert resultado["referencia"].startswith("SIM-")
    assert resultado["finalizadoEn"] is not None
    assert [
        (intento["numero"], intento["estado"], intento["referencia"])
        for intento in resultado["intentos"]
    ] == [(1, "confirmado", resultado["referencia"])]
    assert [(item["accion"], item["resultado"]) for item in resultado["auditoria"]] == [
        ("indemnizacion.solicitud", "aceptada"),
        ("indemnizacion.intento", "iniciado:1"),
        ("indemnizacion.resultado", "confirmado"),
    ]
    assert {item["correlationId"] for item in resultado["auditoria"]} == {
        aceptada.headers["X-Correlation-ID"]
    }

    estado = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}/estado",
        headers={**INTERNAL_HEADERS, "X-Cliente-ID": str(cliente_id)},
    )
    assert estado.status_code == 200
    assert estado.get_json()["estado"] == "pagado"
    assert estado.get_json()["historial"][-1]["estado"] == "pagado"
    with Session(app.extensions["siniestros_engine"]) as session:
        notificados = session.scalars(
            select(NotificationOutbox.estado).where(
                NotificationOutbox.recurso_id == str(siniestro_id)
            )
        ).all()
    assert "pagado" in notificados


def test_indemniza_siniestro_aprobado_por_la_decision_automatica(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app, [("reportado", timedelta(0), True)])
    decision = app.test_client().post(
        f"/internal/v1/siniestros/{siniestro_id}/decisiones/automatica",
        headers=INTERNAL_HEADERS,
        json={
            "evaluacionId": str(uuid4()),
            "evaluadoEn": "2026-11-10T12:00:00Z",
            "elegible": True,
            "requiereInspeccion": False,
            "fraude": {"nivel": "bajo", "bloqueante": False},
            "reglas": [{"reglaId": "cobertura-vigente", "version": 3, "resultado": "cumple"}],
            "motivo": "Evaluacion automatica sin hallazgos.",
        },
    )

    aceptada = solicitar(app, siniestro_id, solicitud())
    lote = procesar(app)

    assert decision.status_code == 201
    assert aceptada.status_code == 202
    assert lote.get_json() == {"confirmados": 1, "rechazados": 0, "pendientes": 0}
    assert detalle(app, siniestro_id).get_json()["estado"] == "confirmado"
    with Session(app.extensions["siniestros_engine"]) as session:
        estados = session.scalars(
            select(CambioEstadoModel.estado)
            .where(CambioEstadoModel.siniestro_id == str(siniestro_id))
            .order_by(CambioEstadoModel.secuencia)
        ).all()
    assert estados == ["reportado", "en_evaluacion", "aprobado", "pagado"]


@pytest.mark.parametrize(
    "cambios",
    [
        APROBADO[:1],
        [*APROBADO[:2], ("en_revision", timedelta(hours=2), True)],
        [*APROBADO[:2], ("rechazado", timedelta(hours=2), True)],
    ],
    ids=["reportado", "en_revision", "rechazado"],
)
def test_no_ordena_pago_de_siniestro_no_aprobado(
    app: Flask, cambios: list[tuple[str, timedelta, bool]]
) -> None:
    siniestro_id, _, _ = registrar(app, cambios)

    respuesta = solicitar(app, siniestro_id, solicitud())

    assert respuesta.status_code == 409
    assert respuesta.get_json()["code"] == "siniestro_no_aprobado"
    assert procesar(app).get_json() == {"confirmados": 0, "rechazados": 0, "pendientes": 0}
    assert conteo(app, Indemnization) == 0
    assert conteo(app, IndemnizationEffect) == 0
    with Session(app.extensions["siniestros_engine"]) as session:
        rechazo = session.scalar(select(Audit).where(Audit.result == "siniestro_no_aprobado"))
        assert rechazo is not None
        assert rechazo.action == "solicitar_indemnizacion"


def test_siniestro_inexistente_o_parametrico_no_ordena_pago(app: Flask) -> None:
    parametrico, _, _ = registrar(app, tipo="parametrico")

    inexistente = solicitar(app, uuid4(), solicitud())
    automatico = solicitar(app, parametrico, solicitud())

    assert inexistente.status_code == 404
    assert inexistente.get_json()["code"] == "siniestro_no_encontrado"
    assert automatico.status_code == 409
    assert automatico.get_json()["code"] == "pago_parametrico_automatico"
    assert conteo(app, Indemnization) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"indemnizacion": {"monto": "100.00", "moneda": "COP"}},
        solicitud(UUID(int=0)),
        {**solicitud(), "beneficiarioId": "no-es-uuid"},
        solicitud(monto="0.00"),
        solicitud(monto="-10.00"),
        solicitud(monto="10.001"),
        solicitud(monto="NaN"),
        {**solicitud(), "indemnizacion": {"monto": "10.00", "moneda": "cop"}},
        {**solicitud(), "indemnizacion": {"monto": "10.00"}},
        {**solicitud(), "cuenta": "123"},
    ],
    ids=[
        "sin-beneficiario",
        "beneficiario-nulo",
        "beneficiario-invalido",
        "monto-cero",
        "monto-negativo",
        "monto-tres-decimales",
        "monto-nan",
        "moneda-invalida",
        "sin-moneda",
        "campo-no-definido",
    ],
)
def test_beneficiario_o_monto_invalido_no_ordena_pago(
    app: Flask, payload: dict[str, object]
) -> None:
    siniestro_id, _, _ = registrar(app)

    respuesta = solicitar(app, siniestro_id, payload)

    assert respuesta.status_code == 400
    assert respuesta.get_json()["code"] == "solicitud_invalida"
    assert conteo(app, Indemnization) == 0


def test_solicitud_exige_credencial_interna_y_clave_idempotente(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    client = app.test_client()
    ruta = f"/internal/v1/siniestros/{siniestro_id}/indemnizacion"

    sin_credencial = client.post(ruta, headers={"Idempotency-Key": str(uuid4())}, json={})
    sin_clave = client.post(ruta, headers=INTERNAL_HEADERS, json=solicitud())
    sin_indemnizacion = detalle(app, siniestro_id)

    assert sin_credencial.status_code == 401
    assert sin_clave.status_code == 400
    assert sin_indemnizacion.status_code == 404
    assert sin_indemnizacion.get_json()["code"] == "indemnizacion_no_encontrada"
    assert conteo(app, Indemnization) == 0


def test_reintentos_con_la_misma_clave_no_duplican_desembolso(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    operacion = uuid4()
    beneficiario = uuid4()

    primera = solicitar(app, siniestro_id, solicitud(beneficiario, "250000"), operacion)
    repetida = solicitar(app, siniestro_id, solicitud(beneficiario, "250000.00"), operacion)
    otra_clave = solicitar(app, siniestro_id, solicitud(beneficiario))
    conflicto = solicitar(app, siniestro_id, solicitud(beneficiario, "1.00"), operacion)
    lotes = [procesar(app).get_json() for _ in range(3)]
    tras_pago = solicitar(app, siniestro_id, solicitud(beneficiario, "250000.00"), operacion)

    assert primera.status_code == 202
    assert repetida.status_code == 200
    assert repetida.get_json()["indemnizacionId"] == primera.get_json()["indemnizacionId"]
    assert otra_clave.status_code == 409
    assert otra_clave.get_json()["code"] == "indemnizacion_existente"
    assert conflicto.status_code == 409
    assert conflicto.get_json()["code"] == "idempotencia_conflictiva"
    assert lotes == [
        {"confirmados": 1, "rechazados": 0, "pendientes": 0},
        {"confirmados": 0, "rechazados": 0, "pendientes": 0},
        {"confirmados": 0, "rechazados": 0, "pendientes": 0},
    ]
    assert tras_pago.status_code == 200
    assert tras_pago.get_json()["estado"] == "confirmado"
    assert conteo(app, Indemnization) == 1
    assert conteo(app, IndemnizationAttempt) == 1
    assert conteo(app, IndemnizationEffect) == 1


def test_solicitudes_y_procesadores_concurrentes_no_duplican_desembolso(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    payload = solicitud(uuid4())
    operacion = uuid4()

    with ThreadPoolExecutor(max_workers=8) as executor:
        misma_clave = list(
            executor.map(lambda _: solicitar(app, siniestro_id, payload, operacion), range(4))
        )
        otras_claves = list(executor.map(lambda _: solicitar(app, siniestro_id, payload), range(4)))
        lotes = list(executor.map(lambda _: procesar(app).get_json(), range(4)))

    assert sorted(respuesta.status_code for respuesta in misma_clave) == [200, 200, 200, 202]
    assert {respuesta.status_code for respuesta in otras_claves} == {409}
    assert sum(lote["confirmados"] for lote in lotes) == 1
    assert conteo(app, Indemnization) == 1
    assert conteo(app, IndemnizationAttempt) == 1
    assert conteo(app, IndemnizationEffect) == 1


class AckPerdido:
    def __init__(self, simulado: SimuladorIndemnizacion) -> None:
        self.simulado = simulado
        self.ejecuciones = 0

    def consultar(self, indemnizacion_id: UUID, huella: str) -> EfectoProveedor | None:
        return self.simulado.consultar(indemnizacion_id, huella)

    def ejecutar(self, orden: OrdenIndemnizacion, huella: str) -> EfectoProveedor:
        self.ejecuciones += 1
        self.simulado.ejecutar(orden, huella)
        raise RespuestaPagoAmbigua("ack_perdido")


def test_concilia_respuesta_perdida_sin_repetir_desembolso(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    repository = app.extensions["siniestros_repository"]
    proveedor = AckPerdido(SimuladorIndemnizacion(repository))
    reloj = [datetime.now(UTC)]
    servicio = ServicioIndemnizaciones(repository, proveedor, lambda: reloj[0])

    assert servicio.procesar(10) == {"confirmados": 0, "rechazados": 0, "pendientes": 1}
    assert servicio.procesar(10) == {"confirmados": 0, "rechazados": 0, "pendientes": 0}
    reloj[0] += timedelta(seconds=3)
    assert servicio.procesar(10) == {"confirmados": 1, "rechazados": 0, "pendientes": 0}

    assert proveedor.ejecuciones == 1
    resultado = servicio.detalle(siniestro_id)
    assert resultado["estado"] == "confirmado"
    intentos = cast(list[dict[str, object]], resultado["intentos"])
    assert [(intento["estado"], intento["codigo"]) for intento in intentos] == [
        ("ambiguo", "ack_perdido"),
        ("confirmado", None),
    ]
    assert conteo(app, IndemnizationEffect) == 1
    with Session(app.extensions["siniestros_engine"]) as session:
        claim = session.get(Claim, str(siniestro_id))
        assert claim is not None and claim.estado == "pagado"


def test_proveedor_no_disponible_converge_sin_perder_la_operacion(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    repository = app.extensions["siniestros_repository"]
    simulado = SimuladorIndemnizacion(repository)
    caidas = [1]

    class Intermitente:
        def consultar(self, indemnizacion_id: UUID, huella: str) -> EfectoProveedor | None:
            if caidas[0]:
                caidas[0] -= 1
                raise TimeoutError("proveedor")
            return simulado.consultar(indemnizacion_id, huella)

        def ejecutar(self, orden: OrdenIndemnizacion, huella: str) -> EfectoProveedor:
            return simulado.ejecutar(orden, huella)

    reloj = [datetime.now(UTC)]
    servicio = ServicioIndemnizaciones(repository, Intermitente(), lambda: reloj[0])

    assert servicio.procesar(10)["pendientes"] == 1
    reloj[0] += timedelta(seconds=3)
    assert servicio.procesar(10)["confirmados"] == 1
    intentos = cast(list[dict[str, object]], servicio.detalle(siniestro_id)["intentos"])
    assert [intento["codigo"] for intento in intentos] == ["proveedor_no_disponible", None]


def test_intento_abandonado_queda_ambiguo_y_se_concilia(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    repository = app.extensions["siniestros_repository"]
    reloj = [datetime.now(UTC)]
    servicio = ServicioIndemnizaciones(repository, reloj=lambda: reloj[0])

    reserva = servicio._reservar()
    assert reserva is not None
    SimuladorIndemnizacion(repository).ejecutar(reserva[0], reserva[1])
    assert servicio.procesar(10)["confirmados"] == 0
    reloj[0] += timedelta(seconds=31)

    assert servicio.procesar(10) == {"confirmados": 1, "rechazados": 0, "pendientes": 0}
    intentos = cast(list[dict[str, object]], servicio.detalle(siniestro_id)["intentos"])
    assert [(intento["estado"], intento["codigo"]) for intento in intentos] == [
        ("ambiguo", "lease_vencido"),
        ("confirmado", None),
    ]
    assert conteo(app, IndemnizationEffect) == 1


def test_rechazo_definitivo_no_marca_pagado_ni_reintenta(app: Flask) -> None:
    siniestro_id, cliente_id, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    repository = app.extensions["siniestros_repository"]
    servicio = ServicioIndemnizaciones(
        repository, SimuladorIndemnizacion(repository, ResultadoProveedorPago.RECHAZADO)
    )

    assert servicio.procesar(10)["rechazados"] == 1
    assert procesar(app, "conciliacion").get_json() == {
        "confirmados": 0,
        "rechazados": 0,
        "pendientes": 0,
    }

    resultado = detalle(app, siniestro_id).get_json()
    assert resultado["estado"] == "rechazado"
    assert resultado["intentos"][0]["estado"] == "rechazado"
    assert resultado["auditoria"][-1]["resultado"] == "rechazado"
    estado = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}/estado",
        headers={**INTERNAL_HEADERS, "X-Cliente-ID": str(cliente_id)},
    )
    assert estado.get_json()["estado"] == "aprobado"


def test_confirmacion_tardia_se_conserva_si_el_siniestro_cambio(app: Flask) -> None:
    siniestro_id, _, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        claim = session.get(Claim, str(siniestro_id))
        assert claim is not None
        claim.estado = "cerrado"

    assert procesar(app).get_json()["confirmados"] == 1
    assert detalle(app, siniestro_id).get_json()["estado"] == "confirmado"
    with Session(app.extensions["siniestros_engine"]) as session:
        claim = session.get(Claim, str(siniestro_id))
        assert claim is not None and claim.estado == "cerrado"


def test_trabajador_de_pagos_procesa_indemnizaciones(
    app: Flask, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    siniestro_id, _, _ = registrar(app)
    assert solicitar(app, siniestro_id, solicitud()).status_code == 202
    monkeypatch.setenv("SINIESTROS_DATABASE_URL", database_url)
    monkeypatch.setenv("SINIESTROS_STORAGE_ROOT", str(tmp_path / "evidencias"))
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("POLIZAS_SERVICE_URL", "http://polizas.test")

    payment_worker.run(once=True, limit=10, idle_seconds=0.01)

    assert detalle(app, siniestro_id).get_json()["estado"] == "confirmado"


def test_contrato_interno_publica_indemnizacion() -> None:
    contract_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "openapi"
        / "indemnizaciones-internal-v1.yaml"
    )
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

    solicitud_contrato = contract["paths"]["/siniestros/{siniestroId}/indemnizacion"]["post"]
    assert {"202", "200"} <= set(solicitud_contrato["responses"])
    assert any(
        parametro.get("$ref", "").endswith("IdempotencyKey")
        for parametro in solicitud_contrato["parameters"]
    )
    assert "get" in contract["paths"]["/siniestros/{siniestroId}/indemnizacion"]
    assert "/indemnizaciones/procesamiento" in contract["paths"]
    assert "/indemnizaciones/conciliacion" in contract["paths"]
    esquema = contract["components"]["schemas"]["SolicitudIndemnizacion"]
    assert set(esquema["required"]) == {"beneficiarioId", "indemnizacion"}
    assert esquema["additionalProperties"] is False
