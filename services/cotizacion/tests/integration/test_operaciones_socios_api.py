import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.domain.errors import ErrorConsultaPerfil
from cotizacion.domain.productos import buscar_producto
from cotizacion.infrastructure.persistence.admision_socios import (
    AdmisionRechazada,
    AdmisionSocios,
    validar_reserva,
)
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    InstantaneaDecisionModel,
    OfertaHipotecariaModel,
    OperacionSocioModel,
)
from sqlalchemy import func, select

from .test_operaciones_hipotecarias_api import INTERNAL_TOKEN, ProfilingServiceStub

PATH = "/v1/socios/solicitudes-cotizacion"


def payload() -> dict:
    return {
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "usuarioId": str(uuid4()),
        "clienteId": str(uuid4()),
        "productoId": "automotive",
        "datosProducto": {"vehiclePlate": "ABC123"},
        "tipoCliente": "persona_natural",
        "tipoDocumento": "cc",
        "identificacion": "1023456789",
        "correo": "cliente@solventa.co",
        "telefono": "+573001234567",
        "consentimientoId": str(uuid4()),
        "consentimientoVersion": 1,
        "referenciaCredito": "HIP-2026-001",
    }


def post(app, body):
    return app.test_client().post(
        PATH,
        json=body,
        headers={
            "Authorization": "Bearer " + INTERNAL_TOKEN,
            "Idempotency-Key": body["operationId"],
        },
    )


def app_for(url: str, **kwargs):
    return create_app(
        Config(
            database_url=url,
            internal_api_token=INTERNAL_TOKEN,
            perfilamiento_habilitado=True,
            **kwargs,
        )
    )


@pytest.mark.parametrize(
    ("product", "data"),
    [
        ("automotive", {"vehiclePlate": "ABC123"}),
        ("life", {"birthDate": "1985-02-10"}),
        ("mortgage-life", {"birthDate": "1985-02-10", "mortgageBalance": "310000000"}),
        (
            "device-protection",
            {"deviceType": "telefono", "brandModel": "modelo", "purchaseValue": "1000000"},
        ),
        ("payment-protection", {"creditType": "personal", "monthlyPayment": "500000"}),
        (
            "travel",
            {
                "destinationCountryCode": "US",
                "departureDate": "2030-01-01",
                "returnDate": "2030-01-10",
            },
        ),
        ("parametric", {"airline": "Air", "flightNumber": "AB123", "flightDate": "2030-01-01"}),
        (
            "parametric-weather",
            {
                "location": "Bogota",
                "weatherEvent": "heavy-rain",
                "startDate": "2030-01-01",
                "endDate": "2030-01-10",
                "insuredAmount": "10000000",
            },
        ),
    ],
)
def test_catalog_products_persist_and_replay_across_instances(
    database_url, monkeypatch, product, data
):
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {**payload(), "productoId": product, "datosProducto": data}
    first = post(app, body)
    assert first.status_code == 201, first.get_json()
    restarted = app_for(database_url)
    retry = post(restarted, body)
    assert retry.get_json() == first.get_json()
    assert profiling.created == 1
    result = first.get_json()
    assert result["oferta"]["recomendada"] is True
    assert result["oferta"]["estado"] == "disponible"
    assert result["oferta"]["prima"]["moneda"] == "COP"
    get = restarted.test_client().get(
        f"{PATH}/{body['operationId']}?socioId=BANCO-001",
        headers={"Authorization": "Bearer " + INTERNAL_TOKEN},
    )
    assert get.get_json() == result
    foreign = restarted.test_client().get(
        f"{PATH}/{body['operationId']}?socioId=OTHER",
        headers={"Authorization": "Bearer " + INTERNAL_TOKEN},
    )
    assert foreign.status_code == 404
    collision = post(restarted, {**body, "socioId": "OTHER"})
    assert collision.status_code == 409
    assert profiling.created == 1
    with crear_sesiones(database_url)() as session:
        row = session.scalar(select(OperacionSocioModel))
        assert row.producto_id == product and str(row.cliente_id) == body["clienteId"]
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1
    changed = {**body, "telefono": "+573009999999"}
    assert post(app, changed).status_code == 409
    assert profiling.created == 1


