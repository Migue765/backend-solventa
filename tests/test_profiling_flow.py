import json
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.error import URLError
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app as create_bff
from bff_web.config import Config as BffConfig
from cotizacion.app import create_app as create_quotes
from cotizacion.config import Config as QuoteConfig
from cotizacion.infrastructure.perfil_http import PerfilResponse, ProveedorPerfilHttp
from cotizacion.infrastructure.persistence.models import CotizacionModel, PagoSimuladoModel
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from identidad.app import create_app as create_identity
from identidad.config import Config as IdentityConfig
from identidad.consent_outbox import ConsentOutbox, publish_http
from identidad.infrastructure.persistence.database import create_sessions
from identidad.infrastructure.persistence.models import ConsentEventModel
from jsonschema import Draft202012Validator, FormatChecker
from perfilamiento.adapters import FuenteMock
from perfilamiento.app import Config as ProfilingConfig
from perfilamiento.app import create_app as create_profiling
from perfilamiento.persistence import PerfilModel, RepositorioPerfiles
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
INTERNAL_TOKEN = "token-interno-solo-para-pruebas"
CONSENT = "/v1/consentimientos/perfilamiento"
REQUESTS = "/v1/solicitudes-cotizacion"
QUOTE = {"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}}


@contextmanager
def serve(app: Flask) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@dataclass
class Flow:
    client: FlaskClient
    headers: dict[str, str]
    profiling: Flask
    quote_database: str
    identity: Flask
    identity_database: str
    profile_url: str

    def authorize(self, version: int = 0) -> None:
        response = self.client.put(
            CONSENT,
            json={
                "autorizado": True,
                "version": version,
                "solicitud": {
                    "finalidad": "cotizacion",
                    "alcances": ["open_finance", "open_data"],
                    "fuentes": ["open_finance", "open_data"],
                    "datos": ["indicador_riesgo"],
                    "versionTerminos": "1.0",
                    "vigenteHasta": (datetime.now(UTC) + timedelta(days=29)).isoformat(),
                },
            },
            headers=self.headers,
        )
        assert response.status_code == 200

    def create_quote(self) -> str:
        response = self.client.post(
            REQUESTS,
            json=QUOTE,
            headers={**self.headers, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201, response.get_json()
        return str(response.get_json()["id"])


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flow]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_SCHEMA", raising=False)
    databases = {}
    for service in ("identidad", "cotizacion"):
        database = f"sqlite:///{tmp_path / (service + '.db')}"
        config = AlembicConfig(str(ROOT / "services" / service / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database)
        command.upgrade(config, "head")
        databases[service] = database
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    issuer = "https://identity.integration.test"
    identity = create_identity(
        IdentityConfig(
            database_url=databases["identidad"],
            jwt_private_key=pem,
            jwt_issuer=issuer,
            internal_api_token=INTERNAL_TOKEN,
            password_iterations=1000,
        )
    )
    with ExitStack() as stack:
        identity_url = stack.enter_context(serve(identity))
        profile_database = f"sqlite:///{tmp_path / 'profiles.db'}"
        RepositorioPerfiles(profile_database).inicializar()
        profiling = create_profiling(
            ProfilingConfig(
                internal_api_token=INTERNAL_TOKEN,
                database_url=profile_database,
                identidad_url=identity_url,
            )
        )
        profile_url = stack.enter_context(serve(profiling))
        quotes = create_quotes(
            QuoteConfig(
                database_url=databases["cotizacion"],
                perfilamiento_habilitado=True,
                perfilamiento_url=profile_url,
                internal_api_token=INTERNAL_TOKEN,
            )
        )
        quote_url = stack.enter_context(serve(quotes))
        bff = create_bff(
            BffConfig(
                identity_url=identity_url,
                jwt_issuer=issuer,
                quote_service_url=quote_url,
                internal_api_token=INTERNAL_TOKEN,
            )
        )
        client = bff.test_client()
        registered = identity.test_client().post(
            "/v1/usuarios",
            json={
                "nombre": "Cliente integrado",
                "correo": "integracion@solventa.co",
                "contrasena": "Solventa123!",
                "tipoDocumento": "cc",
                "numeroDocumento": "1012345678",
                "telefono": "+573001234567",
                "fechaNacimiento": "1990-01-01",
            },
        )
        assert registered.status_code == 201
        login = client.post(
            "/v1/sesiones",
            json={
                "correo": "integracion@solventa.co",
                "contrasena": "Solventa123!",
            },
        )
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.get_json()["accessToken"]}
        yield Flow(
            client,
            headers,
            profiling,
            databases["cotizacion"],
            identity,
            databases["identidad"],
            profile_url,
        )


def test_jwt_consent_quote_profile_offers_selection_and_revocation(flow: Flow) -> None:
    assert flow.client.get(CONSENT, headers=flow.headers).status_code == 404
    denied = flow.client.post(
        REQUESTS,
        json=QUOTE,
        headers={
            **flow.headers,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert denied.status_code == 403
    assert denied.get_json()["code"] == "consentimiento_ausente"
    flow.authorize()
    request_id = flow.create_quote()
    base = f"{REQUESTS}/{request_id}"
    profile = flow.client.get(base + "/perfilamiento", headers=flow.headers)
    assert profile.status_code == 200
    data = profile.get_json()
    assert data["solicitudId"] == request_id
    assert data["consentimientoVersion"] == 1
    assert data["fallbackUsed"] is False
    assert {source["nombre"] for source in data["fuentes"]} == {"open_finance", "open_data"}
    assert all(source["simulada"] for source in data["fuentes"])
    offers = flow.client.get(base + "/ofertas", headers=flow.headers)
    assert offers.status_code == 200
    assert len(offers.get_json()["ofertas"]) == 3
    offer = offers.get_json()["ofertas"][0]
    selected = flow.client.put(
        base + "/seleccion",
        json={
            "ofertaId": offer["id"],
            "version": offer["version"],
        },
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert selected.status_code == 200
    revoked = flow.client.put(
        CONSENT, json={"autorizado": False, "version": 1}, headers=flow.headers
    )
    assert revoked.status_code == 200
    effective_at = datetime.fromisoformat(revoked.get_json()["efectivaEn"])
    # El Outbox sigue pendiente: la consulta autoritativa ya debe bloquear todo nuevo uso.
    for suffix in ("/perfilamiento", "/ofertas", "/seleccion"):
        assert flow.client.get(base + suffix, headers=flow.headers).status_code == 403
    assert (
        flow.client.post(
            REQUESTS, json=QUOTE, headers={**flow.headers, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 403
    )
    sessions = create_sessions(flow.identity_database, None, 1, 0)
    schema = json.loads((ROOT / "contracts/events/consentimiento-v1.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    attempts: list[str] = []

    def publish(event: dict[str, object]) -> dict[str, object]:
        validator.validate(event)
        attempts.append(str(event["tipo"]))
        ack = cast(dict[str, object], publish_http(flow.profile_url, INTERNAL_TOKEN, event))
        if len(attempts) == 1:
            raise URLError("Acuse perdido despues de persistir el evento")
        return ack

    outbox = ConsentOutbox(sessions, publish)
    assert outbox.deliver() == (1, 1)
    assert attempts[0] == "ConsentimientoRevocado"
    assert outbox.deliver() == (1, 0)
    assert attempts == (
        ["ConsentimientoRevocado", "ConsentimientoAutorizado", "ConsentimientoRevocado"]
    )
    with sessions() as session:
        event = session.scalars(
            select(ConsentEventModel).where(ConsentEventModel.tipo == "ConsentimientoRevocado")
        ).one()
        assert event.publicado and event.acuse is not None
        assert event.intentos == 2
        applied_at = datetime.fromisoformat(str(event.acuse["aplicadaEn"]))
        assert timedelta(0) <= applied_at - effective_at <= timedelta(minutes=5)
    assert outbox.deliver() == (0, 0)
    for path in ("/perfilamiento", "/ofertas", "/seleccion"):
        response = flow.client.get(base + path, headers=flow.headers)
        assert response.status_code == 403, response.get_json()
        assert response.get_json()["code"] == "consentimiento_no_vigente"
    denied_selection = flow.client.put(
        base + "/seleccion",
        json={
            "ofertaId": offer["id"],
            "version": offer["version"],
        },
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert denied_selection.status_code == 403
    flow.authorize(2)
    assert flow.client.get(base + "/perfilamiento", headers=flow.headers).status_code == 403
    new_request = flow.create_quote()
    new_profile = flow.client.get(f"{REQUESTS}/{new_request}/perfilamiento", headers=flow.headers)
    assert new_profile.status_code == 200
    assert new_profile.get_json()["perfilId"] != data["perfilId"]
    assert new_profile.get_json()["consentimientoVersion"] == 3


def test_incomplete_decisions_and_actor_spoofing_never_authorize(flow: Flow) -> None:
    for payload in (
        {"autorizado": True, "version": 0},
        {"autorizado": True, "version": 0, "solicitud": {"finalidad": "cotizacion"}},
        {"autorizado": True, "version": 0, "actorId": str(uuid4())},
    ):
        assert flow.client.put(CONSENT, json=payload, headers=flow.headers).status_code == 400
    assert flow.client.get(CONSENT, headers=flow.headers).status_code == 404
    assert flow.client.put(CONSENT, json={"autorizado": False, "version": 0}).status_code == 401
    with Session(create_engine(flow.identity_database)) as session:
        assert session.scalar(select(func.count()).select_from(ConsentEventModel)) == 0


@pytest.mark.parametrize("state", ["rechazado", "parcial", "vencido"])
def test_decisions_without_required_permission_block_the_full_flow(
    flow: Flow, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.authorize()
    approved = flow.client.get(CONSENT, headers=flow.headers).get_json()
    if state == "vencido":
        future = datetime.fromisoformat(approved["vigenteHasta"])
        monkeypatch.setitem(flow.identity.extensions, "clock", lambda: future)
        assert flow.client.get(CONSENT, headers=flow.headers).get_json()["estado"] == "vencido"
    else:
        scope = {
            key: approved[key]
            for key in (
                "finalidad",
                "alcances",
                "fuentes",
                "datos",
                "versionTerminos",
                "vigenteHasta",
            )
        }
        if state == "parcial":
            scope.update(alcances=["open_data"], fuentes=["open_data"])
        result = flow.client.put(
            CONSENT,
            headers=flow.headers,
            json={"autorizado": state != "rechazado", "version": 1, "solicitud": scope},
        )
        assert result.status_code == 200
    response = flow.client.post(
        REQUESTS, json=QUOTE, headers={**flow.headers, "Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "consentimiento_no_vigente"
    with Session(create_engine(flow.quote_database)) as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 0


def test_partial_source_failure_creates_traceable_degraded_profile(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.authorize()
    service = flow.profiling.extensions["consultar_perfil"]
    monkeypatch.setattr(
        service,
        "_fuentes",
        (
            FuenteMock("open_finance"),
            FuenteMock("open_data", "no_disponible"),
        ),
    )
    response = flow.client.post(
        REQUESTS,
        json=QUOTE,
        headers={
            **flow.headers,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 201
    profile = flow.client.get(
        f"{REQUESTS}/{response.get_json()['id']}/perfilamiento",
        headers=flow.headers,
    )
    assert profile.status_code == 200
    assert profile.get_json()["fallbackUsed"] is True
    assert profile.get_json()["degradaciones"] == [
        {
            "fuente": "open_data",
            "motivo": "fuente_no_disponible",
            "politica": "default",
            "versionPolitica": "degradacion-1",
        }
    ]
    with Session(create_engine(flow.quote_database)) as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1


def test_authorized_cached_source_reaches_quote_and_is_explicit(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.authorize()
    first = flow.create_quote()
    original = flow.client.get(f"{REQUESTS}/{first}/perfilamiento", headers=flow.headers).get_json()
    monkeypatch.setattr(
        flow.profiling.extensions["consultar_perfil"],
        "_fuentes",
        (FuenteMock("open_finance"), FuenteMock("open_data", "no_disponible")),
    )
    second = flow.create_quote()
    response = flow.client.get(f"{REQUESTS}/{second}/perfilamiento", headers=flow.headers)
    assert response.status_code == 200
    profile = response.get_json()
    assert profile["fallbackUsed"] is True
    esperada = {
        **original["fuentes"][1],
        "version": f"{original['fuentes'][1]['version']}:cache",
    }
    assert {**profile["fuentes"][1], "vigenteHasta": None} == {**esperada, "vigenteHasta": None}
    assert profile["estadoFuentes"][1]["estado"] == "cache"
    assert profile["estadoFuentes"][1]["motivo"] == "fuente_no_disponible"
    contract = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(
        contract["components"]["schemas"]["Perfilamiento"], format_checker=FormatChecker()
    )
    validator.validate(profile)
    validator.validate(original)
    without_metadata = {key: value for key, value in original.items() if key != "estadoFuentes"}
    validator.validate(without_metadata)
    assert not validator.is_valid({**without_metadata, "fallbackUsed": True})
    assert not validator.is_valid({**profile, "fallbackUsed": False})
    extended = {
        **profile,
        "fuentes": [*profile["fuentes"], {**profile["fuentes"][0], "nombre": "fuente_adicional"}],
        "estadoFuentes": [
            *profile["estadoFuentes"],
            {**profile["estadoFuentes"][0], "fuente": "fuente_adicional"},
        ],
    }
    validator.validate(extended)
    with Session(create_engine(flow.quote_database)) as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 2

    repo = cast(RepositorioPerfiles, flow.profiling.extensions["perfiles"])
    with Session(repo.engine) as session:
        persisted = session.get(PerfilModel, UUID(profile["perfilId"]))
        assert persisted is not None
        decoded = PerfilResponse.model_validate(persisted.contenido)
    extra = decoded.fuentes[0].model_copy(update={"nombre": "fuente_adicional"})
    extra_estado = decoded.estadoFuentes[0].model_copy(update={"fuente": "fuente_adicional"})
    decoded = PerfilResponse.model_validate(
        {
            **decoded.model_dump(),
            "fuentes": (*decoded.fuentes, extra),
            "estadoFuentes": (*decoded.estadoFuentes, extra_estado),
        }
    )
    assert decoded.fuentes[2].nombre == "fuente_adicional"
    monkeypatch.setattr(
        "cotizacion.infrastructure.perfil_http.urlopen",
        lambda request, timeout: BytesIO(decoded.model_dump_json().encode()),
    )
    risk = ProveedorPerfilHttp("http://perfilamiento.invalid", INTERNAL_TOKEN).obtener(
        str(decoded.perfilId), decoded.clienteId
    )
    assert risk.evidencia is not None
    assert len(risk.evidencia["fuentes"]) == 3


def test_expired_profile_blocks_existing_offers(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.authorize()
    request_id = flow.create_quote()
    future = datetime.now(UTC) + timedelta(minutes=16)
    monkeypatch.setattr(flow.profiling.extensions["consultar_perfil"], "_reloj", lambda: future)
    for suffix in ("/perfilamiento", "/ofertas"):
        response = flow.client.get(f"{REQUESTS}/{request_id}{suffix}", headers=flow.headers)
        assert response.status_code == 422
        assert response.get_json()["code"] == "perfil_vencido"


def test_real_jwt_payment_recovery_ownership_and_saga(flow: Flow) -> None:
    flow.authorize()
    request_id = flow.create_quote()
    base = f"{REQUESTS}/{request_id}"
    payment_path = base + "/pago-simulado"
    for method in ("GET", "POST"):
        denied = flow.client.open(payment_path, method=method)
        assert denied.status_code == 401
    forged = flow.client.get(
        payment_path, headers={"Authorization": "Bearer invalid.jwt.signature"}
    )
    assert forged.status_code == 401
    missing = flow.client.get(payment_path, headers=flow.headers)
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "pago_no_encontrado"
    offer = flow.client.get(base + "/ofertas", headers=flow.headers).get_json()["ofertas"][0]
    selected = flow.client.put(
        base + "/seleccion",
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
        json={"ofertaId": offer["id"], "version": offer["version"]},
    )
    assert selected.status_code == 200
    subscription = flow.client.post(
        base + "/suscripcion",
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert subscription.status_code == 201
    key = str(uuid4())
    headers = {**flow.headers, "Idempotency-Key": key}
    for invalid in ({"importe": {"monto": "1", "moneda": "COP"}}, {"clienteId": str(uuid4())}):
        assert flow.client.post(payment_path, headers=headers, json=invalid).status_code == 400
    payment = flow.client.post(payment_path, headers=headers, json={})
    assert payment.status_code == 200, payment.get_json()
    data = payment.get_json()
    assert data["simulada"] is True
    assert data["estado"] == "confirmado"
    assert data["importe"] == selected.get_json()["prima"]
    assert data["referenciaInterna"] == "SIM-" + data["id"]
    assert data["suscripcionId"] == subscription.get_json()["id"]
    assert flow.client.post(payment_path, headers=headers).get_json() == data
    assert flow.client.get(payment_path, headers=flow.headers).get_json() == data
    updated = flow.client.get(base + "/suscripcion", headers=flow.headers).get_json()
    assert updated["saga"] == data["saga"]
    assert updated["estado"] == "aprobada"
    # Otra sesión JWT real no puede cambiar ownership mediante query ni body.
    password = "Integration123!"
    registered = flow.identity.test_client().post(
        "/v1/usuarios",
        json={
            "nombre": "Otro cliente",
            "correo": "otro-pago@solventa.co",
            "contrasena": password,
            "tipoDocumento": "cc",
            "numeroDocumento": "1098765432",
            "telefono": "+573009876543",
            "fechaNacimiento": "1992-02-02",
        },
    )
    assert registered.status_code == 201
    login = flow.client.post(
        "/v1/sesiones", json={"correo": "otro-pago@solventa.co", "contrasena": password}
    )
    other_headers = {
        "Authorization": "Bearer " + login.get_json()["accessToken"],
        "Idempotency-Key": key,
    }
    for method in ("GET", "POST"):
        denied = flow.client.open(payment_path, method=method, headers=other_headers)
        assert denied.status_code == 404
        assert denied.get_json()["code"] == "solicitud_no_encontrada"
    with Session(create_engine(flow.quote_database)) as session:
        assert session.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
