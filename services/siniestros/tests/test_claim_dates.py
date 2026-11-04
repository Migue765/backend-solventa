from datetime import UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError
from siniestros.domain import ClaimInput


def test_event_normalizes_timezone_and_whitespace() -> None:
    draft = ClaimInput.model_validate(
        {
            "polizaId": uuid4(),
            "fechaEvento": "2026-11-04T19:30:00-05:00",
            "tipoEvento": "Colision",
            "descripcion": "  Evento reportado por el cliente.  ",
            "lugar": "Bogota",
        }
    )
    assert draft.descripcion == "Evento reportado por el cliente."
    assert draft.fechaEvento.tzinfo == UTC
    assert draft.fechaEvento.isoformat() == "2026-11-05T00:30:00+00:00"


@pytest.mark.parametrize("timestamp", ["2026-11-04T08:00:00", 1793799100])
def test_event_requires_iso_timestamp_with_explicit_zone(timestamp: object) -> None:
    with pytest.raises(ValidationError):
        ClaimInput.model_validate(
            {
                "polizaId": uuid4(),
                "fechaEvento": timestamp,
                "tipoEvento": "Colision",
                "descripcion": "Evento reportado por el cliente.",
                "lugar": "Bogota",
            }
        )
