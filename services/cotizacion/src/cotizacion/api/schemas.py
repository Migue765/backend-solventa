from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from pydantic.alias_generators import to_camel

from cotizacion.application.crear_cotizacion import CoberturaSolicitada, CrearCotizacionComando
from cotizacion.application.iniciar_operacion_hipotecaria import (
    IniciarOperacionHipotecariaComando,
)
from cotizacion.application.registrar_solicitud import RegistrarSolicitudComando
from cotizacion.domain.cotizacion import Cotizacion
from cotizacion.domain.datos_producto import validar_datos_producto
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.documentos import validar_identificacion
from cotizacion.domain.ofertas import Oferta, ResultadoOfertas, SeleccionOferta
from cotizacion.domain.pago import PagoSimulado
from cotizacion.domain.productos import PRODUCTOS, ProductoSeguro
from cotizacion.domain.solicitud import SolicitudCotizacion
from cotizacion.domain.suscripcion import Suscripcion


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
    tipo_documento: str = Field(pattern=r"^(cc|ce|passport|ppt)$")
    identificacion: str
    datos_producto: dict[str, str]
    correo: EmailStr
    telefono: str = Field(pattern=r"^\+57[0-9]{10}$")

    @model_validator(mode="after")
    def validar_campos_dependientes(self) -> "RegistrarSolicitudRequest":
        self.identificacion = validar_identificacion(self.tipo_documento, self.identificacion)
        self.datos_producto = validar_datos_producto(self.producto_id, self.datos_producto)
        return self

    def a_comando(self, idempotency_key: UUID) -> RegistrarSolicitudComando:
        return RegistrarSolicitudComando(
            cliente_id=self.cliente_id,
            producto_id=self.producto_id,
            tipo_cliente=self.tipo_cliente,
            tipo_documento=self.tipo_documento,
            identificacion=self.identificacion,
            datos_producto=self.datos_producto,
            correo=str(self.correo).casefold(),
            telefono=self.telefono,
            idempotency_key=idempotency_key,
        )


class ClienteOperacionHipotecariaRequest(_Solicitud):
    cliente_id: UUID
    tipo_documento: str = Field(pattern=r"^(cc|ce|passport|ppt)$")
    numero_documento: str
    correo: EmailStr
    telefono: str = Field(pattern=r"^\+57[0-9]{10}$")
    fecha_nacimiento: date

    @model_validator(mode="after")
    def normalizar_documento(self) -> "ClienteOperacionHipotecariaRequest":
        self.numero_documento = validar_identificacion(self.tipo_documento, self.numero_documento)
        return self


class CreditoHipotecarioRequest(_Solicitud):
    referencia: str = Field(pattern=r"^[A-Za-z0-9-]{4,30}$")
    monto: Decimal = Field(ge=1_000_000, decimal_places=0)
    plazo_meses: int = Field(ge=12, le=360)


class IniciarOperacionHipotecariaRequest(_Solicitud):
    operation_id: UUID
    socio_id: str = Field(min_length=1, max_length=50)
    asesor_id: UUID
    cliente: ClienteOperacionHipotecariaRequest
    credito: CreditoHipotecarioRequest
    consentimiento_id: UUID
    consentimiento_version: int = Field(ge=1)

    def a_comando(self) -> IniciarOperacionHipotecariaComando:
        return IniciarOperacionHipotecariaComando(
            operation_id=self.operation_id,
            socio_id=self.socio_id,
            asesor_id=self.asesor_id,
            cliente_id=self.cliente.cliente_id,
            tipo_documento=self.cliente.tipo_documento,
            numero_documento=self.cliente.numero_documento,
            correo=str(self.cliente.correo).casefold(),
            telefono=self.cliente.telefono,
            fecha_nacimiento=self.cliente.fecha_nacimiento.isoformat(),
            referencia_credito=self.credito.referencia,
            monto_credito=self.credito.monto,
            plazo_meses=self.credito.plazo_meses,
            consentimiento_id=self.consentimiento_id,
            consentimiento_version=self.consentimiento_version,
        )


class SeleccionarOfertaRequest(_Solicitud):
    oferta_id: UUID
    version: int = Field(ge=1)


class ConfirmarPagoSimuladoRequest(_Solicitud):
    pass


def pago_simulado_a_json(pago: PagoSimulado) -> dict[str, object]:
    fecha = pago.confirmada_en.isoformat().replace("+00:00", "Z")
    return {
        "id": str(pago.pago_id),
        "solicitudId": pago.solicitud_id,
        "suscripcionId": str(pago.suscripcion_id),
        "ofertaId": str(pago.oferta_id),
        "ofertaVersion": pago.oferta_version,
        "estado": "confirmado",
        "simulada": True,
        "referenciaInterna": f"SIM-{pago.pago_id}",
        "importe": _dinero(pago.importe),
        "periodicidad": pago.periodicidad,
        "confirmadaEn": fecha,
        "saga": {
            "estado": "pago_confirmado",
            "version": pago.saga_version,
            "actualizadaEn": fecha,
        },
    }


def _dinero(dinero: Dinero) -> dict[str, str]:
    return {"monto": str(dinero.monto), "moneda": dinero.moneda}


