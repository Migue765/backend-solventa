from datetime import date, timedelta

import pytest
from bff_web.domain.product_data import validate_product_data

FLIGHT = {
    "airline": "Aerolínea de prueba",
    "flightNumber": "AV123",
    "flightDate": (date.today() + timedelta(days=1)).isoformat(),
}
WEATHER = {
    "location": "Medellín, Antioquia",
    "weatherEvent": "heavy-rain",
    "startDate": (date.today() + timedelta(days=1)).isoformat(),
    "endDate": (date.today() + timedelta(days=30)).isoformat(),
    "insuredAmount": "10000000",
}


@pytest.mark.parametrize(
    ("product", "data", "changes"),
    [
        ("parametric", FLIGHT, {"coverageType": "baggage"}),
        ("parametric", FLIGHT, {"coverageType": ""}),
        ("parametric", FLIGHT, {"flightDate": date.today().isoformat()}),
        ("parametric", FLIGHT, {"flightDate": "2020-01-01"}),
        ("parametric", FLIGHT, {"flightDate": "2026-02-30"}),
        ("parametric", FLIGHT, {"airline": "  "}),
        ("parametric", FLIGHT, {"flightNumber": "AV-123"}),
        ("parametric-weather", WEATHER, {"location": " \t "}),
        ("parametric-weather", WEATHER, {"location": "x" * 121}),
        ("parametric-weather", WEATHER, {"weatherEvent": "snow"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "0"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "-1"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "1.5"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "01"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "1000000001"}),
        ("parametric-weather", WEATHER, {"insuredAmount": "9" * 5000}),
        ("parametric-weather", WEATHER, {"insuredAmount": 1000}),
        ("parametric-weather", WEATHER, {"startDate": "2026-02-30"}),
        ("parametric-weather", WEATHER, {"startDate": date.today().isoformat()}),
        ("parametric-weather", WEATHER, {"endDate": date.today().isoformat()}),
        ("parametric-weather", WEATHER, {"endDate": "2026-2-3"}),
        (
            "parametric-weather",
            WEATHER,
            {"endDate": (date.today() + timedelta(days=367)).isoformat()},
        ),
    ],
)
def test_rejects_invalid_flight_and_weather_values(
    product: str, data: dict[str, str], changes: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        validate_product_data(product, {**data, **changes})


@pytest.mark.parametrize("amount", ["1", "1000000000"])
def test_accepts_weather_amount_and_inclusive_period_boundaries(amount: str) -> None:
    data = {
        **WEATHER,
        "location": "x" * 120,
        "insuredAmount": amount,
        "endDate": (date.today() + timedelta(days=366)).isoformat(),
    }
    assert validate_product_data("parametric-weather", data) == data


def test_accepts_one_day_weather_period() -> None:
    data = {**WEATHER, "endDate": WEATHER["startDate"]}
    assert validate_product_data("parametric-weather", data) == data


def test_preserves_legacy_flight_data_without_adding_a_coverage() -> None:
    assert validate_product_data("parametric", FLIGHT) == FLIGHT
    assert "coverageType" not in validate_product_data("parametric", FLIGHT)
