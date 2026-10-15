import re
from collections.abc import Callable
from datetime import date

from cotizacion.domain.destinos_viaje import CODIGOS_DESTINO_VIAJE

Validador = Callable[[str], bool]


def validar_datos_producto(producto_id: str, datos: object) -> dict[str, str]:
    if not isinstance(datos, dict) or not all(
        isinstance(clave, str) and isinstance(valor, str) for clave, valor in datos.items()
    ):
        raise ValueError("datosProducto debe ser un objeto de pares string/string.")

    validadores = _VALIDADORES.get(producto_id)
    if validadores is None:
        raise ValueError("El producto no admite cotizaciones.")
    if datos.keys() != validadores.keys():
        raise ValueError("datosProducto no contiene exactamente los campos requeridos.")
    if not all(validadores[clave](valor) for clave, valor in datos.items()):
        raise ValueError("datosProducto contiene valores inválidos.")
    if producto_id == "travel" and date.fromisoformat(datos["returnDate"]) < date.fromisoformat(
        datos["departureDate"]
    ):
        raise ValueError("returnDate debe ser igual o posterior a departureDate.")
    return dict(datos)


def _texto(maximo: int) -> Validador:
    return lambda valor: bool(valor.strip()) and len(valor) <= maximo


def _patron(expresion: str) -> Validador:
    patron = re.compile(expresion)
    return lambda valor: patron.fullmatch(valor) is not None


def _fecha_iso(valor: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor) is None:
        return False
    try:
        date.fromisoformat(valor)
    except ValueError:
        return False
    return True


_ENTERO_POSITIVO = _patron(r"[1-9][0-9]*")
_VALIDADORES: dict[str, dict[str, Validador]] = {
    "automotive": {"vehiclePlate": _patron(r"[A-Z]{3}[0-9]{3}")},
    "travel": {
        "destinationCountryCode": lambda valor: valor in CODIGOS_DESTINO_VIAJE,
        "departureDate": _fecha_iso,
        "returnDate": _fecha_iso,
    },
    "life": {"birthDate": _fecha_iso},
    "device-protection": {
        "deviceType": _texto(40),
        "brandModel": _texto(80),
        "purchaseValue": _ENTERO_POSITIVO,
    },
    "parametric": {
        "airline": _texto(80),
        "flightNumber": _patron(r"[A-Za-z0-9]{2,10}"),
        "flightDate": _fecha_iso,
    },
    "payment-protection": {
        "creditType": _texto(60),
        "monthlyPayment": _ENTERO_POSITIVO,
    },
    "mortgage-life": {
        "birthDate": _fecha_iso,
        "mortgageBalance": _ENTERO_POSITIVO,
    },
}
