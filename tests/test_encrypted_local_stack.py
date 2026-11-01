import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from socket import socket
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import pytest
from jwcrypto import jwe, jwk
from perfilamiento.adapters import FuenteMock
from perfilamiento.domain import Fuente
from perfilamiento.persistence import ConsultaInformacionModel, ConsultaModel, FuenteCacheModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


@pytest.mark.parametrize("module", ["tests.jmeter.local_stack", "tests.jmeter.run"])
def test_cli_uses_checkout_before_foreign_service_installations(
    tmp_path: Path, module: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    services = {
        "bff_movil": "bff-movil",
        "bff_web": "bff-web",
        "cotizacion": "cotizacion",
        "identidad": "identidad",
        "perfilamiento": "perfilamiento",
        "polizas": "polizas",
    }
    for package in services:
        foreign = tmp_path / package
        foreign.mkdir()
        (foreign / "__init__.py").write_text(
            'raise AssertionError("Imported a foreign service installation")\n'
        )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import json
import sys
from pathlib import Path

importlib.import_module(sys.argv[1])
root = Path.cwd()
for package, service in json.loads(sys.argv[2]).items():
    loaded = importlib.import_module(package)
    expected = root / "services" / service / "src" / package / "__init__.py"
    assert Path(loaded.__file__).resolve() == expected.resolve(), package
""",
            module,
            json.dumps(services),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def call(
    url: str,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    *,
    method: str | None = None,
) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def encrypted_login_payload(
    challenge: dict[str, Any], email: str, password: str
) -> dict[str, object]:
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
    return {"correo": email, "credencialCifrada": envelope.serialize(compact=True)}


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
            payload = encrypted_login_payload(challenge, email, password)
            assert call(web + "/v1/sesiones", payload)[0] == 200
            assert call(web + "/v1/sesiones", payload)[0] == 400


def test_local_bff_uses_only_its_own_profile_http_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with LocalStack(tmp_path, encrypted_login_enabled=True) as stack:
        email, password = "partner@example.com", "Local-Test-Password!"
        created = (
            stack.apps["identity"]
            .test_cli_runner()
            .invoke(
                args=[
                    "create-partner-user",
                    "--name",
                    "Socio sintetico",
                    "--email",
                    email,
                    "--partner-id",
                    "LOCAL-PARTNER",
                    "--partner-name",
                    "Banco sintetico",
                ],
                input=f"{password}\n{password}\n",
            )
        )
        assert created.exit_code == 0, created.output
        challenge_status, challenge = call(stack.urls["web"] + "/v1/sesiones/desafio")
        assert challenge_status == 200
        status, session = call(
            stack.urls["web"] + "/v1/sesiones",
            encrypted_login_payload(challenge, email, password),
        )
        assert status == 200 and session["usuario"]["socios"] == ["LOCAL-PARTNER"]
        outbound: list[str] = []

        def owned_profile_only(request: Request, *, timeout: float) -> Any:
            if not request.full_url.startswith(stack.urls["profile"] + "/"):
                raise URLError("El BFF intento usar un Perfilamiento ajeno al stack")
            outbound.append(request.full_url)
            return urlopen(request, timeout=timeout)

        monkeypatch.setattr(
            "bff_web.infrastructure.http_client_information_gateway.urlopen",
            owned_profile_only,
        )
        operation_id = uuid4()
        path = f"/v1/socios/consultas-informacion-cliente/{operation_id}?socioId=LOCAL-PARTNER"
        assert call(stack.urls["web"] + path)[0] == 401
        assert not outbound
        status, result = call(
            stack.urls["web"] + path,
            headers={"Authorization": "Bearer " + session["accessToken"]},
        )
        assert status == 404 and result["code"] == "consulta_no_encontrada"
        assert outbound == [
            f"{stack.urls['profile']}/v1/consultas-informacion-cliente/{operation_id}"
            "?socioId=LOCAL-PARTNER"
        ]


def test_hu44_resilience_over_owned_encrypted_http_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    with TemporaryDirectory(prefix="hu44-", dir=tmp_path) as directory:
        with LocalStack(Path(directory), encrypted_login_enabled=True) as stack:
            ports = [server.server_port for server in stack.servers.values()]
            server_threads = list(stack.threads.values())
            assert not {4200, 4300}.intersection(ports)
            profile_app = stack.apps["profile"]
            info = profile_app.extensions["consultar_informacion"]
            repo = profile_app.extensions["perfiles"]
            password = "Local-Test-Password!"
            email = f"hu44-{uuid4().hex}@example.com"
            status, _ = call(
                stack.urls["identity"] + "/v1/usuarios",
                {
                    "nombre": "Cliente sintetico HU44",
                    "correo": email,
                    "contrasena": password,
                    "tipoDocumento": "passport",
                    "numeroDocumento": uuid4().hex[:16].upper(),
                    "telefono": "+573001234567",
                    "fechaNacimiento": "1990-01-01",
                },
            )
            assert status == 201

            def login(correo: str) -> dict[str, Any]:
                status, challenge = call(stack.urls["web"] + "/v1/sesiones/desafio")
                assert status == 200
                status, session = call(
                    stack.urls["web"] + "/v1/sesiones",
                    encrypted_login_payload(challenge, correo, password),
                )
                assert status == 200
                return session

            client_session = login(email)
            client_headers = {"Authorization": "Bearer " + client_session["accessToken"]}
            created = (
                stack.apps["identity"]
                .test_cli_runner()
                .invoke(
                    args=[
                        "create-partner-user",
                        "--name",
                        "Socio HU44",
                        "--email",
                        "hu44@example.com",
                        "--partner-id",
                        "BANK-HU44",
                        "--partner-name",
                        "Banco sintetico",
                    ],
                    input=f"{password}\n{password}\n",
                )
            )
            assert created.exit_code == 0, created.output
            partner = login("hu44@example.com")
            partner_headers = {"Authorization": "Bearer " + partner["accessToken"]}
            path = stack.urls["web"] + "/v1/socios/consultas-informacion-cliente"
            consent_url = stack.urls["web"] + "/v1/consentimientos/perfilamiento"
            release, finished = Event(), Event()
            scenario = ["normal"]
            calls: list[str] = []
            real = FuenteMock.consultar

            def source(adapter: FuenteMock, client: UUID, now: datetime) -> Fuente:
                calls.append(adapter.nombre)
                if adapter.nombre == "open_data":
                    if scenario[0] == "slow":
                        try:
                            assert release.wait(timeout=3)
                            return real(adapter, client, now)
                        finally:
                            finished.set()
                    if scenario[0] == "failed":
                        raise OSError("detalle privado que no debe salir")
                    if scenario[0] == "revoked":
                        status, _ = call(
                            consent_url,
                            {"autorizado": False, "version": 1},
                            client_headers,
                            method="PUT",
                        )
                        assert status == 200
                return real(adapter, client, now)

            monkeypatch.setattr(FuenteMock, "consultar", source)

            def consult() -> tuple[int, dict[str, Any]]:
                operation = str(uuid4())
                return call(
                    path,
                    {
                        "operationId": operation,
                        "socioId": "BANK-HU44",
                        "clienteId": client_session["usuario"]["id"],
                        "productoId": "mortgage-life",
                        "finalidad": "cotizacion",
                        "datosSolicitados": ["indicador_riesgo", "saldo"],
                    },
                    {
                        **partner_headers,
                        "Idempotency-Key": operation,
                        "API-Version": "1",
                        "X-Correlation-ID": "hu44-http-" + scenario[0],
                    },
                )

            assert call(path, {})[0] == 401
            status, denied = consult()
            assert status == 403 and denied["code"] == "consentimiento_invalido"
            assert calls == []
            status, _ = call(
                consent_url,
                {
                    "autorizado": True,
                    "version": 0,
                    "solicitud": {
                        "finalidad": "cotizacion",
                        "alcances": ["open_finance", "open_data"],
                        "fuentes": ["open_finance", "open_data"],
                        "datos": ["indicador_riesgo"],
                        "versionTerminos": "1.0",
                        "vigenteHasta": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    },
                },
                client_headers,
                method="PUT",
            )
            assert status == 200

            scenario[0] = "slow"
            try:
                status, unavailable = consult()
                assert status == 503 and unavailable["code"] == "servicio_no_disponible"
                assert not release.is_set()
            finally:
                release.set()
            assert finished.wait(timeout=3)
            with Session(repo.engine) as session:
                for model in (ConsultaInformacionModel, FuenteCacheModel):
                    assert session.scalar(select(func.count()).select_from(model)) == 0

            scenario[0] = "normal"
            status, original = consult()
            assert status == 201
            assert original["datos"] == {"indicadorRiesgo": "0.50"}
            assert set(original) == {
                "operationId",
                "socioId",
                "actorId",
                "clienteId",
                "productoId",
                "finalidad",
                "estado",
                "consentimientoId",
                "consentimientoVersion",
                "datos",
                "evidenciaFuentes",
                "consultadaEn",
            }
            assert original["actorId"] == partner["usuario"]["id"]
            assert original["clienteId"] == client_session["usuario"]["id"]
            for mode in ("failed", "slow"):
                scenario[0] = mode
                release.clear()
                finished.clear()
                try:
                    status, cached = consult()
                    assert status == 201
                    assert cached["datos"] == original["datos"]
                    evidence = cached["evidenciaFuentes"][1]
                    assert (
                        evidence["version"] == original["evidenciaFuentes"][1]["version"] + ":cache"
                    )
                    assert (
                        evidence["consultadaEn"] == original["evidenciaFuentes"][1]["consultadaEn"]
                    )
                    if mode == "slow":
                        assert not release.is_set()
                finally:
                    release.set()
                if mode == "slow":
                    assert finished.wait(timeout=3)
                status, recovered = call(
                    path + "/" + cached["operationId"] + "?socioId=BANK-HU44",
                    headers=partner_headers,
                )
                assert status == 200 and recovered == cached

            scenario[0] = "revoked"
            status, denied = consult()
            assert status == 403 and denied["code"] == "consentimiento_invalido"
            assert (
                call(
                    path + "/" + original["operationId"] + "?socioId=BANK-HU44",
                    headers=partner_headers,
                )[0]
                == 403
            )
            with Session(repo.engine) as session:
                assert (
                    session.scalar(select(func.count()).select_from(ConsultaInformacionModel)) == 3
                )
                audits: list[ConsultaModel] = list(session.scalars(select(ConsultaModel)))
                records = [row.contenido for row in audits]
                assert any(row["resultado"] == "dependencia_timeout" for row in records)
                assert sum(row["resultado"] == "cache" for row in records) == 2
                assert any(row["resultado"] == "consentimiento_no_vigente" for row in records)
                assert "detalle privado" not in json.dumps(records)
            worker_threads = list(info._ejecutor._executor._threads)
            closer = profile_app.extensions["cerrar_recursos_perfilamiento"]
        assert not closer.alive
        assert all(not thread.is_alive() for thread in server_threads + worker_threads)
        assert not stack.servers and not stack.threads
        for port in ports:
            with socket() as connection:
                assert connection.connect_ex(("127.0.0.1", port)) != 0
        record_property("owned_ports_closed", json.dumps(ports))
        record_property("hu44_dependency_budget_ms", 400)
        record_property("hu44_no_default_or_late_persistence", True)
    assert not Path(directory).exists()
    record_property("owned_database_directory_removed", True)
