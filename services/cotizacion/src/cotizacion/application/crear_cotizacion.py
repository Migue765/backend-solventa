from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from cotizacion.application.calculadora_cotizacion import CalculadoraCotizacion
from cotizacion.application.ports import CotizacionRepository
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero


@dataclass(frozen=True)
class CoberturaSolicitada:
    cobertura_id: UUID
    suma_asegurada: Decimal
    deducible: Decimal


@dataclass(frozen=True)
class CrearCotizacionComando:
    cliente_id: UUID
    producto_id: UUID
    perfil_id: str
    moneda: str
    coberturas: tuple[CoberturaSolicitada, ...]


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class CrearCotizacion:
    def __init__(
        self,
        repositorio: CotizacionRepository,
        calculadora: CalculadoraCotizacion,
        reloj: Callable[[], datetime] = _ahora_utc,
    ) -> None:
        self._repositorio = repositorio
        self._calculadora = calculadora
        self._reloj = reloj

    def ejecutar(self, comando: CrearCotizacionComando) -> Cotizacion:
        cotizacion = Cotizacion(
            cliente_id=comando.cliente_id,
            producto_id=comando.producto_id,
            coberturas=[
                CoberturaCotizada(
                    cobertura_id=c.cobertura_id,
                    suma_asegurada=Dinero(c.suma_asegurada, comando.moneda),
                    deducible=Dinero(c.deducible, comando.moneda),
                )
                for c in comando.coberturas
            ],
        )
        self._calculadora.calcular(cotizacion, comando.perfil_id, self._reloj())
        self._repositorio.guardar(cotizacion)
        return cotizacion
