from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.iniciar_solicitud_socio import RepositorioOperacionesSocios
from cotizacion.application.ofertas import ofertas_desde_decision
from cotizacion.application.perfilamiento import validar_perfil
from cotizacion.application.ports import ProveedorPerfil, SolicitudCotizacionRepository
from cotizacion.domain.decision import contenido_integro
from cotizacion.domain.errors import (
    EntradaOfertaInvalidaError,
    ErrorConsultaPerfil,
    OfertaNoDisponibleError,
    OfertaSocioNoEncontradaError,
    OperacionSocioNoEncontradaError,
)
from cotizacion.domain.oferta_socio import (
    ESQUEMA_OFERTA_SOCIO,
    OfertaSocio,
    factores_planes,
    hash_oferta,
)
from cotizacion.domain.ofertas import Oferta
from cotizacion.domain.perfil_riesgo import perfil_desde_score


class RepositorioOfertasSocios(Protocol):
    def buscar(self, socio: str, operation_id: UUID) -> OfertaSocio | None: ...

    def guardar(self, oferta: OfertaSocio) -> OfertaSocio:
        """Inserta la oferta o devuelve la que otro proceso ya confirmó para la operación."""
        ...


class CalcularOfertaSocio:
    """Combina el rating persistido con el riesgo individualizado vigente y guarda la oferta.

    Un reintento devuelve la oferta guardada sin recalcular ni consultar el perfil.
    """

    def __init__(
        self,
        operaciones: RepositorioOperacionesSocios,
        solicitudes: SolicitudCotizacionRepository,
        ofertas: RepositorioOfertasSocios,
        perfiles: ProveedorPerfil | None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._operaciones = operaciones
        self._solicitudes = solicitudes
        self._ofertas = ofertas
        self._perfiles = perfiles
        self._reloj = reloj

    def ejecutar(self, socio_id: str, operation_id: UUID) -> OfertaSocio:
        existente = self._ofertas.buscar(socio_id, operation_id)
        if existente is not None:
            return existente
        operacion = self._operaciones.buscar(socio_id, operation_id)
        if operacion is None:
            raise OperacionSocioNoEncontradaError()
        if self._perfiles is None:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        solicitud = self._solicitudes.obtener_solicitud(
            operacion.solicitud_id, operacion.cliente_id
        )
        if solicitud is None or solicitud.cotizacion.cotizacion_id != operacion.cotizacion_id:
            raise EntradaOfertaInvalidaError("cotizacion_no_disponible")
        instantanea = solicitud.cotizacion.instantanea
        if instantanea is None or not contenido_integro(instantanea):
            raise EntradaOfertaInvalidaError("decision_inconsistente")
        ahora = self._reloj()
        valida_hasta = solicitud.cotizacion.valida_hasta
        if valida_hasta is None or _con_zona(valida_hasta) <= ahora:
            raise EntradaOfertaInvalidaError("cotizacion_vencida")
        perfil = validar_perfil(solicitud, self._perfiles)
        evidencia = perfil.evidencia
        assert evidencia is not None
        if datetime.fromisoformat(evidencia["vigenteHasta"]) <= ahora:
            raise EntradaOfertaInvalidaError("perfil_vencido")
        if any(datetime.fromisoformat(f["vigenteHasta"]) <= ahora for f in evidencia["fuentes"]):
            raise EntradaOfertaInvalidaError("fuente_vencida")
        if Decimal(instantanea.contenido["perfil"]["score"]) != perfil.score:
            raise EntradaOfertaInvalidaError("decision_inconsistente")
        factores = factores_planes(instantanea.contenido)
        try:
            resultado = ofertas_desde_decision(solicitud, ahora, None)
        except OfertaNoDisponibleError as error:
            raise EntradaOfertaInvalidaError("decision_sin_tarificacion") from error
        if len(resultado.ofertas) != len(factores):
            raise EntradaOfertaInvalidaError("decision_inconsistente")
        contenido = _contenido(
            operacion.socio_id,
            operation_id,
            operacion.producto_id,
            operacion.solicitud_id,
            instantanea.contenido,
            instantanea.hash_decision,
            evidencia,
            resultado.ofertas,
            factores,
            ahora,
            _con_zona(valida_hasta),
        )
        return self._ofertas.guardar(
            OfertaSocio(
                oferta_socio_id=uuid5(
                    NAMESPACE_URL, f"solventa:socio:{socio_id}:{operation_id}:oferta"
                ),
                socio_id=socio_id,
                operation_id=operation_id,
                cliente_id=operacion.cliente_id,
                producto_id=operacion.producto_id,
                solicitud_id=operacion.solicitud_id,
                cotizacion_id=operacion.cotizacion_id,
                hash_decision=instantanea.hash_decision,
                hash_contenido=hash_oferta(contenido),
                contenido=contenido,
                calculada_en=ahora,
                vigente_hasta=_con_zona(valida_hasta),
            )
        )

    def consultar(self, socio_id: str, operation_id: UUID) -> OfertaSocio:
        oferta = self._ofertas.buscar(socio_id, operation_id)
        if oferta is not None:
            return oferta
        if self._operaciones.buscar(socio_id, operation_id) is None:
            raise OperacionSocioNoEncontradaError()
        raise OfertaSocioNoEncontradaError()


def _contenido(
    socio_id: str,
    operation_id: UUID,
    producto_id: str,
    solicitud_id: str,
    decision: dict[str, Any],
    hash_decision: str,
    evidencia: dict[str, Any],
    ofertas: tuple[Oferta, ...],
    factores: list[list[dict[str, str]]],
    calculada_en: datetime,
    vigente_hasta: datetime,
) -> dict[str, Any]:
    detalle = decision["tarificacion"]
    score = Decimal(evidencia["score"])
    return {
        "esquema": ESQUEMA_OFERTA_SOCIO,
        "operationId": str(operation_id),
        "socioId": socio_id,
        "productoId": producto_id,
        "solicitudId": solicitud_id,
        "cotizacionId": decision["cotizacionId"],
        "calculadaEn": calculada_en.isoformat(),
        "vigenteHasta": vigente_hasta.isoformat(),
        "decision": {
            "hashDecision": hash_decision,
            "modelo": detalle["modelo"],
            "reglaId": decision["regla"]["reglaId"],
            "reglaVersion": decision["regla"]["version"],
        },
        "riesgo": {
            "perfilId": evidencia["perfilId"],
            "version": evidencia["version"],
            "modeloVersion": evidencia["modeloVersion"],
            "score": str(score),
            "nivel": perfil_desde_score(score).nivel.value,
            "consentimientoId": evidencia["consentimientoId"],
            "consentimientoVersion": evidencia["consentimientoVersion"],
            "calculadoEn": evidencia["calculadoEn"],
            "vigenteHasta": evidencia["vigenteHasta"],
            "fallbackUsed": evidencia["fallbackUsed"],
        },
        "fuentes": [
            {
                "nombre": fuente["nombre"],
                "version": fuente["version"],
                "consultadaEn": fuente["consultadaEn"],
                "vigenteHasta": fuente["vigenteHasta"],
                "simulada": fuente["simulada"],
                "indicadorRiesgo": fuente["indicadorRiesgo"],
            }
            for fuente in evidencia["fuentes"]
        ],
        "variables": {
            "baseAsegurada": detalle["baseAsegurada"],
            "duracion": detalle["duracion"],
            "periodicidad": detalle["periodicidad"],
            "tasa": decision["regla"]["tasa"],
            "factorPerfil": decision["regla"]["factorPerfil"],
            "score": str(score),
            "moneda": "COP",
        },
        "ofertas": [
            {
                "ofertaId": str(oferta.oferta_id),
                "version": oferta.version,
                "nombre": oferta.nombre,
                "descripcion": oferta.descripcion,
                "prima": {"monto": str(oferta.prima_mensual.monto), "moneda": "COP"},
                "periodicidad": oferta.periodicidad,
                "recomendada": oferta.recomendada,
                "coberturaPrincipal": oferta.cobertura_principal,
                "deducible": oferta.deducible,
                "vigencia": oferta.vigencia,
                "coberturas": list(oferta.coberturas),
                "condiciones": list(oferta.condiciones),
                "beneficiosAdicionales": list(oferta.beneficios_adicionales),
                "factores": factores_plan,
            }
            for oferta, factores_plan in zip(ofertas, factores, strict=True)
        ],
    }


def _con_zona(fecha: datetime) -> datetime:
    return fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha
