"""Local web acceptance with fresh synthetic data; never connects to AWS."""

import argparse
import csv
import json
import os
import secrets
import signal
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

from tests.jmeter.local_stack import LocalStack
from tests.test_encrypted_local_stack import call, encrypted_login_payload


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        sid = next(csv.reader(identity.stdout.splitlines()))[1]
        if not sid.startswith("S-1-"):
            raise RuntimeError("No se pudo identificar al propietario del directorio local.")
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True,
            capture_output=True,
        )


def require(response: tuple[int, object], status: int, label: str) -> dict[str, object]:
    actual, body = response
    if actual != status or not isinstance(body, dict):
        raise RuntimeError(f"{label}: se esperaba HTTP {status}; recibido {actual}.")
    return body


def seed_and_verify(stack: LocalStack) -> dict[str, dict[str, str]]:
    web, identity = stack.urls["web"], stack.urls["identity"]
    accounts = {
        role: {
            "correo": f"local-{role}@example.com",
            "contrasena": secrets.token_urlsafe(24) + "Aa1!",
        }
        for role in ("cliente", "socio")
    }
    require(
        call(
            identity + "/v1/usuarios",
            {
                **accounts["cliente"],
                "nombre": "Cliente sintetico local",
                "tipoDocumento": "cc",
                "numeroDocumento": "1098765432",
                "telefono": "+573000000001",
                "fechaNacimiento": "1990-01-01",
            },
        ),
        201,
        "Alta sintetica directa en Identidad",
    )
    partner = accounts["socio"]
    created = (
        stack.apps["identity"]
        .test_cli_runner()
        .invoke(
            args=[
                "create-partner-user",
                "--name",
                "Socio sintetico local",
                "--email",
                partner["correo"],
                "--partner-id",
                "LOCAL-WEB",
                "--partner-name",
                "Organizacion sintetica local",
            ],
            input=f"{partner['contrasena']}\n{partner['contrasena']}\n",
        )
    )
    if created.exit_code != 0:
        raise RuntimeError("No se pudo crear el socio sintetico con el CLI existente.")
    auth: dict[str, dict[str, str]] = {}
    client_id: object = None
    for role, account in accounts.items():
        challenge = require(call(web + "/v1/sesiones/desafio"), 200, "Desafio JWE")
        payload = encrypted_login_payload(challenge, account["correo"], account["contrasena"])
        session = require(call(web + "/v1/sesiones", payload), 200, "Login JWE")
        user, token = session.get("usuario"), session.get("accessToken")
        if not isinstance(user, dict) or user.get("rol") != role or not isinstance(token, str):
            raise RuntimeError("Identidad no devolvio el rol/token esperado.")
        auth[role] = {"Authorization": "Bearer " + token}
        if role == "cliente":
            client_id = user.get("id")
        require(call(web + "/v1/sesiones", payload), 400, "Rechazo de replay")
    require(call(web + "/v1/sesiones", {**accounts["cliente"]}), 400, "Rechazo de plaintext")
    require(
        call(
            web + "/v1/consentimientos/perfilamiento",
            {
                "autorizado": True,
                "version": 0,
                "solicitud": {
                    "finalidad": "cotizacion",
                    "alcances": ["open_finance", "open_data"],
                    "fuentes": ["open_finance", "open_data"],
                    "datos": ["indicador_riesgo"],
                    "versionTerminos": "1.0",
                    "vigenteHasta": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                },
            },
            auth["cliente"],
            method="PUT",
        ),
        200,
        "Consentimiento exclusivamente sintetico",
    )
    portal = web + "/v1/socios/actual"
    require(call(portal), 401, "Portal sin JWT")
    require(call(portal, headers=auth["cliente"]), 403, "Portal denegado al cliente")
    require(call(portal, headers=auth["socio"]), 200, "Portal del socio")
    lookup = web + "/v1/socios/clientes/por-documento?socioId=LOCAL-WEB"
    require(call(lookup, headers=auth["socio"]), 400, "Documento vacio")
    require(
        call(lookup + "&tipoDocumento=cc&numeroDocumento=1098765432", headers=auth["socio"]),
        200,
        "Consulta del cliente sintetico",
    )
    operation = str(uuid4())
    require(
        call(
            web + "/v1/socios/solicitudes-cotizacion",
            {
                "operationId": operation,
                "socioId": "LOCAL-WEB",
                "productoId": "mortgage-life",
                "datosProducto": {
                    "mortgageBalance": "100000000",
                    "referenciaCredito": "LOCAL-001",
                },
                "cliente": {
                    "tipoCliente": "persona_natural",
                    "tipoDocumento": "cc",
                    "numeroDocumento": "1098765432",
                },
            },
            {**auth["socio"], "Idempotency-Key": operation},
        ),
        201,
        "Cotizacion sintetica",
    )
    require(
        call(
            stack.urls["policies"] + "/v1/polizas",
            {
                "clienteId": client_id,
                "suscripcionId": str(uuid4()),
                "pagoId": str(uuid4()),
                "tipo": "automotive",
                "confirmadaEn": datetime.now(UTC).isoformat(),
                "simulada": True,
                "fechasContratadas": None,
            },
            {"Authorization": "Bearer " + stack.internal_token},
        ),
        200,
        "Poliza academica sintetica",
    )
    require(
        call(web + "/v1/siniestros/polizas", headers=auth["cliente"]),
        200,
        "Polizas del cliente",
    )
    return accounts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=int, default=5001)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--postgres", action="store_true", help="Use one fresh native PostgreSQL DB."
    )
    parser.add_argument(
        "--postgres-bin", type=Path, help="Verified PostgreSQL 17 Windows bin directory."
    )
    parser.add_argument("--postgres-port", type=int, default=55417)
    parser.add_argument(
        "--check", action="store_true", help="Verify startup and stop owned services."
    )
    args = parser.parse_args()
    if args.postgres != (args.postgres_bin is not None):
        parser.error("--postgres y --postgres-bin deben usarse juntos; no hay fallback SQLite.")
    if not 1 <= args.postgres_port <= 65535:
        parser.error("--postgres-port debe estar entre 1 y 65535.")
    if any(os.getenv(name) for name in ("DATABASE_URL", "DB_SCHEMA", "POLIZAS_DATABASE_URL")):
        raise RuntimeError("Elimina las variables de bases externas antes de arrancar.")
    directory = (args.data_dir or Path("reports") / f"local-web-{uuid4().hex}").resolve()
    private_directory(directory)
    stopped = Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    with ExitStack() as resources:
        databases = None
        database_evidence: dict[str, object] | None = None
        if args.postgres:
            from scripts.local_database import postgres_cluster

            database = resources.enter_context(
                postgres_cluster(args.postgres_bin.resolve(), directory, args.postgres_port)
            )
            databases, database_evidence = database.runtime_urls, database.evidence
        stack = resources.enter_context(
            LocalStack(
                directory,
                args.web_port,
                mobile_port=None,
                encrypted_login_enabled=True,
                prepared_databases=databases,
            )
        )
        accounts = seed_and_verify(stack)
        fixture = directory / "accounts.json"
        with os.fdopen(os.open(fixture, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as out:
            json.dump({"syntheticOnly": True, "accounts": accounts}, out, indent=2)
        print(
            json.dumps(
                {
                    "ready": True,
                    "pid": os.getpid(),
                    "urls": stack.urls,
                    "accountsFile": str(fixture),
                    "syntheticOnly": True,
                    "databaseMode": "postgresql" if args.postgres else "sqlite-legacy",
                    "databaseEvidence": database_evidence,
                    "postgres": (
                        {
                            "host": "127.0.0.1",
                            "port": args.postgres_port,
                            "database": "solventa_prod",
                            "pid": int(
                                (directory / "postgres" / "data" / "postmaster.pid")
                                .read_text()
                                .splitlines()[0]
                            ),
                            "dataDirectory": str(directory / "postgres" / "data"),
                        }
                        if args.postgres
                        else None
                    ),
                    "clientDocument": "1098765432",
                    "partnerId": "LOCAL-WEB",
                    "limitations": [
                        "fakeredis; OpenFinance/OpenData FuenteMock",
                        "HTTP local directo al BFF; no API Gateway/TLS/WAF/MultiAZ/DR",
                        *([] if args.postgres else ["SQLite legacy no cumple BD unica"]),
                        "Poliza/pago academicos; no proveedores reales",
                        "Registro JWE publico requiere integrar PR114",
                        "Sin BFF movil/Asistencias/Notificaciones",
                    ],
                }
            ),
            flush=True,
        )
        if not args.check:
            stopped.wait()


if __name__ == "__main__":
    main()
