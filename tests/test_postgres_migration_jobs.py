import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from socket import socket
from typing import Any

import pytest
import yaml
from psycopg import sql

from scripts import local_database
from scripts.local_web import private_directory

ROOT = Path(__file__).resolve().parents[1]
DSN_KEYS = {
    "identidad": "DATABASE_URL",
    "cotizacion": "DATABASE_URL",
    "perfilamiento": "PERFILAMIENTO_DATABASE_URL",
    "polizas": "POLIZAS_DATABASE_URL",
    "siniestros": "SINIESTROS_DATABASE_URL",
    "notificaciones": "NOTIFICACIONES_DATABASE_URL",
    "asistencias": "ASISTENCIAS_DATABASE_URL",
}


def _container(domain: str, filename: str) -> dict[str, Any]:
    directory = ROOT / "deploy" / "k8s"
    if domain != "cotizacion":
        directory /= domain
    manifest = yaml.safe_load((directory / filename).read_text(encoding="utf-8"))
    container: dict[str, Any] = manifest["spec"]["template"]["spec"]["containers"][0]
    return container


def test_jobs_use_separate_migrator_secrets_without_changing_runtime() -> None:
    for domain, key in DSN_KEYS.items():
        job_env = {entry["name"]: entry for entry in _container(domain, "migrate-job.yaml")["env"]}
        runtime_env = {
            entry["name"]: entry for entry in _container(domain, "deployment.yaml")["env"]
        }
        source = job_env[key]["valueFrom"]["secretKeyRef"]
        assert source == {"name": f"{domain}-migrator", "key": key}, domain
        assert source != runtime_env[key]["valueFrom"]["secretKeyRef"], domain
        if domain != "perfilamiento":
            assert job_env["DB_RUNTIME_ROLE"]["value"] == f"{domain}_runtime"
        else:
            assert job_env["INTERNAL_API_TOKEN"] == runtime_env["INTERNAL_API_TOKEN"]


@pytest.mark.parametrize("domain", [name for name in DSN_KEYS if name != "perfilamiento"])
def test_job_runtime_role_rejects_sqlite_before_creating_tables(
    tmp_path: Path, domain: str
) -> None:
    database = tmp_path / "forbidden.sqlite"
    environment = {
        **os.environ,
        DSN_KEYS[domain]: f"sqlite:///{database}",
        "DB_RUNTIME_ROLE": f"{domain}_runtime",
        "PYTHONPATH": os.pathsep.join(str(ROOT / "services" / name / "src") for name in DSN_KEYS),
    }
    command = _container(domain, "migrate-job.yaml")["command"]
    result = subprocess.run(
        [sys.executable, "-m", *command],
        cwd=ROOT / "services" / domain,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "DB_RUNTIME_ROLE requiere PostgreSQL." in result.stderr
    assert not database.exists()


@pytest.mark.parametrize("identity_schema", [False, True])
def test_real_job_commands_preserve_owner_role_schema_and_restrict_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_schema: bool,
    record_property: Callable[[str, object], None],
) -> None:
    binaries = os.getenv("SOLVENTA_TEST_POSTGRES_BIN")
    if not binaries:
        pytest.skip("Requiere PostgreSQL 17 nativo real; no sustituir por SQLite.")
    directory = tmp_path / "postgres-job"
    private_directory(directory)
    with socket() as free_port:
        free_port.bind(("127.0.0.1", 0))
        port = free_port.getsockname()[1]

    calls = 0

    def execute_jobs(urls: dict[str, str]) -> None:
        nonlocal calls
        calls += 1
        for domain, url in urls.items():
            container = _container(domain, "migrate-job.yaml")
            environment = os.environ.copy()
            for name in {*DSN_KEYS.values(), "DB_SCHEMA", "DB_RUNTIME_ROLE"}:
                environment.pop(name, None)
            for entry in container["env"]:
                if "value" in entry:
                    environment[entry["name"]] = str(entry["value"])
            if not identity_schema:
                environment.pop("DB_SCHEMA", None)
            environment[DSN_KEYS[domain]] = url
            environment["INTERNAL_API_TOKEN"] = secrets.token_urlsafe(32)
            environment["IDENTIDAD_SERVICE_URL"] = "http://127.0.0.1:9"
            environment["PYTHONPATH"] = os.pathsep.join(
                str(ROOT / "services" / name / "src") for name in DSN_KEYS
            )
            command = container["command"]
            if command[0] == "alembic":
                command = [sys.executable, "-m", *command]
            elif command[0] == "python":
                command = [sys.executable, *command[1:]]
            else:
                raise AssertionError("Comando Job inesperado.")
            result = subprocess.run(
                command,
                cwd=ROOT / "services" / domain,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert result.returncode == 0, f"{domain}: {result.stderr}"
            if domain != "perfilamiento":
                history = (
                    "alembic_version_identidad" if domain == "identidad" else "alembic_version"
                )
                with local_database._connect(url) as owner:
                    access = owner.execute(
                        "SELECT has_table_privilege(%s, %s, 'SELECT')",
                        (f"{domain}_runtime", history),
                    ).fetchone()
                    assert access == (False,), (domain, calls, "Job no confirmo REVOKE")
                    if calls == 1:
                        owner.execute(
                            sql.SQL("GRANT ALL ON TABLE {} TO {}").format(
                                sql.Identifier(history), sql.Identifier(f"{domain}_runtime")
                            )
                        )

    # Execute the real Job commands twice, not the bootstrap's own REVOKE implementation.
    monkeypatch.setattr(local_database, "_migrate", execute_jobs)
    with local_database.postgres_cluster(Path(binaries), directory, port) as database:
        total = 0
        for domain, result in database.evidence.items():
            assert isinstance(result, dict)
            assert isinstance(result["domainTables"], int)
            total += result["domainTables"]
            assert result["tableOwnersVerified"] is True
            assert result["ownerNoLogin"] is True
            if domain != "perfilamiento":
                assert result["migrationHistoryRuntimeDenied"] is True
        assert total == 73
        for domain, count, revision, tables in (
            (
                "polizas",
                7,
                "polizas_0004",
                (
                    "renovaciones",
                    "pagos_renovacion",
                    "versiones_poliza",
                    "endosos",
                    "auditoria_polizas",
                    "cancelaciones",
                ),
            ),
            (
                "siniestros",
                18,
                "0008_outbox_notificaciones",
                (
                    "decisiones_siniestro",
                    "asignaciones_siniestro",
                    "reglas_fraude",
                    "evaluaciones_fraude",
                    "casos_revision_fraude",
                    "outbox_notificaciones",
                ),
            ),
        ):
            result = database.evidence[domain]
            assert isinstance(result, dict)
            assert result["domainTables"] == count
            assert result["revisions"] == [revision]
            with local_database._connect(database.runtime_urls[domain]) as runtime:
                for table in tables:
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        granted = runtime.execute(
                            "SELECT has_table_privilege(current_user, %s, %s)",
                            (f"{domain}.{table}", privilege),
                        ).fetchone()
                        assert granted == (True,), (domain, table, privilege)
        record_property("database_evidence", json.dumps(database.evidence, sort_keys=True))
    assert calls == 2
