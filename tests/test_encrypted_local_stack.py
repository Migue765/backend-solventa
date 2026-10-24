import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from jwcrypto import jwe, jwk

from tests.jmeter.local_stack import LocalStack


def call(url: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


@pytest.mark.parametrize("encrypted", [False, True])
def test_local_stack_login_flag(tmp_path: Path, encrypted: bool) -> None:
    email, password = "synthetic@example.com", "Local-Test-Password!"
    with LocalStack(tmp_path, encrypted_login_enabled=encrypted) as stack:
        web, mobile = stack.urls["web"], stack.urls["mobile"]
        status, _ = call(
            web + "/v1/usuarios",
            {"nombre": "Synthetic", "correo": email, "contrasena": password},
        )
        assert status == 201
        status, challenge = call(web + "/v1/sesiones/desafio")
        assert status == (200 if encrypted else 503)
        legacy_status, _ = call(web + "/v1/sesiones", {"correo": email, "contrasena": password})
        assert legacy_status == (400 if encrypted else 200)
        mobile_status, _ = call(
            mobile + "/movil/v1/sesiones", {"correo": email, "contrasena": password}
        )
        assert mobile_status == 200
        if encrypted:
            header = {
                "alg": "RSA-OAEP-256",
                "enc": "A256GCM",
                "kid": challenge["clavePublica"]["kid"],
                "typ": "solventa-login+jwe",
            }
            plaintext = json.dumps(
                {"desafioId": challenge["desafioId"], "correo": email, "contrasena": password}
            )
            envelope = jwe.JWE(plaintext.encode(), protected=json.dumps(header))
            envelope.add_recipient(jwk.JWK(**challenge["clavePublica"]))
            payload: dict[str, object] = {
                "correo": email,
                "credencialCifrada": envelope.serialize(compact=True),
            }
            assert call(web + "/v1/sesiones", payload)[0] == 200
            assert call(web + "/v1/sesiones", payload)[0] == 400
