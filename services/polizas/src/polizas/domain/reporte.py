import hashlib
import json
from datetime import UTC, datetime


def corte_desde_texto(valor: str, ahora: datetime) -> datetime:
    corte = datetime.fromisoformat(valor)
    if corte.tzinfo is None:
        raise ValueError("El corte requiere zona horaria.")
    corte = corte.astimezone(UTC)
    if corte > ahora:
        raise ValueError("El corte no puede ser futuro.")
    return corte


def instante(valor: datetime) -> str:
    return valor.astimezone(UTC).isoformat().replace("+00:00", "Z")


def huella(registros: object) -> str:
    canonico = json.dumps(registros, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()
