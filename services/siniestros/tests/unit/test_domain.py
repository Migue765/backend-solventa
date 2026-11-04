from pathlib import Path

import pytest
from pydantic import ValidationError
from siniestros.domain import ClaimInput, Config, UploadInput


@pytest.mark.parametrize("timestamp", [0, "2026-01-01", "2026-01-01T00:00:00"])
def test_capture_timestamp_requires_explicit_zone(timestamp: object) -> None:
    with pytest.raises(ValidationError):
        UploadInput.model_validate(
            {
                "mimeType": "image/jpeg",
                "tamanoBytes": 1,
                "sha256": "0" * 64,
                "fechaCaptura": timestamp,
            }
        )


@pytest.mark.parametrize("size", [0, -1, True, "100"])
def test_size_is_a_positive_integer(size: object) -> None:
    with pytest.raises(ValidationError):
        UploadInput.model_validate(
            {"mimeType": "image/jpeg", "tamanoBytes": size, "sha256": "0" * 64}
        )


def test_claim_normalizes_zone_and_rejects_untrusted_author() -> None:
    payload = {
        "polizaId": "c1b15c66-8696-4f87-8813-b2712b9bc95c",
        "fechaEvento": "2026-01-01T04:00:00-05:00",
        "tipoEvento": "Evento",
        "descripcion": "Descripcion sintetica",
        "lugar": "Lugar",
    }
    assert ClaimInput.model_validate(payload).fechaEvento.hour == 9
    with pytest.raises(ValidationError):
        ClaimInput.model_validate({**payload, "autorId": payload["polizaId"]})


def test_limits_must_be_explicit_valid_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Config("sqlite:///test.db", tmp_path, "synthetic", "http://127.0.0.1:1", max_count=0)
    with pytest.raises(ValueError):
        Config("sqlite:///test.db", tmp_path, "", "http://127.0.0.1:1")
    with pytest.raises(ValueError):
        Config("sqlite:///test.db", Path("relative"), "synthetic", "http://127.0.0.1:1")


@pytest.mark.parametrize(
    "url", ["sqlite://", "sqlite:///:memory:", "sqlite:///file:test?mode=memory"]
)
def test_in_memory_database_cannot_claim_durable_receipt(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="durable"):
        Config(url, tmp_path, "synthetic", "http://127.0.0.1:1")
