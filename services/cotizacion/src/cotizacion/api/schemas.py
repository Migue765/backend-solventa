from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cotizacion.application.crear_cotizacion import CoberturaSolicitada, CrearCotizacionComando
from cotizacion.domain.cotizacion import Cotizacion
from cotizacion.domain.dinero import Dinero


class _Solicitud(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class CoberturaRequest(_Solicitud):
    cobertura_id: UUID
    suma_asegurada: Decimal = Field(gt=0)
    deducible: Decimal = Field(default=Decimal("0"), ge=0)


class CrearCotizacionRequest(_Solicitud):
    cliente_id: UUID
    producto_id: UUID
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    coberturas: list[CoberturaRequest] = Field(min_length=1)

    def a_comando(self) -> CrearCotizacionComando:
        return CrearCotizacionComando(
            cliente_id=self.cliente_id,
            producto_id=self.producto_id,
            moneda=self.moneda,
            coberturas=tuple(
                CoberturaSolicitada(c.cobertura_id, c.suma_asegurada, c.deducible) for c in self.coberturas
            ),
        )


def _dinero(dinero: Dinero) -> dict:
    return {"monto": str(dinero.monto), "moneda": dinero.moneda}


def cotizacion_a_json(cotizacion: Cotizacion) -> dict:
    return {
        "cotizacionId": str(cotizacion.cotizacion_id),
        "clienteId": str(cotizacion.cliente_id),
        "productoId": str(cotizacion.producto_id),
        "estado": cotizacion.estado.value,
        "primaEstimada": _dinero(cotizacion.prima_estimada),
        "validaHasta": cotizacion.valida_hasta.isoformat(),
        "coberturas": [
            {
                "coberturaId": str(c.cobertura_id),
                "sumaAsegurada": _dinero(c.suma_asegurada),
                "deducible": _dinero(c.deducible),
                "prima": _dinero(c.prima),
            }
            for c in cotizacion.coberturas
        ],
    }
