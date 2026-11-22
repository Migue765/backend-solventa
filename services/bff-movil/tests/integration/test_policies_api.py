from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from flask import Flask
from flask.testing import FlaskClient
from support import AUDIENCE, ISSUER, USER, FakeIdentity, FakeJwks, IdentityServer, KeyPair
from support import sign_token as sign

INTERNAL = "interno-polizas"
SID = str(uuid4())
POLICY_ID = str(uuid4())
SUMMARY = {
    "id": POLICY_ID,
    "numero": "POL-20261014-ABCDEF12",
    "producto": {"tipo": "automotive", "nombre": "Seguro Automotriz Total"},
    "estado": "vigente",
    "vigencia": {"inicio": "2026-10-14", "fin": "2027-10-13"},
    "coberturaPrincipal": {"codigo": "danos_vehiculo", "nombre": "Danos al vehiculo"},
    "simulada": True,
}


@pytest.fixture
def polizas() -> Iterator[IdentityServer]:
    server = IdentityServer()
    yield server
    server.close()


def _app(
    jwks: FakeJwks, identity_server: IdentityServer, polizas: IdentityServer, **config: Any
) -> FlaskClient:
    values: dict[str, Any] = {
        "identity_url": identity_server.url,
        "jwt_issuer": ISSUER,
        "policies_service_url": polizas.url,
        "internal_api_token": INTERNAL,
        "identity_timeout_seconds": 2,
    }
    values.update(config)
    app: Flask = create_app(
        Config(**values),
        identity_gateway=FakeIdentity(),
        token_verifier=JwtVerifier(JwksKeyStore(jwks.fetch), ISSUER, AUDIENCE, "movil"),
    )
    return app.test_client()


def _auth(keypair: KeyPair, **claims: Any) -> dict[str, str]:
    values: dict[str, Any] = {"scope": "cliente", "sid": SID}
    values.update(claims)
    return {"Authorization": "Bearer " + sign(keypair, **values)}


def _session(identity_server: IdentityServer, active: bool = True) -> None:
    identity_server.respond("GET", f"/v1/sesiones/{SID}", 200, {"sesionId": SID, "activa": active})


