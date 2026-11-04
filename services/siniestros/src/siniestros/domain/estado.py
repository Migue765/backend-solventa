"""Capacidad de consulta de estado compartida por HU21 (movil) y HU25 (Web).

Sigue el agregado Siniestro del modelo de dominio: tipo asistido o parametrico,
ocurrencia del evento, reporte y estado vigente con su historial.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

TIPOS = frozenset({"asistido", "parametrico"})
DESCRIPCIONES = {
    "reportado": "Recibimos el aviso de tu siniestro.",
    "en_evaluacion": "Estamos evaluando la informacion del caso.",
    "en_revision": "Un analista esta revisando tu caso.",
    "en_inspeccion": "El caso fue asignado para inspeccion.",
    "aprobado": "El siniestro fue aprobado.",
    "rechazado": "El siniestro no fue aprobado.",
    "pagado": "El pago del siniestro fue realizado.",
    "cerrado": "El caso fue cerrado.",
}


class SiniestroNoIntegro(Exception):
    """Lo persistido no permite afirmar un estado vigente."""


@dataclass(frozen=True)
class CambioEstado:
    estado: str
    ocurrido_en: datetime
    visible_cliente: bool


@dataclass(frozen=True)
class RegistroSiniestro:
    siniestro_id: UUID
    poliza_id: UUID
    tipo: str | None
    estado: str
    ocurrido_en: datetime
    reportado_en: datetime
    actualizado_en: datetime


@dataclass(frozen=True)
class EstadoConsultado:
    siniestro: RegistroSiniestro
    historial: tuple[CambioEstado, ...]


def consultar_estado(
    registro: RegistroSiniestro, cambios: Sequence[CambioEstado]
) -> EstadoConsultado:
    """Valida la coherencia del registro y conserva solo el historial permitido al cliente.

    El cambio que fija el estado vigente siempre se muestra; los demas solo si son visibles.
    """
    siniestro = replace(
        registro,
        ocurrido_en=_utc(registro.ocurrido_en),
        reportado_en=_utc(registro.reportado_en),
        actualizado_en=_utc(registro.actualizado_en),
    )
    if siniestro.tipo is not None and siniestro.tipo not in TIPOS:
        raise SiniestroNoIntegro("El siniestro tiene un tipo desconocido.")
    if siniestro.ocurrido_en > siniestro.reportado_en:
        raise SiniestroNoIntegro("El evento no puede ocurrir despues del reporte.")
    if not cambios:
        raise SiniestroNoIntegro("El siniestro no tiene historial.")
    if any(cambio.estado not in DESCRIPCIONES for cambio in cambios):
        raise SiniestroNoIntegro("El historial contiene un estado desconocido.")
    primero, ultimo = cambios[0], cambios[-1]
    if primero.estado != "reportado" or _utc(primero.ocurrido_en) != siniestro.reportado_en:
        raise SiniestroNoIntegro("El historial no inicia con el reporte.")
    if ultimo.estado != siniestro.estado or _utc(ultimo.ocurrido_en) != siniestro.actualizado_en:
        raise SiniestroNoIntegro("El estado vigente no coincide con el historial.")
    fechas = [_utc(cambio.ocurrido_en) for cambio in cambios]
    if fechas != sorted(fechas):
        raise SiniestroNoIntegro("El historial no es cronologico.")
    permitido = tuple(
        CambioEstado(cambio.estado, _utc(cambio.ocurrido_en), True)
        for indice, cambio in enumerate(cambios)
        if cambio.visible_cliente or indice == len(cambios) - 1
    )
    return EstadoConsultado(siniestro, permitido)


def _utc(valor: datetime) -> datetime:
    # SQLite devuelve instantes sin zona aunque la columna sea timezone=True.
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
