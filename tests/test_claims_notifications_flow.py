"""HU22: Siniestros publica sus cambios en Notificaciones por el contrato interno real."""

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.request import Request

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from flask.testing import FlaskClient
from notificaciones.app import create_app as create_notifications_app
from siniestros.app import create_app as create_claims_app
from siniestros.domain import Config
from siniestros.infrastructure import notificaciones as claims_notifications

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "token-interno-hu22"
INTERNAL = {"Authorization": f"Bearer {TOKEN}"}
SOURCE = "ideam"


class ForwardedResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@pytest.fixture
def notifications(tmp_path: Path) -> Iterator[Flask]:
    app = create_notifications_app(
        f"sqlite:///{tmp_path / 'notificaciones.db'}", TOKEN, create_schema=True
    )
    yield app
    app.extensions["engine"].dispose()


@pytest.fixture
def claims(
    tmp_path: Path, notifications: Flask, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Flask]:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migrations = AlembicConfig(str(ROOT / "services" / "siniestros" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    target: FlaskClient = notifications.test_client()

    def forward(request: Request, timeout: float) -> ForwardedResponse:
        assert request.full_url == "http://notificaciones.test/v1/eventos"
        assert timeout > 0
        response = target.post(
            "/v1/eventos",
            data=request.data,
            headers=dict(request.header_items()),
        )
        return ForwardedResponse(response.status_code, response.get_data())

    monkeypatch.setattr(claims_notifications, "urlopen", forward)
    app = create_claims_app(
        Config(
            url,
            tmp_path / "evidencias",
            TOKEN,
            "http://polizas.test",
            parametric_sources={SOURCE: "fuente-segura"},
            notifications_url="http://notificaciones.test",
        )
    )
    yield app
    app.extensions["siniestros_engine"].dispose()


def test_parametric_claim_and_payment_reach_client_notifications(
    claims: Flask, notifications: Flask
) -> None:
    client = claims.test_client()
    client_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    rule = {
        "reglaId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "version": 1,
        "fuente": SOURCE,
        "tipoEvento": "lluvia_acumulada",
        "metrica": "precipitacion",
        "operador": "gte",
        "umbral": 50.0,
        "unidad": "mm",
        "ubicacionCodigo": "CO-ANT-001",
        "clienteId": client_id,
        "polizaId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "beneficiarioId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "montoIndemnizacion": "250000.00",
        "moneda": "COP",
        "vigenteDesde": "2026-01-01T00:00:00Z",
        "vigenteHasta": "2027-01-01T00:00:00Z",
    }
    event = {
        "eventoId": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "ocurridoEn": "2026-11-08T12:00:00+00:00",
        "tipoEvento": "lluvia_acumulada",
        "ubicacionCodigo": "CO-ANT-001",
        "metrica": {"nombre": "precipitacion", "valor": 80.0, "unidad": "mm"},
    }
    assert (
        client.post("/internal/v1/reglas-parametricas", headers=INTERNAL, json=rule).status_code
        == 201
    )
    assert (
        client.post(
            "/internal/v1/eventos-parametricos/recepcion",
            headers={"Authorization": "Bearer fuente-segura", "X-Fuente-ID": SOURCE},
            json={"eventos": [event]},
        ).status_code
        == 202
    )
    limit = {"limite": 10}
    client.post("/internal/v1/eventos-parametricos/procesamiento", headers=INTERNAL, json=limit)
    paid = client.post(
        "/internal/v1/pagos-parametricos/procesamiento", headers=INTERNAL, json=limit
    )
    assert paid.get_json()["confirmados"] == 1

    published = client.post("/internal/v1/notificaciones/publicacion", headers=INTERNAL, json=limit)
    replay = client.post("/internal/v1/notificaciones/publicacion", headers=INTERNAL, json=limit)

    assert published.status_code == 200
    assert published.get_json() == {"publicadas": 3, "fallidas": 0}
    assert replay.get_json() == {"publicadas": 0, "fallidas": 0}
    inbox = notifications.test_client().get(
        "/v1/notificaciones", headers={**INTERNAL, "X-Cliente-ID": client_id}
    )
    assert inbox.status_code == 200
    kinds = sorted(item["tipo"] for item in inbox.get_json()["notificaciones"])
    assert kinds == ["pagos", "siniestros", "siniestros"]
