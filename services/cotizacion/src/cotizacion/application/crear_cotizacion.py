from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from cotizacion.application.ports import CotizacionRepository, Tarificador
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
    moneda: str
    coberturas: tuple[CoberturaSolicitada, ...]


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class CrearCotizacion:
    def __init__(
        self,
        repositorio: CotizacionRepository,
        tarificador: Tarificador,
        vigencia: timedelta,
        reloj: Callable[[], datetime] = _ahora_utc,
    ) -> None:
        self._repositorio = repositorio
        self._tarificador = tarificador
        self._vigencia = vigencia
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
        primas = {
            c.cobertura_id: self._tarificador.calcular_prima(cotizacion.producto_id, c)
            for c in cotizacion.coberturas
        }
        cotizacion.calcular(primas, self._reloj(), self._vigencia)
        self._repositorio.guardar(cotizacion)
        return cotizacion
