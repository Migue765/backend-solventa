import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.ports import (
    SeleccionOfertaRepository,
    SolicitudCotizacionRepository,
)
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    OfertaNoDisponibleError,
    SeleccionNoEncontradaError,
    SolicitudNoEncontradaError,
)
from cotizacion.domain.ofertas import (
    EstadoOferta,
    Oferta,
    ResultadoOfertas,
    SeleccionOferta,
)


class GestionarOfertas:
    def __init__(
        self,
        solicitudes: SolicitudCotizacionRepository,
        selecciones: SeleccionOfertaRepository,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._solicitudes = solicitudes
        self._selecciones = selecciones
        self._reloj = reloj

    def listar(self, solicitud_id: str, cliente_id: UUID) -> ResultadoOfertas:
        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        return _generar_ofertas(
            solicitud_id,
            solicitud.creada_en,
            solicitud.cotizacion.valida_hasta,
            self._reloj(),
        )

    def seleccionar(
        self,
        solicitud_id: str,
        cliente_id: UUID,
        oferta_id: UUID,
        version: int,
        idempotency_key: UUID,
    ) -> SeleccionOferta:
        resultado = self.listar(solicitud_id, cliente_id)
        fingerprint = hashlib.sha256(f"{oferta_id}:{version}".encode()).hexdigest()
        existente = self._selecciones.obtener_seleccion(resultado.solicitud_id)
        if existente and existente.idempotency_key == idempotency_key:
            if existente.request_fingerprint != fingerprint:
                raise ConflictoIdempotenciaError()
            return existente

        oferta = next(
            (
                item
                for item in resultado.ofertas
                if item.oferta_id == oferta_id and item.version == version
            ),
            None,
        )
        if (
            oferta is None
            or oferta.estado is not EstadoOferta.DISPONIBLE
            or oferta.vigente_hasta <= self._reloj()
        ):
            raise OfertaNoDisponibleError()

        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        seleccion = SeleccionOferta(
            solicitud_id=solicitud_id,
            cotizacion_id=solicitud.cotizacion.cotizacion_id,
            oferta_id=oferta.oferta_id,
            version=oferta.version,
            nombre_oferta=oferta.nombre,
            prima_mensual=oferta.prima_mensual,
            seleccionada_en=self._reloj(),
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        self._selecciones.guardar_seleccion(seleccion)
        return seleccion

    def consultar_seleccion(self, solicitud_id: str, cliente_id: UUID) -> SeleccionOferta:
        if self._solicitudes.obtener_solicitud(solicitud_id, cliente_id) is None:
            raise SolicitudNoEncontradaError()
        seleccion = self._selecciones.obtener_seleccion(solicitud_id)
        if seleccion is None:
            raise SeleccionNoEncontradaError()
        return seleccion


def _generar_ofertas(
    solicitud_id: str,
    calculada_en: datetime,
    vigente_hasta: datetime | None,
    ahora: datetime,
) -> ResultadoOfertas:
    if vigente_hasta is None:
        raise OfertaNoDisponibleError()
    if vigente_hasta.tzinfo is None:
        vigente_hasta = vigente_hasta.replace(tzinfo=UTC)
    estado_vigente = EstadoOferta.DISPONIBLE if vigente_hasta > ahora else EstadoOferta.VENCIDA
    ofertas = (
        _oferta(
            solicitud_id,
            "pro",
            "Seguro Automotriz Pro",
            "Cobertura extendida con beneficios premium",
            Decimal("1.80"),
            "Daños propios y terceros",
            "UF 3 (Bajo)",
            "Hasta UF 1.000",
            ("Daños materiales, robo, hurto o uso no autorizado.", "Asistencia en ruta 24/7."),
            ("Sujeto a inspección técnica previa.", "Deducible UF 3 por evento."),
            ("Auto de reemplazo", "Defensa jurídica"),
            True,
            estado_vigente,
            vigente_hasta,
        ),
        _oferta(
            solicitud_id,
            "total",
            "Seguro Automotriz Total",
            "Protección integral para tu vehículo",
            Decimal("1.45"),
            "Daños, robo y pérdida total",
            "UF 5 (Medio)",
            "Hasta UF 750",
            ("Daños materiales y pérdida total.", "Responsabilidad civil."),
            ("Vehículos de hasta diez años.", "Deducible UF 5 por evento."),
            ("Asistencia en ruta", "Grúa hasta 100 km"),
            False,
            estado_vigente,
            vigente_hasta,
        ),
        _oferta(
            solicitud_id,
            "base",
            "Seguro Automotriz Base",
            "Cobertura esencial para terceros",
            Decimal("0.95"),
            "Responsabilidad civil",
            "UF 8 (Alto)",
            "Hasta UF 500",
            ("Responsabilidad civil por daños a terceros.",),
            ("Oferta fuera de vigencia.",),
            ("Orientación legal",),
            False,
            EstadoOferta.VENCIDA,
            calculada_en - timedelta(seconds=1),
        ),
    )
    return ResultadoOfertas(
        solicitud_id=solicitud_id,
        calculada_en=calculada_en,
        vigente_hasta=vigente_hasta,
        tasa_aplicada="Estándar (Risk A)",
        ofertas=ofertas,
    )


def _oferta(
    solicitud_id: str,
    key: str,
    nombre: str,
    descripcion: str,
    prima: Decimal,
    cobertura_principal: str,
    deducible: str,
    responsabilidad: str,
    coberturas: tuple[str, ...],
    condiciones: tuple[str, ...],
    beneficios: tuple[str, ...],
    recomendada: bool,
    estado: EstadoOferta,
    vigente_hasta: datetime,
) -> Oferta:
    return Oferta(
        oferta_id=uuid5(NAMESPACE_URL, f"solventa:{solicitud_id}:{key}"),
        version=1,
        nombre=nombre,
        descripcion=descripcion,
        prima_mensual=Dinero(prima, "UF"),
        cobertura_principal=cobertura_principal,
        deducible=deducible,
        vigencia="Anual",
        responsabilidad_civil=responsabilidad,
        coberturas=coberturas,
        condiciones=condiciones,
        beneficios_adicionales=beneficios,
        recomendada=recomendada,
        estado=estado,
        vigente_hasta=vigente_hasta,
    )
