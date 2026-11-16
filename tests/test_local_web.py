import json
import os
import subprocess
import sys
from pathlib import Path
from socket import socket

import pytest

from scripts.local_web import private_directory


@pytest.mark.parametrize("postgres", [False, True])
def test_bootstrap_from_fresh_directory_closes_services_and_keeps_only_synthetic_data(
    tmp_path: Path,
    postgres: bool,
) -> None:
    binaries = os.getenv("SOLVENTA_TEST_POSTGRES_BIN")
    if postgres and not binaries:
        pytest.skip("Se requiere PostgreSQL 17 nativo real; SQLite no sustituye esta comprobacion.")
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    for name in ("DATABASE_URL", "DB_SCHEMA", "POLIZAS_DATABASE_URL"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = os.pathsep.join(
        str(root / "services" / name / "src")
        for name in (
            "identidad",
            "bff-web",
            "bff-movil",
            "cotizacion",
            "perfilamiento",
            "polizas",
            "siniestros",
        )
    )
    args = [
        sys.executable,
        "-m",
        "scripts.local_web",
        "--check",
        "--web-port",
        "0",
        "--data-dir",
        str(tmp_path / "fresh"),
    ]
    if postgres:
        with socket() as free_port:
            free_port.bind(("127.0.0.1", 0))
            port = free_port.getsockname()[1]
        args += ["--postgres", "--postgres-bin", str(binaries), "--postgres-port", str(port)]
    run = subprocess.run(
        args,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert run.returncode == 0, run.stderr
    ready = json.loads(run.stdout)
    assert ready["ready"] is True
    assert "mobile" not in ready["urls"]
    accounts = json.loads(Path(ready["accountsFile"]).read_text())
    assert accounts["syntheticOnly"] is True
    assert set(accounts["accounts"]) == {"cliente", "socio"}
    assert all(account["contrasena"] not in run.stdout for account in accounts["accounts"].values())
    assert "accessToken" not in run.stdout and "refreshToken" not in run.stdout
    for url in ready["urls"].values():
        with socket() as connection:
            assert connection.connect_ex(("127.0.0.1", int(url.rsplit(":", 1)[1]))) != 0
    if postgres:
        assert ready["databaseMode"] == "postgresql"
        evidence = ready["databaseEvidence"]
        assert len(evidence) == 7
        assert sum(domain["domainTables"] for domain in evidence.values()) == 63
        for name, domain in evidence.items():
            assert domain["database"] == "solventa_prod" and domain["schema"] == name
            assert domain["crud"] and domain["runtimeDdlDenied"]
            assert domain["crossDomainReadsDenied"] == domain["crossDomainWritesDenied"] == 6
            assert domain["migrationCrossDomainReadsDenied"] == 6
            assert domain["migrationCrossDomainDdlDenied"] == 6
            assert domain["ownerNoLogin"] and domain["tableOwnersVerified"]
            assert domain["runtimeNotPrivileged"] and domain["searchPath"] == name
            assert bool(domain["revisions"]) == (name != "perfilamiento")
            assert domain["migrationHistoryRuntimeDenied"] == (name != "perfilamiento")
        connections = json.loads((tmp_path / "fresh" / "postgres" / "connections.json").read_text())
        assert all(url not in run.stdout for url in connections["runtime"].values())
        with socket() as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0
        assert not list((tmp_path / "fresh").glob("*.db"))
    else:
        assert ready["databaseMode"] == "sqlite-legacy"
        assert (tmp_path / "fresh" / "identidad.db").is_file()


def test_existing_data_is_not_reused_or_overwritten(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        private_directory(tmp_path)


@pytest.mark.parametrize("missing_binaries", [False, True])
def test_postgres_requires_explicit_mode_and_never_falls_back_to_sqlite(
    tmp_path: Path, missing_binaries: bool
) -> None:
    args = [
        sys.executable,
        "-m",
        "scripts.local_web",
        "--postgres",
        "--data-dir",
        str(tmp_path / "bad"),
    ]
    if missing_binaries:
        args += ["--postgres-bin", str(tmp_path / "no-postgresql-here")]
    run = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if missing_binaries:
        assert run.returncode == 1
        assert "FileNotFoundError" in run.stderr
        assert not list((tmp_path / "bad").glob("*.db"))
        assert not (tmp_path / "bad" / "accounts.json").exists()
    else:
        assert run.returncode == 2
        assert "no hay fallback SQLite" in run.stderr
        assert not (tmp_path / "bad").exists()
