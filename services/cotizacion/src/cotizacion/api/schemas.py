from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from cotizacion.application.crear_cotizacion import CoberturaSolicitada, CrearCotizacionComando
from cotizacion.application.registrar_solicitud import RegistrarSolicitudComando
from cotizacion.domain.cotizacion import Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.productos import PRODUCTOS, ProductoSeguro
from cotizacion.domain.solicitud import SolicitudCotizacion


class _Solicitud(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class CoberturaRequest(_Solicitud):
    cobertura_id: UUID
    suma_asegurada: Decimal = Field(gt=0)
    deducible: Decimal = Field(default=Decimal("0"), ge=0)


class CrearCotizacionRequest(_Solicitud):
    cliente_id: UUID
    producto_id: UUID
    perfil_id: str = Field(min_length=1, max_length=64)
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    coberturas: list[CoberturaRequest] = Field(min_length=1)

    def a_comando(self) -> CrearCotizacionComando:
        return CrearCotizacionComando(
            cliente_id=self.cliente_id,
            producto_id=self.producto_id,
            perfil_id=self.perfil_id,
            moneda=self.moneda,
            coberturas=tuple(
                CoberturaSolicitada(c.cobertura_id, c.suma_asegurada, c.deducible)
                for c in self.coberturas
            ),
        )


class RegistrarSolicitudRequest(_Solicitud):
    cliente_id: UUID
    producto_id: str = Field(max_length=64)
    tipo_cliente: str = Field(pattern=r"^persona_natural$")
    identificacion: str = Field(pattern=r"^[A-Za-z0-9.-]{6,20}$")
    patente_vehiculo: str = Field(pattern=r"^[A-Za-z0-9-]{5,8}$")
    correo: EmailStr
    telefono: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")

    def a_comando(self, idempotency_key: UUID) -> RegistrarSolicitudComando:
        return RegistrarSolicitudComando(
            cliente_id=self.cliente_id,
            producto_id=self.producto_id,
            tipo_cliente=self.tipo_cliente,
            identificacion=self.identificacion.upper(),
            patente_vehiculo=self.patente_vehiculo.upper(),
            correo=str(self.correo).casefold(),
            telefono=self.telefono,
            idempotency_key=idempotency_key,
        )


def _dinero(dinero: Dinero) -> dict:
    return {"monto": str(dinero.monto), "moneda": dinero.moneda}


def cotizacion_a_json(cotizacion: Cotizacion) -> dict:
    decision = cotizacion.instantanea
    return {
        "cotizacionId": str(cotizacion.cotizacion_id),
        "clienteId": str(cotizacion.cliente_id),
        "productoId": str(cotizacion.producto_id),
        "estado": cotizacion.estado.value,
        "primaEstimada": _dinero(cotizacion.prima_estimada),
        "validaHasta": cotizacion.valida_hasta.isoformat(),
        "decision": decision
        and {
            "reglaVersion": decision.regla_version,
            "perfilId": decision.perfil_id,
            "hashDecision": decision.hash_decision,
        },
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


def producto_a_json(producto: ProductoSeguro) -> dict[str, object]:
    return {
        "id": producto.producto_id,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "disponible": producto.disponible,
    }


def solicitud_a_json(solicitud: SolicitudCotizacion) -> dict[str, object]:
    return {
        "id": solicitud.solicitud_id,
        "productoId": _producto_publico(solicitud.cotizacion.producto_id),
        "tipoCliente": solicitud.tipo_cliente,
        "identificacion": solicitud.identificacion,
        "patenteVehiculo": solicitud.patente_vehiculo,
        "correo": solicitud.correo,
        "telefono": solicitud.telefono,
        "estado": "recibida",
        "creadaEn": solicitud.creada_en.isoformat().replace("+00:00", "Z"),
    }


def _producto_publico(producto_interno_id: UUID) -> str:
    producto = next(item for item in PRODUCTOS if item.producto_interno_id == producto_interno_id)
    return producto.producto_id
