from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from siniestros.domain import AvisoSiniestro, ErrorSiniestro, PolizaAutorizada, validar_evento


def test_event_normalizes_timezone_and_policy_boundaries() -> None:
    now = datetime.now(UTC)
    policy = PolizaAutorizada(
        id=uuid4(), tipo="automotive", fechaInicio=now.date(), fechaFin=now.date(), estado="vigente"
    )
    draft = AvisoSiniestro(
        polizaId=policy.id,
        fechaEvento=now.astimezone(UTC),
        tipoEvento="Colision",
        descripcion="  Evento reportado por el cliente.  ",
        lugar="Bogota",
    )
    validar_evento(draft, policy, now)
    assert draft.descripcion == "Evento reportado por el cliente."
    assert draft.fecha_ocurrencia.tzinfo == UTC
    with pytest.raises(ErrorSiniestro, match="solicitud_invalida"):
        validar_evento(
            draft.model_copy(update={"fecha_ocurrencia": now + timedelta(seconds=1)}),
            policy,
            now,
        )


@pytest.mark.parametrize("state", ["programada", "vencida"])
def test_inactive_policy_is_rejected(state: str) -> None:
    now = datetime.now(UTC)
    policy = PolizaAutorizada.model_validate(
        {
            "id": uuid4(),
            "tipo": "automotive",
            "fechaInicio": now.date(),
            "fechaFin": now.date(),
            "estado": state,
        }
    )
    draft = AvisoSiniestro(
        polizaId=policy.id,
        fechaEvento=now,
        tipoEvento="Colision",
        descripcion="Evento reportado por el cliente.",
        lugar="Bogota",
    )
    with pytest.raises(ErrorSiniestro, match="poliza_no_vigente"):
        validar_evento(draft, policy, now)


def test_event_date_uses_utc_not_local_day() -> None:
    now = datetime(2026, 11, 5, 1, tzinfo=UTC)
    policy = PolizaAutorizada(
        id=uuid4(), tipo="travel", fechaInicio=now.date(), fechaFin=now.date(), estado="vigente"
    )
    draft = AvisoSiniestro.model_validate(
        {
            "polizaId": policy.id,
            "fechaEvento": "2026-11-04T19:30:00-05:00",
            "tipoEvento": "Accidente",
            "descripcion": "Evento reportado por el cliente.",
            "lugar": "Bogota",
        }
    )
    validar_evento(draft, policy, now)
    assert draft.fecha_ocurrencia.isoformat() == "2026-11-05T00:30:00+00:00"
    with pytest.raises(ErrorSiniestro, match="solicitud_invalida"):
        validar_evento(
            draft.model_copy(update={"fecha_ocurrencia": datetime(2026, 11, 4, 23, tzinfo=UTC)}),
            policy,
            now,
        )
