import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app as create_bff
from bff_web.config import Config as BffConfig
from cotizacion.app import create_app as create_quotes
from cotizacion.config import Config as QuoteConfig
from cotizacion.infrastructure.persistence.models import CotizacionModel, PagoSimuladoModel
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from identidad.app import create_app as create_identity
from identidad.config import Config as IdentityConfig
from perfilamiento.adapters import FuenteMock
from perfilamiento.app import Config as ProfilingConfig
from perfilamiento.app import create_app as create_profiling
from perfilamiento.persistence import RepositorioPerfiles
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

    def authorize(self, version: int = 0) -> None:
        response = self.client.put(
            CONSENT, json={"autorizado": True, "version": version}, headers=self.headers
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
        registered = client.post(
            "/v1/usuarios",
            json={
                "nombre": "Cliente integrado",
                "correo": "integracion@solventa.co",
                "contrasena": "Solventa123!",
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
        yield Flow(client, headers, profiling, databases["cotizacion"])


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


def test_partial_source_failure_never_creates_a_quote(
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
    assert response.status_code == 503
    assert response.get_json()["code"] == "perfilamiento_no_disponible"
    with Session(create_engine(flow.quote_database)) as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 0


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
    registered = flow.client.post(
        "/v1/usuarios",
        json={"nombre": "Otro cliente", "correo": "otro-pago@solventa.co", "contrasena": password},
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
