from dataclasses import dataclass, field
from typing import Any

import pytest
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.domain.auth import TokenSet
from bff_movil.domain.errors import IdentityRejectedError, IdentityUnavailableError
from flask import Flask
from support import ISSUER, USER, bearer

CID = "11111111-1111-4111-8111-111111111111"


@dataclass
class Gateway:
    result: dict[str, Any] = field(default_factory=dict)
    failure: Exception | None = None
    calls: list[tuple[str, str, dict[str, object] | None, str | None]] = field(default_factory=list)

    def biometric_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        bearer: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        self.calls.append((method, path, body, bearer))
        if self.failure:
            raise self.failure
        return self.result

    def biometric_login(self, proof: dict[str, object], correlation_id: str) -> TokenSet:
        if self.failure:
            raise self.failure
        return TokenSet("access", "refresh", 3600, 2592000, USER)


def enabled(app: Flask, gateway: Gateway) -> Flask:
    return create_app(
        Config(
            identity_url="http://identity.test", jwt_issuer=ISSUER, biometric_login_enabled=True
        ),
        token_verifier=app.extensions["token_verifier"],
        biometric_gateway=gateway,
    )


def test_disabled_routes_are_not_silently_enabled(app: Flask) -> None:
    response = app.test_client().post("/movil/v1/biometria/desafios", json={"credencialId": CID})
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Cache-Control"] == "no-store"


def test_enrollment_forwards_original_bearer_and_never_a_form_subject(
    app: Flask, token: str
) -> None:
    gateway = Gateway(
        result={
            "credencialId": CID,
            "desafioId": "A" * 43,
            "proposito": "enrolar",
            "algoritmo": "ECDSA_P256_SHA256_DER",
            "mensaje": "A" * 20,
            "expiraEn": "2026-10-24T01:00:00Z",
        }
    )
    client = enabled(app, gateway).test_client()
    body = {"contrasena": "Solventa123!", "clavePublica": "A" * 122}
    response = client.post(
        "/movil/v1/biometria/enrolamientos/desafio", json=body, headers=bearer(token)
    )
    assert response.status_code == 200
    assert gateway.calls == [("POST", "/v1/biometria/enrolamientos/desafio", body, token)]
    assert (
        client.post(
            "/movil/v1/biometria/enrolamientos/desafio",
            json={**body, "usuarioId": CID},
            headers=bearer(token),
        ).status_code
        == 400
    )
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "failure,status",
    [
        (IdentityRejectedError(429, "demasiados_intentos", 1), 429),
        (IdentityRejectedError(401, "credenciales_invalidas"), 401),
        (IdentityUnavailableError(), 503),
    ],
)
def test_upstream_failure_never_becomes_success(
    app: Flask, failure: Exception, status: int
) -> None:
    client = enabled(app, Gateway(failure=failure)).test_client()
    response = client.post(
        "/movil/v1/sesiones/biometria",
        json={
            "credencialId": CID,
            "desafioId": "A" * 43,
            "firma": "A" * 8,
        },
    )
    assert response.status_code == status
    assert "accessToken" not in response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    if status == 429:
        assert response.headers["Retry-After"] == "1"


def test_malformed_upstream_is_not_an_authentication_failure(app: Flask) -> None:
    response = (
        enabled(app, Gateway(result={"biometriaOK": True}))
        .test_client()
        .post("/movil/v1/biometria/desafios", json={"credencialId": CID})
    )
    assert response.status_code == 503


def test_invalid_flag_is_not_silently_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOMETRIC_LOGIN_ENABLED", "enabled")
    with pytest.raises(ValueError, match="BIOMETRIC_LOGIN_ENABLED"):
        Config.from_environment()


def test_delete_never_confirms_unexpected_upstream_payload(app: Flask, token: str) -> None:
    client = enabled(app, Gateway(result={"estado": "pendiente"})).test_client()
    response = client.delete(f"/movil/v1/biometria/credenciales/{CID}", headers=bearer(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
