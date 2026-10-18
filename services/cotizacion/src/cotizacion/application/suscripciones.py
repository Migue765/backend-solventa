import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cotizacion.application.perfilamiento import validar_perfil
from cotizacion.application.ports import (
    ProveedorPerfil,
    SeleccionOfertaRepository,
    SolicitudCotizacionRepository,
    SuscripcionRepository,
)
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    SeleccionNoEncontradaError,
    SolicitudNoEncontradaError,
    SuscripcionNoEncontradaError,
)
from cotizacion.domain.suscripcion import (
    DecisionSuscripcion,
    EstadoSagaEmision,
    EstadoSuscripcion,
    SagaEmision,
    Suscripcion,
)

REGLA_SUSCRIPCION_VERSION = "suscripcion-automatica-2026.1"


class ProcesarSuscripcion:
    def __init__(
        self,
        solicitudes: SolicitudCotizacionRepository,
        selecciones: SeleccionOfertaRepository,
        suscripciones: SuscripcionRepository,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        perfiles: ProveedorPerfil | None = None,
    ) -> None:
        self._solicitudes = solicitudes
        self._selecciones = selecciones
        self._suscripciones = suscripciones
        self._reloj = reloj
        self._perfiles = perfiles

    def iniciar(
        self,
        solicitud_id: str,
        cliente_id: UUID,
        idempotency_key: UUID,
    ) -> Suscripcion:
        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        if self._perfiles is not None:
            validar_perfil(solicitud, self._perfiles)
        seleccion = self._selecciones.obtener_seleccion(solicitud_id)
        if seleccion is None:
            raise SeleccionNoEncontradaError()

        fingerprint = hashlib.sha256(
            f"{solicitud_id}:{seleccion.oferta_id}:{seleccion.version}".encode()
        ).hexdigest()
        por_clave = self._suscripciones.buscar_suscripcion_por_idempotencia(
            cliente_id, idempotency_key
        )
        if por_clave is not None:
            if por_clave.request_fingerprint != fingerprint:
                raise ConflictoIdempotenciaError()
            return por_clave

        existente = self._suscripciones.obtener_suscripcion(solicitud_id)
        if existente is not None:
            return existente

        ahora = self._reloj()
        suscripcion_id = uuid4()
        datos_evaluados = {
            "solicitudId": solicitud_id,
            "clienteId": str(cliente_id),
            "productoId": str(solicitud.cotizacion.producto_id),
            "datosProducto": solicitud.datos_producto,
            "ofertaId": str(seleccion.oferta_id),
            "ofertaVersion": seleccion.version,
            "reglaVersion": REGLA_SUSCRIPCION_VERSION,
        }
        hash_decision = hashlib.sha256(
            json.dumps(
                datos_evaluados,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        decision = DecisionSuscripcion(
            suscripcion_id=suscripcion_id,
            regla_version=REGLA_SUSCRIPCION_VERSION,
            resultado=EstadoSuscripcion.APROBADA,
            datos_evaluados=datos_evaluados,
            hash_decision=hash_decision,
            decidida_en=ahora,
        )
        suscripcion = Suscripcion(
            suscripcion_id=suscripcion_id,
            solicitud_id=solicitud_id,
            cotizacion_id=solicitud.cotizacion.cotizacion_id,
            cliente_id=cliente_id,
            oferta_id=seleccion.oferta_id,
            oferta_version=seleccion.version,
            estado=EstadoSuscripcion.APROBADA,
            regla_version=REGLA_SUSCRIPCION_VERSION,
            creada_en=ahora,
            actualizada_en=ahora,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            decision=decision,
            saga=SagaEmision(
                suscripcion_id=suscripcion_id,
                estado=EstadoSagaEmision.PAGO_PENDIENTE,
                version=1,
                actualizada_en=ahora,
            ),
        )
        self._suscripciones.guardar_suscripcion(suscripcion)
        return suscripcion

    def consultar(self, solicitud_id: str, cliente_id: UUID) -> Suscripcion:
        if self._solicitudes.obtener_solicitud(solicitud_id, cliente_id) is None:
            raise SolicitudNoEncontradaError()
        suscripcion = self._suscripciones.obtener_suscripcion(solicitud_id)
        if suscripcion is None:
            raise SuscripcionNoEncontradaError()
        return suscripcion
