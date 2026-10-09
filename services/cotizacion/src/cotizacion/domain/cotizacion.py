from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID, uuid4

from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError


class EstadoCotizacion(str, Enum):
    PENDIENTE = "PENDIENTE"
    CALCULADA = "CALCULADA"


@dataclass
class CoberturaCotizada:
    cobertura_id: UUID
    suma_asegurada: Dinero
    deducible: Dinero
    prima: Dinero | None = None

    def __post_init__(self) -> None:
        if self.suma_asegurada.monto <= 0:
            raise ReglaDeNegocioError("La suma asegurada debe ser mayor a cero.")
        if self.deducible.moneda != self.suma_asegurada.moneda:
            raise ReglaDeNegocioError("El deducible y la suma asegurada deben estar en la misma moneda.")
        if self.deducible.monto > self.suma_asegurada.monto:
            raise ReglaDeNegocioError("El deducible no puede superar la suma asegurada.")


@dataclass
class Cotizacion:
    """Raíz de agregado: gobierna la consistencia de sus coberturas y su prima."""

    cliente_id: UUID
    producto_id: UUID
    coberturas: list[CoberturaCotizada]
    cotizacion_id: UUID = field(default_factory=uuid4)
    estado: EstadoCotizacion = EstadoCotizacion.PENDIENTE
    prima_estimada: Dinero | None = None
    valida_hasta: datetime | None = None

    def __post_init__(self) -> None:
        if not self.coberturas:
            raise ReglaDeNegocioError("La cotización debe incluir al menos una cobertura.")
        ids = [c.cobertura_id for c in self.coberturas]
        if len(ids) != len(set(ids)):
            raise ReglaDeNegocioError("Una cobertura no puede repetirse en la misma cotización.")
        if len({c.suma_asegurada.moneda for c in self.coberturas}) > 1:
            raise ReglaDeNegocioError("Todas las coberturas deben estar en la misma moneda.")

    @property
    def moneda(self) -> str:
        return self.coberturas[0].suma_asegurada.moneda

    def calcular(self, primas: dict[UUID, Dinero], ahora: datetime, vigencia: timedelta) -> None:
        if self.estado is not EstadoCotizacion.PENDIENTE:
            raise ReglaDeNegocioError("Solo se puede calcular una cotización pendiente.")
        for cobertura in self.coberturas:
            cobertura.prima = primas[cobertura.cobertura_id]
        self.prima_estimada = sum((c.prima for c in self.coberturas), start=Dinero.cero(self.moneda))
        self.valida_hasta = ahora + vigencia
        self.estado = EstadoCotizacion.CALCULADA
