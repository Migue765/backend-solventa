import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from siniestros.domain import ClaimInput, Config, Problem
from siniestros.models import Base
from siniestros.policies import policies
from siniestros.repository import Repository


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Repository]:
    repo = Repository(
        Config(
            database_url=f"sqlite:///{tmp_path / 'claims.db'}",
            storage_root=tmp_path / "private",
            internal_token="synthetic-internal",
            policies_url="http://127.0.0.1:1",
        )
    )
    Base.metadata.create_all(repo.engine)
    try:
        yield repo
    finally:
        repo.engine.dispose()


def draft(instant: str) -> ClaimInput:
    return ClaimInput.model_validate(
        {
            "polizaId": str(uuid4()),
            "fechaEvento": instant,
            "tipoEvento": "Colision",
            "descripcion": "  Evento reportado por el cliente.  ",
            "lugar": "Bogota",
        }
    )


def test_event_normalizes_timezone_and_policy_boundaries(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = datetime.now(UTC)
    value = draft(instant.isoformat())
    policy = {
        "id": str(value.polizaId),
        "fechaInicio": instant.date().isoformat(),
        "fechaFin": instant.date().isoformat(),
        "estado": "vigente",
    }
    monkeypatch.setattr("siniestros.repository.policies", lambda *_: [policy])
    assert value.descripcion == "Evento reportado por el cliente."
    assert value.fechaEvento.tzinfo == UTC
    assert repository.create_claim("owner", str(uuid4()), value, "cid")[1] == 201
    with pytest.raises(Problem, match="fecha_evento_invalida"):
        repository.create_claim(
            "owner",
            str(uuid4()),
            value.model_copy(update={"fechaEvento": datetime.now(UTC) + timedelta(days=1)}),
            "cid",
        )


@pytest.mark.parametrize("state", ["programada", "vencida"])
def test_inactive_policy_is_rejected(
    state: str,
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = draft(datetime.now(UTC).isoformat())
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(
        {
            "polizas": [
                {
                    "id": str(value.polizaId),
                    "tipo": "automotive",
                    "fechaInicio": "2020-01-01",
                    "fechaFin": "2099-12-31",
                    "estado": state,
                }
            ]
        }
    ).encode()
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr("siniestros.policies.build_opener", lambda *_: opener)
    assert policies(repository.config, "owner", "cid") == []
    with pytest.raises(Problem, match="poliza_no_encontrada"):
        repository.create_claim("owner", str(uuid4()), value, "cid")
    assert repository.list_claims("owner") == []


def test_event_date_uses_utc_not_local_day(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = datetime(2026, 11, 5, 1, tzinfo=UTC)
    value = draft("2026-11-04T19:30:00-05:00")
    monkeypatch.setattr(
        "siniestros.repository.datetime",
        SimpleNamespace(now=lambda _: instant, fromisoformat=datetime.fromisoformat),
    )
    monkeypatch.setattr(
        "siniestros.repository.policies",
        lambda *_: [
            {
                "id": str(value.polizaId),
                "fechaInicio": "2026-11-05",
                "fechaFin": "2026-11-05",
            }
        ],
    )
    assert value.fechaEvento.isoformat() == "2026-11-05T00:30:00+00:00"
    assert repository.create_claim("owner", str(uuid4()), value, "cid")[1] == 201
    with pytest.raises(Problem, match="fecha_evento_invalida"):
        repository.create_claim(
            "owner",
            str(uuid4()),
            value.model_copy(update={"fechaEvento": datetime(2026, 11, 4, 23, tzinfo=UTC)}),
            "cid",
        )


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"polizas": {}},
        {"polizas": [{"estado": "vigente"}]},
        {"polizas": [{"estado": "vencida"}]},
    ],
)
def test_invalid_policy_response_is_not_an_authorization(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
    document: object,
) -> None:
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(document).encode()
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr("siniestros.policies.build_opener", lambda *_: opener)
    with pytest.raises(Problem, match="servicio_no_disponible"):
        policies(repository.config, "owner", "cid")
