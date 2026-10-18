import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from cotizacion.application.calculadora_cotizacion import CalculadoraCotizacion
from cotizacion.application.ports import (
    GeneradorPerfil,
    PerfilRiesgoGateway,
    SolicitudCotizacionRepository,
)
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ConflictoIdempotenciaError, ReglaDeNegocioError
from cotizacion.domain.perfil_riesgo import perfil_desde_score
from cotizacion.domain.productos import buscar_producto
from cotizacion.domain.solicitud import SolicitudCotizacion


@dataclass(frozen=True)
class RegistrarSolicitudComando:
    cliente_id: UUID
    producto_id: str
    tipo_cliente: str
    tipo_documento: str
    identificacion: str
    datos_producto: dict[str, str]
    correo: str
    telefono: str
    idempotency_key: UUID


class RegistrarSolicitud:
    def __init__(
        self,
        repositorio: SolicitudCotizacionRepository,
        calculadora: CalculadoraCotizacion,
        perfiles_riesgo: PerfilRiesgoGateway,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        generador_perfiles: GeneradorPerfil | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._calculadora = calculadora
        self._perfiles_riesgo = perfiles_riesgo
        self._reloj = reloj
        self._generador_perfiles = generador_perfiles

    def ejecutar(self, comando: RegistrarSolicitudComando) -> SolicitudCotizacion:
        fingerprint = _fingerprint(comando)
        existente = self._repositorio.buscar_por_idempotencia(
            comando.cliente_id, comando.idempotency_key
        )
        if existente:
            if existente.request_fingerprint != fingerprint:
                raise ConflictoIdempotenciaError()
            return existente

        producto = buscar_producto(comando.producto_id)
        if producto is None or not producto.disponible:
            raise ReglaDeNegocioError("El seguro seleccionado no está disponible.")

        ahora = self._reloj()
        suma_asegurada = (
            Decimal(comando.datos_producto["insuredAmount"])
            if producto.producto_id == "parametric-weather"
            else producto.suma_asegurada
        )
        cobertura = CoberturaCotizada(
            cobertura_id=producto.cobertura_id,
            suma_asegurada=Dinero(suma_asegurada, "COP"),
            deducible=Dinero(producto.deducible, "COP"),
        )
        cotizacion = Cotizacion(
            cliente_id=comando.cliente_id,
            producto_id=producto.producto_interno_id,
            coberturas=[cobertura],
        )
        perfil_id = str(comando.cliente_id)
        perfil_ofertas = None
        if self._generador_perfiles is not None:
            perfil_remoto = self._generador_perfiles.generar(
                comando.cliente_id, cotizacion.cotizacion_id
            )
            perfil_id = perfil_remoto.perfil_id
            perfil_ofertas = perfil_desde_score(perfil_remoto.score)
            ahora = self._reloj()
        self._calculadora.calcular(
            cotizacion,
            perfil_id,
            ahora,
            producto,
            comando.datos_producto,
            perfil_ofertas=(
                perfil_ofertas
                or self._perfiles_riesgo.consultar(comando.tipo_documento, comando.identificacion)
                if producto.producto_id == "parametric-weather"
                else None
            ),
        )
        solicitud = SolicitudCotizacion(
            solicitud_id=_generar_id(ahora),
            cotizacion=cotizacion,
            tipo_cliente=comando.tipo_cliente,
            tipo_documento=comando.tipo_documento,
            identificacion=comando.identificacion,
            datos_producto=comando.datos_producto,
            correo=comando.correo,
            telefono=comando.telefono,
            idempotency_key=comando.idempotency_key,
            request_fingerprint=fingerprint,
            creada_en=ahora,
        )
        self._repositorio.guardar_solicitud(solicitud)
        return solicitud


def _fingerprint(comando: RegistrarSolicitudComando) -> str:
    payload = {
        "productoId": comando.producto_id,
        "tipoCliente": comando.tipo_cliente,
        "tipoDocumento": comando.tipo_documento,
        "identificacion": comando.identificacion,
        "datosProducto": comando.datos_producto,
        "correo": comando.correo,
        "telefono": comando.telefono,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _generar_id(ahora: datetime) -> str:
    return f"AQT-{ahora.year}-{uuid4().hex[:10].upper()}"
