import json
import os
import subprocess
import sys
from pathlib import Path
from socket import socket

import pytest

from scripts.local_web import private_directory


def test_bootstrap_from_fresh_directory_closes_services_and_keeps_only_synthetic_data(
    tmp_path: Path,
) -> None:
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
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.local_web",
            "--check",
            "--web-port",
            "0",
            "--data-dir",
            str(tmp_path / "fresh"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
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
    assert (tmp_path / "fresh" / "identidad.db").is_file()


def test_existing_data_is_not_reused_or_overwritten(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        private_directory(tmp_path)