def test_mortgage_offer_persists_explanation_and_complete_decision(database_url, monkeypatch):
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {
        **payload(),
        "productoId": "mortgage-life",
        "datosProducto": {
            "birthDate": "1985-02-10",
            "mortgageBalance": "310000000",
        },
    }

    first = post(app, body)

    assert first.status_code == 201
    offer = first.get_json()["oferta"]
    assert offer["prima"]["monto"] == "165850.00"
    assert offer["coberturaPrincipal"] == "Fallecimiento. Base asegurada: $310.000.000 COP"
    assert "prima mensual" in offer["explicacion"]["resumen"]
    assert [factor["nombre"] for factor in offer["explicacion"]["factoresDeterminantes"]] == [
        "Saldo hipotecario",
        "Perfil de riesgo autorizado",
        "Regla y plan",
    ]
    assert offer["trazabilidad"]["perfilVersion"] == 1
    assert offer["trazabilidad"]["modeloPerfilVersion"] == "perfil-riesgo-1"
    assert offer["trazabilidad"]["reglaVersion"] == "academico-v1"
    assert offer["trazabilidad"]["fuentes"] == [
        {"nombre": "open_finance", "version": "mock-1"},
        {"nombre": "open_data", "version": "mock-1"},
    ]

    with crear_sesiones(database_url)() as session:
        persisted = session.scalar(select(OfertaHipotecariaModel))
        snapshot = session.scalar(select(InstantaneaDecisionModel))
        assert persisted is not None and snapshot is not None
        assert persisted.operation_id == UUID(body["operationId"])
        assert persisted.contenido == offer
        assert persisted.evidencia_decision == snapshot.contenido
        assert persisted.hash_decision == snapshot.hash_decision
        assert (
            persisted.hash_oferta
            == hashlib.sha256(
                json.dumps(
                    offer,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
        )
        evidence = persisted.evidencia_decision
        assert evidence["tarificacion"]["datosProducto"] == body["datosProducto"]
        assert evidence["perfil"]["evidencia"]["fuentes"]
        assert evidence["perfil"]["evidencia"]["operationId"] == body["operationId"]
        assert evidence["perfil"]["evidencia"]["creditoId"] == body["referenciaCredito"]
        assert evidence["regla"]["version"] == "academico-v1"
        assert evidence["resultado"]["primaEstimada"] == offer["prima"]["monto"]

    restarted = app_for(database_url)
    monkeypatch.setattr(
        restarted.extensions["gestionar_ofertas"],
        "listar",
        lambda *_: (_ for _ in ()).throw(AssertionError("No recalcular la oferta persistida")),
    )
    recovered = restarted.test_client().get(
        f"{PATH}/{body['operationId']}?socioId=BANCO-001",
        headers={"Authorization": "Bearer " + INTERNAL_TOKEN},
    )
    assert recovered.status_code == 200
    assert recovered.get_json() == first.get_json()


@pytest.mark.parametrize("failure", ["missing-profile", "inactive-product"])
def test_mortgage_offer_failure_does_not_persist_partial_results(
    database_url, monkeypatch, failure
):
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {
        **payload(),
        "productoId": "mortgage-life",
        "datosProducto": {
            "birthDate": "1985-02-10",
            "mortgageBalance": "310000000",
        },
    }
    registrar = app.extensions["registrar_solicitud"]
    if failure == "missing-profile":
        monkeypatch.setattr(
            registrar._generador_perfiles,
            "generar",
            lambda *_args: (_ for _ in ()).throw(ErrorConsultaPerfil("perfil_no_encontrado", 404)),
        )
    else:
        product = buscar_producto("mortgage-life")
        assert product is not None
        monkeypatch.setattr(
            "cotizacion.application.registrar_solicitud.buscar_producto",
            lambda _: replace(product, disponible=False),
        )

    response = post(app, body)

    assert response.status_code == (404 if failure == "missing-profile" else 422)
    with crear_sesiones(database_url)() as session:
        for model in (CotizacionModel, OperacionSocioModel, OfertaHipotecariaModel):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_mortgage_offer_explains_profile_degradation(database_url, monkeypatch):
    profiling = ProfilingServiceStub()
    profiling.degradations = [
        {
            "fuente": "open_finance",
            "motivo": "fuente_no_disponible",
            "politica": "default",
            "versionPolitica": "degradacion-1",
        }
    ]
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {
        **payload(),
        "productoId": "mortgage-life",
        "datosProducto": {
            "birthDate": "1985-02-10",
            "mortgageBalance": "310000000",
        },
    }

    response = post(app, body)

    assert response.status_code == 201
    factors = response.get_json()["oferta"]["explicacion"]["factoresDeterminantes"]
    degradation = next(
        factor for factor in factors if factor["nombre"] == "Disponibilidad de datos"
    )
    assert degradation["valor"] == "open_finance: política default"
    assert "fuente_no_disponible" in degradation["impacto"]


def test_quota_override_is_persistent_and_does_not_block_other_partner(database_url, monkeypatch):
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    configured = app.test_cli_runner().invoke(
        args=[
            "configure-partner-limit",
            "--partner-id",
            "BANCO-001",
            "--per-minute",
            "1",
            "--concurrency",
            "1",
        ]
    )
    assert configured.exit_code == 0
    body = payload()
    assert post(app, body).status_code == 201
    second_app = app_for(database_url)
    assert post(second_app, body).status_code == 201
    denied = post(second_app, payload())
    assert denied.status_code == 429
    assert denied.get_json()["error"] == "cuota_socio_excedida"
    assert 1 <= int(denied.headers["Retry-After"]) <= 60
    assert post(second_app, {**payload(), "socioId": "BANCO-002"}).status_code == 201
    assert profiling.created == 2


def test_concurrent_duplicate_and_saturated_partner_do_not_block_other_partner(
    database_url, monkeypatch
):
    profiling = ProfilingServiceStub()
    entered, release = Event(), Event()
    first_body = payload()

    def slow(request, timeout):
        if request.data is not None and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return profiling(request, timeout)

    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", slow)
    first_app = app_for(database_url, socios_max_concurrentes=1)
    second_app = app_for(database_url, socios_max_concurrentes=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(post, first_app, first_body)
        try:
            assert entered.wait(5)
            duplicate = post(second_app, first_body)
            assert duplicate.status_code == 409
            assert duplicate.get_json()["error"] == "operacion_en_curso"
            collision = post(second_app, {**first_body, "socioId": "BANCO-002"})
            assert collision.status_code == 409
            saturated = post(second_app, payload())
            assert saturated.status_code == 429
            assert saturated.get_json()["error"] == "socio_ocupado"
            independent = post(second_app, {**payload(), "socioId": "BANCO-002"})
            assert independent.status_code == 201
        finally:
            release.set()
        completed = pending.result(timeout=5)
    assert completed.status_code == 201
    assert post(second_app, first_body).get_json() == completed.get_json()
    assert profiling.created == 2


def test_failed_attempt_can_retry_but_cannot_change_body(database_url, monkeypatch):
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = app_for(database_url)
    body = {
        **payload(),
        "productoId": "mortgage-life",
        "datosProducto": {
            "birthDate": "1985-02-10",
            "mortgageBalance": "310000000",
        },
    }
    repository = app.extensions["operaciones_socios"]
    save = repository.guardar
    failures = 0

    def fail_once(operation, token):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("simulated crash after quote commit")
        save(operation, token)

    monkeypatch.setattr(repository, "guardar", fail_once)
    assert post(app, body).status_code == 500
    assert post(app, {**body, "telefono": "+573009999999"}).status_code == 409
    assert post(app, body).status_code == 201
    assert profiling.created == 1
    with crear_sesiones(database_url)() as session:
        assert session.scalar(select(func.count()).select_from(OfertaHipotecariaModel)) == 1


def test_expired_reservation_and_window_can_recover(database_url):
    now = datetime.now(UTC)
    admission = AdmisionSocios(crear_sesiones(database_url), cuota=1, reloj=lambda: now)
    operation = uuid4()
    admission._adquirir("BANK", operation, "fingerprint")
    assert admission.estado("BANK", operation) == "operacion_en_curso"
    now += timedelta(seconds=61)
    assert admission.estado("BANK", operation) == "operacion_reintentable"
    with admission.reservar("BANK", operation, "fingerprint"):
        assert admission.estado("BANK", operation) == "operacion_en_curso"


def test_old_worker_cannot_finalize_replaced_reservation(database_url):
    sessions = crear_sesiones(database_url)
    now = datetime.now(UTC)
    admission = AdmisionSocios(sessions, reloj=lambda: now)
    operation = uuid4()
    old_token = admission._adquirir("BANK", operation, "fingerprint")
    now += timedelta(seconds=61)
    new_token = admission._adquirir("BANK", operation, "fingerprint")
    with sessions.begin() as session:
        with pytest.raises(AdmisionRechazada):
            validar_reserva(session, "BANK", operation, old_token)
        validar_reserva(session, "BANK", operation, new_token)


@pytest.mark.parametrize(
    "code,status",
    [("cuota_socio_excedida", 429), ("socio_ocupado", 429), ("operacion_en_curso", 409)],
)
def test_admission_audit_is_structured_without_payload_identity(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    code: str,
    status: int,
) -> None:
    app = app_for(database_url)

    def reject(*args: object, **kwargs: object) -> UUID:
        raise AdmisionRechazada(code, status, 12)

    monkeypatch.setattr(app.extensions["admision_socios"], "_adquirir", reject)
    body = payload()
    correlation_id = str(uuid4())
    response = app.test_client().post(
        PATH,
        json=body,
        headers={
            "Authorization": "Bearer " + INTERNAL_TOKEN,
            "Idempotency-Key": body["operationId"],
            "X-Correlation-ID": correlation_id,
        },
    )
    assert response.status_code == status
    assert response.get_json() == {"error": code, "correlationId": correlation_id}
    assert response.headers["Retry-After"] == "12"
    records = [record for record in caplog.records if getattr(record, "event", None)]
    assert len(records) == 1
    fields = records[0].__dict__
    assert fields["event"] == "sol325.rechazo"
    assert json.loads(records[0].getMessage()) == {
        key: fields[key]
        for key in ("event", "code", "reason", "status", "correlationId", "endpoint")
    }
    assert fields["code"] == fields["reason"] == code
    assert fields["status"] == status
    assert fields["correlationId"] == response.headers["X-Correlation-ID"]
    assert "actorId" not in fields and "socioId" not in fields
    for value in (
        *(
            body[key]
            for key in (
                "socioId",
                "usuarioId",
                "clienteId",
                "correo",
                "identificacion",
                "telefono",
                "consentimientoId",
                "operationId",
            )
        ),
        body["datosProducto"]["vehiclePlate"],
        INTERNAL_TOKEN,
    ):
        assert str(value) not in repr(fields)
    assert fields["exc_info"] is None