def test_lista_solo_polizas_de_la_identidad_autenticada(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    _session(identity_server)
    polizas.respond("GET", "/v1/clientes/me/polizas", 200, {"polizas": [SUMMARY]})
    client = _app(jwks, identity_server, polizas)

    response = client.get(
        "/movil/v1/polizas",
        headers={**_auth(keypair), "X-Cliente-ID": str(uuid4())},
    )

    assert response.status_code == 200
    assert response.get_json() == {"polizas": [SUMMARY]}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    upstream = {k.lower(): v for k, v in polizas.requests[0]["headers"].items()}
    assert upstream["x-cliente-id"] == str(USER.user_id)
    assert upstream["authorization"] == "Bearer " + INTERNAL
    assert upstream["x-correlation-id"] == response.headers["X-Correlation-ID"]
    session_check = identity_server.requests[0]
    assert session_check["path"] == f"/v1/sesiones/{SID}"
    session_headers = {k.lower(): v for k, v in session_check["headers"].items()}
    assert session_headers["authorization"] == "Bearer " + INTERNAL


def test_lista_vacia_y_detalle_vigente(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    _session(identity_server)
    detail = {**SUMMARY, "version": 2, "plan": "premium", "coberturas": [], "prima": None}
    polizas.respond("GET", "/v1/clientes/me/polizas", 200, {"polizas": []})
    polizas.respond("GET", f"/v1/clientes/me/polizas/{POLICY_ID}", 200, detail)
    client = _app(jwks, identity_server, polizas)

    empty = client.get("/movil/v1/polizas", headers=_auth(keypair))
    found = client.get(f"/movil/v1/polizas/{POLICY_ID}", headers=_auth(keypair))

    assert empty.status_code == 200 and empty.get_json() == {"polizas": []}
    assert found.status_code == 200 and found.get_json() == detail


def test_poliza_ajena_o_inexistente_no_expone_datos(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    _session(identity_server)
    other = str(uuid4())
    polizas.respond(
        "GET",
        f"/v1/clientes/me/polizas/{other}",
        404,
        {"error": "poliza_no_encontrada", "correlationId": "interno"},
    )
    client = _app(jwks, identity_server, polizas)

    response = client.get(f"/movil/v1/polizas/{other}", headers=_auth(keypair))

    assert response.status_code == 404
    assert response.content_type == "application/problem+json"
    assert response.get_json()["code"] == "poliza_no_encontrada"
    assert other not in response.get_data(as_text=True)
    assert "interno" not in response.get_data(as_text=True)
    assert client.get("/movil/v1/polizas/no-uuid", headers=_auth(keypair)).status_code == 404


@pytest.mark.parametrize(
    "claims",
    [
        {"sid": None},
        {"sid": "no-uuid"},
        {"expires_in": -120},
        {"client_id": "web"},
    ],
)
def test_sesion_invalida_o_vencida_no_consulta(
    jwks: FakeJwks,
    keypair: KeyPair,
    identity_server: IdentityServer,
    polizas: IdentityServer,
    claims: dict[str, Any],
) -> None:
    _session(identity_server)
    client = _app(jwks, identity_server, polizas)

    response = client.get("/movil/v1/polizas", headers=_auth(keypair, **claims))

    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"
    assert polizas.requests == []


def test_sesion_cerrada_no_puede_consultar(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    _session(identity_server, active=False)
    client = _app(jwks, identity_server, polizas)

    listing = client.get("/movil/v1/polizas", headers=_auth(keypair))
    detail = client.get(f"/movil/v1/polizas/{POLICY_ID}", headers=_auth(keypair))
    anonymous = client.get("/movil/v1/polizas")

    assert listing.status_code == detail.status_code == anonymous.status_code == 401
    assert listing.get_json()["code"] == "sesion_invalida"
    assert listing.headers["WWW-Authenticate"] == "Bearer"
    assert polizas.requests == []


def test_rol_o_parametros_no_permitidos(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    _session(identity_server)
    client = _app(jwks, identity_server, polizas)

    advisor = client.get("/movil/v1/polizas", headers=_auth(keypair, scope="asesor"))
    other_client = client.get("/movil/v1/polizas?clienteId=" + str(uuid4()), headers=_auth(keypair))

    assert advisor.status_code == 403
    assert other_client.status_code == 400
    assert polizas.requests == []


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, {"error": "servicio_no_disponible"}),
        (401, {"error": "no_autorizado"}),
        (500, {"detalle": "traza interna"}),
        (200, ["no", "objeto"]),
    ],
)
def test_error_de_polizas_se_distingue_de_lista_vacia(
    jwks: FakeJwks,
    keypair: KeyPair,
    identity_server: IdentityServer,
    polizas: IdentityServer,
    status: int,
    body: object,
) -> None:
    _session(identity_server)
    polizas.respond("GET", "/v1/clientes/me/polizas", status, body)
    client = _app(jwks, identity_server, polizas)

    response = client.get("/movil/v1/polizas", headers=_auth(keypair))

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Retry-After"] == "5"
    assert "traza" not in response.get_data(as_text=True)


def test_identidad_o_configuracion_no_disponibles(
    jwks: FakeJwks, keypair: KeyPair, identity_server: IdentityServer, polizas: IdentityServer
) -> None:
    identity_server.respond("GET", f"/v1/sesiones/{SID}", 401, {"code": "no_autorizado"})
    client = _app(jwks, identity_server, polizas)
    unconfigured = _app(jwks, identity_server, polizas, policies_service_url="")

    identity_down = client.get("/movil/v1/polizas", headers=_auth(keypair))
    missing_config = unconfigured.get("/movil/v1/polizas", headers=_auth(keypair))

    assert identity_down.status_code == missing_config.status_code == 503
    assert identity_down.headers["Cache-Control"] == "no-store"
    assert polizas.requests == []
