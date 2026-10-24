import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from jwcrypto import jwe, jwk

from tests.jmeter.local_stack import LocalStack


def test_cli_dependency_is_declared_for_integrated_workflows() -> None:
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements-dev.txt").read_text()
    assert any(line.startswith("fakeredis") for line in requirements.splitlines())


def test_cli_starts_encrypted_stack_and_cleans_up() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import threading
from types import SimpleNamespace
from tests.jmeter import local_stack
local_stack.threading = SimpleNamespace(
    Thread=threading.Thread,
    Event=lambda: SimpleNamespace(wait=lambda: None, set=lambda: None)
)
sys.argv = ["local_stack", "--web-port", "0", "--mobile-port", "0", "--encrypted-login"]
local_stack.main()
""",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BFF web:" in result.stdout


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
