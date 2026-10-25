import base64
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fakeredis
import pytest
from bff_movil.app import create_app as create_bff
from bff_movil.config import Config as BffConfig
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from flask import Flask
from identidad.app import create_app as create_identity
from identidad.config import Config as IdentityConfig
from identidad.infrastructure.persistence.models import Base
from sqlalchemy import create_engine
from werkzeug.serving import make_server

ISSUER = "https://identidad.solventa.test"
PASSWORD = "Solventa123!"


@contextmanager
def serving(app: Flask) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(
    url: str,
    body: dict[str, object] | None = None,
    bearer: str | None = None,
    method: str = "POST",
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def signed(challenge: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> dict[str, object]:
    return {
        "credencialId": challenge["credencialId"],
        "desafioId": challenge["desafioId"],
        "firma": b64u(
            key.sign(
                base64.urlsafe_b64decode(challenge["mensaje"] + "=="),
                ec.ECDSA(hashes.SHA256()),
            )
        ),
    }


@pytest.fixture
def local_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Flask]]:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *args, **kwargs: redis)
    url = f"sqlite:///{tmp_path / 'biometric.db'}"
    Base.metadata.create_all(create_engine(url))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    identity = create_identity(
        IdentityConfig(
            database_url=url,
            jwt_private_key=pem,
            jwt_issuer=ISSUER,
            password_iterations=1000,
            max_attempts=50,
            biometric_login_enabled=True,
            redis_url="redis://test-double",
        )
    )
    with serving(identity) as identity_url:
        bff = create_bff(
            BffConfig(
                identity_url=identity_url,
                jwt_issuer=ISSUER,
                biometric_login_enabled=True,
            )
        )
        with serving(bff) as bff_url:
            assert call(identity_url + "/ping", method="GET")[0] == 200
            assert call(bff_url + "/ping", method="GET")[0] == 200
            yield bff_url + "/movil/v1", identity


def test_real_http_login_enrollment_biometric_logout_and_revocation(
    local_stack: tuple[str, Flask],
) -> None:
    api, _ = local_stack
    assert (
        call(
            api + "/usuarios",
            {
                "nombre": "Sintetica",
                "correo": "biometria@example.com",
                "contrasena": PASSWORD,
            },
        )[0]
        == 201
    )
    status, conventional = call(
        api + "/sesiones", {"correo": "biometria@example.com", "contrasena": PASSWORD}
    )
    assert status == 200
    bearer = conventional["accessToken"]
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    status, challenge = call(
        api + "/biometria/enrolamientos/desafio",
        {
            "contrasena": PASSWORD,
            "clavePublica": b64u(public),
        },
        bearer,
    )
    assert status == 200
    status, enrolled = call(api + "/biometria/enrolamientos", signed(challenge, key), bearer)
    assert status == 201 and enrolled["estado"] == "activa"
    credential_id = enrolled["credencialId"]

    def biometric_login() -> dict[str, Any]:
        status, nonce = call(api + "/biometria/desafios", {"credencialId": credential_id})
        assert status == 200
        payload = signed(nonce, key)
        status, session = call(api + "/sesiones/biometria", payload)
        assert status == 200 and session["usuario"] == conventional["usuario"]
        assert call(api + "/sesiones/biometria", payload)[0] == 401
        return session

    session = biometric_login()
    assert call(api + "/sesiones/actual", bearer=session["accessToken"], method="GET")[0] == 200
    status, renewed = call(api + "/sesiones/renovar", {"refreshToken": session["refreshToken"]})
    assert status == 200
    assert (
        call(
            api + "/sesiones/actual",
            {"refreshToken": renewed["refreshToken"]},
            renewed["accessToken"],
            "DELETE",
        )[0]
        == 204
    )
    assert call(api + "/sesiones/renovar", {"refreshToken": renewed["refreshToken"]})[0] == 401
    status, credentials = call(api + "/biometria/credenciales", bearer=bearer, method="GET")
    assert status == 200 and credentials["credenciales"][0]["estado"] == "activa"
    second = biometric_login()
    assert (
        call(api + "/biometria/credenciales/" + credential_id, bearer=bearer, method="DELETE")[0]
        == 204
    )
    assert call(api + "/biometria/desafios", {"credencialId": credential_id})[0] == 401
    assert call(api + "/sesiones/renovar", {"refreshToken": second["refreshToken"]})[0] == 401
    # Current access JWT retains its original expiry; no immediate revocation claim.
    assert call(api + "/sesiones/actual", bearer=second["accessToken"], method="GET")[0] == 200
    assert call(api + "/sesiones/renovar", {"refreshToken": conventional["refreshToken"]})[0] == 200


def test_bff_limits_body_and_rejects_anonymous_enrollment(
    local_stack: tuple[str, Flask],
) -> None:
    api, _ = local_stack
    assert call(api + "/biometria/enrolamientos/desafio", {})[0] == 401
    assert call(api + "/biometria/desafios", {"biometriaOK": True})[0] == 400
    assert call(api + "/biometria/desafios", {"credencialId": "x" * 8193})[0] == 400
    assert (
        call(
            api + "/sesiones/biometria",
            {
                "credencialId": "11111111-1111-4111-8111-111111111111",
                "desafioId": "A" * 43,
                "firma": "A" * 8,
            },
        )[0]
        == 401
    )
