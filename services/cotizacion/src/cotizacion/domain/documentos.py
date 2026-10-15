import re

_PATRONES = {
    "cc": re.compile(r"[0-9]{6,10}"),
    "ce": re.compile(r"[0-9]{6,12}"),
    "passport": re.compile(r"[A-Z0-9]{5,16}"),
    "ppt": re.compile(r"[0-9]{7,10}"),
}


def validar_identificacion(tipo_documento: str, identificacion: str) -> str:
    patron = _PATRONES.get(tipo_documento)
    if patron is None or patron.fullmatch(identificacion) is None:
        raise ValueError("La identificación no corresponde al tipo de documento.")
    return identificacion
