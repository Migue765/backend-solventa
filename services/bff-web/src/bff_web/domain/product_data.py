import re
from collections.abc import Callable
from datetime import date

Validator = Callable[[str], bool]


def validate_product_data(
    product_id: str, data: object, *, for_creation: bool = True
) -> dict[str, str]:
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError("datosProducto must contain only string/string pairs.")

    validators = _VALIDATORS.get(product_id)
    if validators is None:
        raise ValueError("The product does not support quote requests.")
    optional = _OPTIONAL.get(product_id, {})
    if not validators.keys() <= data.keys() <= validators.keys() | optional.keys():
        raise ValueError("datosProducto must contain exactly the required fields.")
    allowed = validators | optional
    if not all(allowed[key](value) for key, value in data.items()):
        raise ValueError("datosProducto contains invalid values.")
    if product_id == "travel" and date.fromisoformat(data["returnDate"]) < date.fromisoformat(
        data["departureDate"]
    ):
        raise ValueError("returnDate must be on or after departureDate.")
    if (
        for_creation
        and product_id == "parametric"
        and date.fromisoformat(data["flightDate"]) <= date.today()
    ):
        raise ValueError("flightDate debe ser posterior a hoy.")
    if product_id == "parametric-weather":
        start = date.fromisoformat(data["startDate"])
        end = date.fromisoformat(data["endDate"])
        if end < start or (end - start).days >= 366 or (for_creation and start <= date.today()):
            raise ValueError("El periodo debe ser futuro, ordenado y de hasta 366 días inclusive.")
    return dict(data)


def _text(maximum: int) -> Validator:
    return lambda value: bool(value.strip()) and len(value) <= maximum


def _pattern(expression: str) -> Validator:
    pattern = re.compile(expression)
    return lambda value: pattern.fullmatch(value) is not None


def _iso_date(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


_POSITIVE_INTEGER = _pattern(r"[1-9][0-9]{0,15}")
_VALIDATORS: dict[str, dict[str, Validator]] = {
    "automotive": {"vehiclePlate": _pattern(r"[A-Z]{3}[0-9]{3}")},
    "travel": {
        "destinationCountryCode": _pattern(r"[A-Z]{2}"),
        "departureDate": _iso_date,
        "returnDate": _iso_date,
    },
    "life": {"birthDate": _iso_date},
    "device-protection": {
        "deviceType": _text(40),
        "brandModel": _text(80),
        "purchaseValue": _POSITIVE_INTEGER,
    },
    "parametric": {
        "airline": _text(80),
        "flightNumber": _pattern(r"[A-Za-z0-9]{2,10}"),
        "flightDate": _iso_date,
    },
    "parametric-weather": {
        "location": _text(120),
        "weatherEvent": lambda value: value in {"heavy-rain", "drought"},
        "startDate": _iso_date,
        "endDate": _iso_date,
        "insuredAmount": lambda value: (
            len(value) <= 10 and _POSITIVE_INTEGER(value) and int(value) <= 1_000_000_000
        ),
    },
    "payment-protection": {
        "creditType": _text(60),
        "monthlyPayment": _POSITIVE_INTEGER,
    },
    "mortgage-life": {
        "birthDate": _iso_date,
        "mortgageBalance": _POSITIVE_INTEGER,
    },
}

_OPTIONAL: dict[str, dict[str, Validator]] = {
    "parametric": {
        "coverageType": lambda value: value
        in {"delay-cancellation", "missed-connection", "comprehensive"},
    },
}
