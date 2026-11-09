import json
from typing import cast
from urllib.request import Request
from uuid import uuid4

import pytest
from bff_movil.api import assistance
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from flask import Flask
from support import AUDIENCE, ISSUER, FakeIdentity, FakeJwks, KeyPair, sign_token


class FakeResponse:
    status = 200

    def __init__(self, body: dict[str, object], status: int = 200) -> None:
        self._body = json.dumps(body).encode()
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self._body


def token(keypair: KeyPair) -> str:
    return sign_token(keypair, scope="cliente")


def configured_app(jwks: FakeJwks, identity: FakeIdentity) -> Flask:
    return create_app(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            assistance_service_url="http://asistencias.test",
            internal_api_token="interno",
        ),
        identity_gateway=identity,
        token_verifier=JwtVerifier(JwksKeyStore(jwks.fetch), ISSUER, AUDIENCE, "movil"),
    )


def test_asistencias_requiere_sesion_cliente(app: Flask, keypair: KeyPair) -> None:
    without_session = app.test_client().post(
        "/movil/v1/prestadores/consulta", json={"ubicacion": {}}
    )
    wrong_scope = app.test_client().post(
        "/movil/v1/prestadores/consulta",
        headers={"Authorization": f"Bearer {sign_token(keypair, scope='socio')}"},
        json={"ubicacion": {}},
    )

    assert without_session.status_code == 401
    assert wrong_scope.status_code == 403


def test_bff_deriva_cliente_y_reenvia_consulta(
    jwks: FakeJwks,
    identity: FakeIdentity,
    keypair: KeyPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[Request] = []

    def open_upstream(request: Request, timeout: float) -> FakeResponse:
        forwarded.append(request)
        assert timeout == 5
        return FakeResponse({"prestadores": []})

    monkeypatch.setattr(assistance, "urlopen", open_upstream)
    app = configured_app(jwks, identity)
    payload = {
        "ubicacion": {
            "latitud": 4.711,
            "longitud": -74.0721,
            "origen": "permiso",
            "confirmada": True,
            "confirmadaEn": "2026-11-08T12:00:00Z",
        }
    }

    response = app.test_client().post(
        "/movil/v1/prestadores/consulta",
        headers={"Authorization": f"Bearer {token(keypair)}"},
        json=payload,
    )

    assert response.status_code == 200
    request = forwarded[0]
    assert request.full_url == "http://asistencias.test/v1/prestadores/consulta"
    assert request.get_header("X-cliente-id") is not None
    assert request.get_header("Authorization") == "Bearer interno"
    assert request.data is not None
    assert json.loads(cast(bytes, request.data)) == payload


def test_bff_reenvia_clave_idempotente_al_registrar(
    jwks: FakeJwks,
    identity: FakeIdentity,
    keypair: KeyPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[Request] = []

    def open_upstream(request: Request, timeout: float) -> FakeResponse:
        forwarded.append(request)
        return FakeResponse(
            {
                "id": str(uuid4()),
                "estado": "registrada",
                "prestadorId": str(uuid4()),
                "tipoAsistencia": "grua",
                "creadaEn": "2026-11-08T12:00:00Z",
            },
            201,
        )

    monkeypatch.setattr(assistance, "urlopen", open_upstream)
    app = configured_app(jwks, identity)
    operation = uuid4()

    response = app.test_client().post(
        "/movil/v1/asistencias",
        headers={
            "Authorization": f"Bearer {token(keypair)}",
            "Idempotency-Key": str(operation),
        },
        json={"dato": "el servicio valida el contrato"},
    )

    assert response.status_code == 201
    assert forwarded[0].get_header("Idempotency-key") == str(operation)


def test_fallo_upstream_no_confirma_asistencia(
    jwks: FakeJwks,
    identity: FakeIdentity,
    keypair: KeyPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: Request, timeout: float) -> FakeResponse:
        raise OSError("dependencia no disponible")

    monkeypatch.setattr(assistance, "urlopen", fail)
    app = configured_app(jwks, identity)

    response = app.test_client().post(
        "/movil/v1/asistencias",
        headers={
            "Authorization": f"Bearer {token(keypair)}",
            "Idempotency-Key": str(uuid4()),
        },
        json={"dato": "sintetico"},
    )

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Retry-After"] == "5"
