from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from polizas.domain.emision import EvidenciaEmision, estado_vigencia
from pydantic import ValidationError


def evidencia(**cambios: object) -> dict[str, object]:
    return {
        "clienteId": str(uuid4()),
        "suscripcionId": str(uuid4()),
        "pagoId": str(uuid4()),
        "tipo": "automotive",
        "confirmadaEn": "2026-01-31T23:00:00Z",
        "simulada": True,
        "fechasContratadas": None,
        **cambios,
    }


@pytest.mark.parametrize(
    "tipo",
    [
        "automotive",
        "life",
        "device-protection",
        "payment-protection",
        "mortgage-life",
    ],
)
@pytest.mark.parametrize(
    ("confirmacion", "fin"),
    [
        ("2026-01-31T23:00:00Z", date(2026, 2, 28)),
        ("2028-01-31T23:00:00Z", date(2028, 2, 29)),
        ("2026-12-31T23:00:00Z", date(2027, 1, 31)),
        ("2026-03-31T23:30:00-05:00", date(2026, 5, 1)),
    ],
)
def test_mes_calendario_autorizado(tipo: str, confirmacion: str, fin: date) -> None:
    datos = EvidenciaEmision.model_validate(evidencia(tipo=tipo, confirmadaEn=confirmacion))
    assert datos.vigencia()[1] == fin


@pytest.mark.parametrize("tipo", ["travel", "parametric", "parametric-weather"])
def test_conserva_fechas_contratadas(tipo: str) -> None:
    fin = "2026-12-01" if tipo == "parametric" else "2026-12-07"
    datos = EvidenciaEmision.model_validate(
        evidencia(
            tipo=tipo,
            fechasContratadas={"inicio": "2026-12-01", "fin": fin},
        )
    )
    assert datos.vigencia() == (date(2026, 12, 1), date.fromisoformat(fin))


@pytest.mark.parametrize(
    "cambios",
    [
        {"simulada": False},
        {"simulada": 1},
        {"tipo": "parametric", "fechasContratadas": {"inicio": "2026-12-01", "fin": "2026-12-07"}},
        {"confirmadaEn": "2026-01-01T00:00:00"},
        {"tipo": "desconocido"},
        {"tipo": "travel"},
        {"fechasContratadas": {"inicio": "2026-01-01", "fin": "2026-01-02"}},
        {"tipo": "travel", "fechasContratadas": {"inicio": "2026-12-07", "fin": "2026-12-01"}},
    ],
)
def test_rechaza_evidencia_invalida(cambios: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenciaEmision.model_validate(evidencia(**cambios))


@pytest.mark.parametrize(
    ("dia", "esperado"),
    [
        (1, "programada"),
        (2, "vigente"),
        (3, "vigente"),
        (4, "vencida"),
    ],
)
def test_estado_incluye_ambas_fechas(dia: int, esperado: str) -> None:
    assert (
        estado_vigencia(date(2026, 1, 2), date(2026, 1, 3), datetime(2026, 1, dia, tzinfo=UTC))
        == esperado
    )