def cotizacion_a_json(cotizacion: Cotizacion) -> dict:
    if cotizacion.prima_estimada is None or cotizacion.valida_hasta is None:
        raise ValueError("La cotización todavía no ha sido calculada.")
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
                "prima": _prima_cobertura(c.prima),
            }
            for c in cotizacion.coberturas
        ],
    }


def _prima_cobertura(prima: Dinero | None) -> dict[str, str]:
    if prima is None:
        raise ValueError("La cobertura todavía no ha sido calculada.")
    return _dinero(prima)


def producto_a_json(producto: ProductoSeguro) -> dict[str, object]:
    return {
        "id": producto.producto_id,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "disponible": producto.disponible,
        "coberturasPrincipales": [cobertura.nombre for cobertura in producto.coberturas],
        "coberturas": [
            {
                "nombre": cobertura.nombre,
                "descripcion": cobertura.descripcion,
            }
            for cobertura in producto.coberturas
        ],
        "condiciones": list(producto.condiciones),
        "informacionRelevante": {
            "sumaAsegurada": str(producto.suma_asegurada),
            "deducible": str(producto.deducible),
            "moneda": "COP",
            "vigencia": producto.vigencia,
        },
    }


def solicitud_a_json(solicitud: SolicitudCotizacion) -> dict[str, object]:
    return {
        "id": solicitud.solicitud_id,
        "productoId": _producto_publico(solicitud.cotizacion.producto_id),
        "tipoCliente": solicitud.tipo_cliente,
        "tipoDocumento": solicitud.tipo_documento,
        "identificacion": solicitud.identificacion,
        "datosProducto": solicitud.datos_producto,
        "correo": solicitud.correo,
        "telefono": solicitud.telefono,
        "estado": "recibida",
        "creadaEn": solicitud.creada_en.isoformat().replace("+00:00", "Z"),
        "cotizacion": cotizacion_a_json(solicitud.cotizacion),
    }


def resultado_ofertas_a_json(resultado: ResultadoOfertas) -> dict[str, object]:
    return {
        "solicitudId": resultado.solicitud_id,
        "calculadaEn": _fecha(resultado.calculada_en),
        "vigenteHasta": _fecha(resultado.vigente_hasta),
        "tasaAplicada": resultado.tasa_aplicada,
        "ofertas": [_oferta_a_json(oferta) for oferta in resultado.ofertas],
    }


def seleccion_oferta_a_json(seleccion: SeleccionOferta) -> dict[str, object]:
    return {
        "solicitudId": seleccion.solicitud_id,
        "ofertaId": str(seleccion.oferta_id),
        "version": seleccion.version,
        "nombreOferta": seleccion.nombre_oferta,
        "prima": _dinero(seleccion.prima_mensual),
        "periodicidad": seleccion.periodicidad,
        "primaMensual": (
            _dinero(seleccion.prima_mensual) if seleccion.periodicidad == "mensual" else None
        ),
        "seleccionadaEn": _fecha(seleccion.seleccionada_en),
    }


def suscripcion_a_json(suscripcion: Suscripcion) -> dict[str, object]:
    return {
        "id": str(suscripcion.suscripcion_id),
        "solicitudId": suscripcion.solicitud_id,
        "ofertaId": str(suscripcion.oferta_id),
        "ofertaVersion": suscripcion.oferta_version,
        "estado": suscripcion.estado.value,
        "reglaVersion": suscripcion.regla_version,
        "creadaEn": _fecha(suscripcion.creada_en),
        "actualizadaEn": _fecha(suscripcion.actualizada_en),
        "decision": {
            "resultado": suscripcion.decision.resultado.value,
            "reglaVersion": suscripcion.decision.regla_version,
            "hashDecision": suscripcion.decision.hash_decision,
            "decididaEn": _fecha(suscripcion.decision.decidida_en),
        },
        "saga": {
            "estado": suscripcion.saga.estado.value,
            "version": suscripcion.saga.version,
            "actualizadaEn": _fecha(suscripcion.saga.actualizada_en),
        },
    }


def _oferta_a_json(oferta: Oferta) -> dict[str, object]:
    return {
        "id": str(oferta.oferta_id),
        "version": oferta.version,
        "nombre": oferta.nombre,
        "descripcion": oferta.descripcion,
        "prima": _dinero(oferta.prima_mensual),
        "periodicidad": oferta.periodicidad,
        "primaMensual": (
            _dinero(oferta.prima_mensual) if oferta.periodicidad == "mensual" else None
        ),
        "coberturaPrincipal": oferta.cobertura_principal,
        "deducible": oferta.deducible,
        "vigencia": oferta.vigencia,
        "responsabilidadCivil": oferta.responsabilidad_civil,
        "coberturas": list(oferta.coberturas),
        "condiciones": list(oferta.condiciones),
        "beneficiosAdicionales": list(oferta.beneficios_adicionales),
        "recomendada": oferta.recomendada,
        "estado": oferta.estado.value,
        "vigenteHasta": _fecha(oferta.vigente_hasta),
    }


def _fecha(fecha: datetime) -> str:
    return fecha.isoformat().replace("+00:00", "Z")


def _producto_publico(producto_interno_id: UUID) -> str:
    producto = next(item for item in PRODUCTOS if item.producto_interno_id == producto_interno_id)
    return producto.producto_id
