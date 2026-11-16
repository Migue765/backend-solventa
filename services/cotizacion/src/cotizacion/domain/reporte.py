import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any


class ReporteInconsistenteError(Exception):
    pass


def corte_desde_texto(valor: str, ahora: datetime) -> datetime:
    corte = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if corte.tzinfo is None or corte.utcoffset() is None:
        raise ValueError("El corte debe incluir zona horaria.")
    corte = corte.astimezone(UTC)
    if corte > ahora:
        raise ValueError("El corte no puede estar en el futuro.")
    return corte


def instante(valor: datetime) -> str:
    normalizado = valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
    return normalizado.isoformat().replace("+00:00", "Z")


def huella(registros: Sequence[dict[str, Any]]) -> str:
    canonico = json.dumps(list(registros), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()
