import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.ports import (
    PerfilRiesgoGateway,
    ProveedorPerfil,
    SeleccionOfertaRepository,
    SolicitudCotizacionRepository,
)
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    ErrorConsultaPerfil,
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
from cotizacion.domain.perfil_riesgo import NivelRiesgo, PerfilRiesgo
from cotizacion.domain.productos import PRODUCTOS


class GestionarOfertas:
    def __init__(
        self,
        solicitudes: SolicitudCotizacionRepository,
        selecciones: SeleccionOfertaRepository,
        perfiles_riesgo: PerfilRiesgoGateway,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        perfiles: ProveedorPerfil | None = None,
    ) -> None:
        self._solicitudes = solicitudes
        self._selecciones = selecciones
        self._perfiles_riesgo = perfiles_riesgo
        self._reloj = reloj
        self._perfiles = perfiles

    def listar(self, solicitud_id: str, cliente_id: UUID) -> ResultadoOfertas:
        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        if self._perfiles is None:
            perfil_riesgo = self._perfiles_riesgo.consultar(
                solicitud.tipo_documento, solicitud.identificacion
            )
        else:
            instantanea = solicitud.cotizacion.instantanea
            if instantanea is None:
                raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
            perfil = self._perfiles.obtener(instantanea.perfil_id, cliente_id)
            if perfil is None:
                raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
            nivel = (
                NivelRiesgo.A
                if perfil.score <= Decimal("0.5")
                else (NivelRiesgo.B if perfil.score <= Decimal("0.75") else NivelRiesgo.C)
            )
            factor = {
                NivelRiesgo.A: Decimal("1.00"),
                NivelRiesgo.B: Decimal("1.10"),
                NivelRiesgo.C: Decimal("1.25"),
            }[nivel]
            perfil_riesgo = PerfilRiesgo(nivel, factor)
        return _generar_ofertas(
            solicitud_id,
            _producto_publico(solicitud.cotizacion.producto_id),
            solicitud.creada_en,
            solicitud.cotizacion.valida_hasta,
            self._reloj(),
            perfil_riesgo.factor_prima,
            perfil_riesgo.tarifa_aplicada,
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
    producto_id: str,
    calculada_en: datetime,
    vigente_hasta: datetime | None,
    ahora: datetime,
    factor_prima: Decimal,
    tarifa_aplicada: str,
) -> ResultadoOfertas:
    if vigente_hasta is None:
        raise OfertaNoDisponibleError()
    if vigente_hasta.tzinfo is None:
        vigente_hasta = vigente_hasta.replace(tzinfo=UTC)
    estado_vigente = EstadoOferta.DISPONIBLE if vigente_hasta > ahora else EstadoOferta.VENCIDA
    ofertas = tuple(
        _oferta(
            solicitud_id,
            f"{producto_id}-{indice}",
            plantilla,
            indice == 0,
            estado_vigente,
            vigente_hasta,
            factor_prima,
        )
        for indice, plantilla in enumerate(_PLANTILLAS[producto_id])
    )
    return ResultadoOfertas(
        solicitud_id=solicitud_id,
        calculada_en=calculada_en,
        vigente_hasta=vigente_hasta,
        tasa_aplicada=tarifa_aplicada,
        ofertas=ofertas,
    )


def _oferta(
    solicitud_id: str,
    key: str,
    plantilla: "PlantillaOferta",
    recomendada: bool,
    estado: EstadoOferta,
    vigente_hasta: datetime,
    factor_prima: Decimal,
) -> Oferta:
    return Oferta(
        oferta_id=uuid5(NAMESPACE_URL, f"solventa:{solicitud_id}:{key}"),
        version=1,
        nombre=plantilla.nombre,
        descripcion=plantilla.descripcion,
        prima_mensual=Dinero(plantilla.prima * factor_prima, "COP"),
        cobertura_principal=plantilla.cobertura_principal,
        deducible=plantilla.deducible,
        vigencia=plantilla.vigencia,
        responsabilidad_civil=plantilla.responsabilidad_civil,
        coberturas=plantilla.coberturas,
        condiciones=plantilla.condiciones,
        beneficios_adicionales=plantilla.beneficios,
        recomendada=recomendada,
        estado=estado,
        vigente_hasta=vigente_hasta,
    )


def _producto_publico(producto_interno_id: UUID) -> str:
    return next(
        producto.producto_id
        for producto in PRODUCTOS
        if producto.producto_interno_id == producto_interno_id
    )


@dataclass(frozen=True)
class PlantillaOferta:
    nombre: str
    descripcion: str
    prima: Decimal
    cobertura_principal: str
    deducible: str
    coberturas: tuple[str, ...]
    condiciones: tuple[str, ...]
    beneficios: tuple[str, ...]
    vigencia: str = "Anual"
    responsabilidad_civil: str = "No aplica"


def _plantilla(
    nombre: str,
    descripcion: str,
    prima: str,
    cobertura_principal: str,
    deducible: str,
    coberturas: tuple[str, ...],
    condiciones: tuple[str, ...],
    beneficios: tuple[str, ...],
    *,
    vigencia: str = "Anual",
    responsabilidad_civil: str = "No aplica",
) -> PlantillaOferta:
    return PlantillaOferta(
        nombre,
        descripcion,
        Decimal(prima),
        cobertura_principal,
        deducible,
        coberturas,
        condiciones,
        beneficios,
        vigencia,
        responsabilidad_civil,
    )


_PLANTILLAS: dict[str, tuple[PlantillaOferta, PlantillaOferta, PlantillaOferta]] = {
    "automotive": (
        _plantilla(
            "Seguro Automotriz Pro",
            "Cobertura extendida con beneficios premium.",
            "189900",
            "Daños propios y terceros",
            "$500.000 COP (Bajo)",
            ("Daños materiales, robo y pérdida total.", "Asistencia en ruta 24/7."),
            ("Sujeto a inspección técnica previa.", "Deducible de $500.000 COP por evento."),
            ("Auto de reemplazo", "Defensa jurídica"),
            responsabilidad_civil="Hasta $1.000.000.000 COP",
        ),
        _plantilla(
            "Seguro Automotriz Total",
            "Protección integral para tu vehículo.",
            "149900",
            "Daños, robo y pérdida total",
            "$1.000.000 COP (Medio)",
            ("Daños materiales y pérdida total.", "Responsabilidad civil."),
            ("Vehículos de hasta diez años.", "Deducible de $1.000.000 COP por evento."),
            ("Asistencia en ruta", "Grúa hasta 100 km"),
            responsabilidad_civil="Hasta $750.000.000 COP",
        ),
        _plantilla(
            "Seguro Automotriz Base",
            "Cobertura esencial para terceros.",
            "99900",
            "Responsabilidad civil",
            "$1.500.000 COP (Alto)",
            ("Responsabilidad civil por daños a terceros.",),
            ("Oferta fuera de vigencia.",),
            ("Orientación legal",),
            responsabilidad_civil="Hasta $500.000.000 COP",
        ),
    ),
    "travel": (
        _plantilla(
            "Viaje Global Premium",
            "Protección amplia para viajes nacionales e internacionales.",
            "84900",
            "Emergencias médicas hasta $200.000.000 COP",
            "$200.000 COP por evento médico",
            ("Urgencias médicas.", "Cancelación y pérdida de equipaje."),
            ("Válido para el destino y fechas declaradas.",),
            ("Telemedicina 24/7", "Asistencia de viaje"),
            vigencia="Por viaje",
        ),
        _plantilla(
            "Viaje Integral",
            "Cobertura equilibrada para imprevistos durante el viaje.",
            "59900",
            "Emergencias médicas hasta $120.000.000 COP",
            "$350.000 COP por evento médico",
            ("Urgencias médicas.", "Demora de equipaje."),
            ("Válido para viajeros y fechas declaradas.",),
            ("Orientación médica", "Asistencia documental"),
            vigencia="Por viaje",
        ),
        _plantilla(
            "Viaje Esencial",
            "Protección básica para emergencias durante el viaje.",
            "39900",
            "Emergencias médicas hasta $60.000.000 COP",
            "$500.000 COP por evento médico",
            ("Urgencias médicas por accidente.",),
            ("Oferta fuera de vigencia.",),
            ("Línea de asistencia",),
            vigencia="Por viaje",
        ),
    ),
    "life": (
        _plantilla(
            "Microseguro Vida Familiar",
            "Respaldo económico ampliado para tus beneficiarios.",
            "45900",
            "Fallecimiento por cualquier causa hasta $30.000.000 COP",
            "Sin deducible",
            ("Fallecimiento por cualquier causa.", "Incapacidad total y permanente."),
            ("Sujeto a declaración de asegurabilidad.",),
            ("Asistencia funeraria", "Orientación psicológica"),
        ),
        _plantilla(
            "Microseguro Vida Esencial",
            "Protección accesible para el bienestar de tu familia.",
            "29900",
            "Fallecimiento por cualquier causa hasta $20.000.000 COP",
            "Sin deducible",
            ("Fallecimiento por cualquier causa.",),
            ("Edad de ingreso entre 18 y 65 años.",),
            ("Asistencia funeraria",),
        ),
        _plantilla(
            "Microseguro Vida Básico",
            "Cobertura básica de vida de contratación sencilla.",
            "19900",
            "Fallecimiento accidental hasta $10.000.000 COP",
            "Sin deducible",
            ("Fallecimiento accidental.",),
            ("Oferta fuera de vigencia.",),
            ("Orientación para beneficiarios",),
        ),
    ),
    "device-protection": (
        _plantilla(
            "Dispositivo Protección Total",
            "Cobertura completa para tu dispositivo de uso diario.",
            "44900",
            "Daño accidental y hurto hasta $8.000.000 COP",
            "$300.000 COP por evento",
            ("Daño accidental.", "Hurto calificado y daño por líquidos."),
            ("Requiere factura o soporte de compra.",),
            ("Diagnóstico técnico", "Equipo temporal sujeto a disponibilidad"),
        ),
        _plantilla(
            "Dispositivo Protección Plus",
            "Protección contra los incidentes más frecuentes.",
            "29900",
            "Daño accidental hasta $5.000.000 COP",
            "$450.000 COP por evento",
            ("Daño accidental.", "Daño por líquidos."),
            ("El equipo debe estar funcional al contratar.",),
            ("Recogida a domicilio",),
        ),
        _plantilla(
            "Dispositivo Protección Básica",
            "Cobertura esencial para daños de pantalla.",
            "16900",
            "Daño de pantalla hasta $2.000.000 COP",
            "$600.000 COP por evento",
            ("Rotura accidental de pantalla.",),
            ("Oferta fuera de vigencia.",),
            ("Diagnóstico sin costo",),
        ),
    ),
    "parametric": (
        _plantilla(
            "Vuelo Puntual Premium",
            "Compensación automática amplia ante afectaciones del vuelo.",
            "29900",
            "Compensación hasta $1.500.000 COP",
            "Sin deducible",
            ("Cancelación.", "Retraso superior a tres horas."),
            ("La aerolínea debe confirmar el evento.",),
            ("Pago automático", "Acceso a sala VIP por demora"),
            vigencia="Por vuelo",
        ),
        _plantilla(
            "Vuelo Puntual Plus",
            "Respaldo automático para demoras y cancelaciones.",
            "19900",
            "Compensación hasta $900.000 COP",
            "Sin deducible",
            ("Cancelación.", "Retraso superior a cuatro horas."),
            ("Aplica al vuelo y fecha declarados.",),
            ("Pago digital",),
            vigencia="Por vuelo",
        ),
        _plantilla(
            "Vuelo Puntual Esencial",
            "Compensación básica por cancelación del vuelo.",
            "12900",
            "Compensación hasta $500.000 COP",
            "Sin deducible",
            ("Cancelación confirmada por la aerolínea.",),
            ("Oferta fuera de vigencia.",),
            ("Notificación automática",),
            vigencia="Por vuelo",
        ),
    ),
    "payment-protection": (
        _plantilla(
            "Protección de Pagos Integral",
            "Respaldo amplio de las cuotas de tu crédito.",
            "54900",
            "Hasta doce mensualidades, máximo $12.000.000 COP",
            "Sin deducible",
            ("Desempleo involuntario.", "Incapacidad temporal."),
            ("Aplica después del periodo de carencia de 60 días.",),
            ("Orientación laboral", "Asistencia financiera"),
        ),
        _plantilla(
            "Protección de Pagos Plus",
            "Cobertura equilibrada para mantener tu crédito al día.",
            "39900",
            "Hasta seis mensualidades, máximo $7.200.000 COP",
            "Sin deducible",
            ("Desempleo involuntario.", "Incapacidad temporal."),
            ("Aplica después del periodo de carencia de 90 días.",),
            ("Orientación financiera",),
        ),
        _plantilla(
            "Protección de Pagos Básica",
            "Respaldo esencial de corto plazo para tu obligación.",
            "24900",
            "Hasta tres mensualidades, máximo $3.600.000 COP",
            "Sin deducible",
            ("Desempleo involuntario.",),
            ("Oferta fuera de vigencia.",),
            ("Línea de orientación",),
        ),
    ),
    "mortgage-life": (
        _plantilla(
            "Vida Hipotecaria Integral",
            "Protección completa del saldo de tu crédito de vivienda.",
            "129900",
            "Saldo hipotecario hasta $300.000.000 COP",
            "Sin deducible",
            ("Fallecimiento por cualquier causa.", "Incapacidad total y permanente."),
            ("Sujeto a evaluación de asegurabilidad.",),
            ("Asistencia funeraria", "Orientación sucesoral"),
        ),
        _plantilla(
            "Vida Hipotecaria Equilibrio",
            "Respaldo del crédito hipotecario con prima equilibrada.",
            "94900",
            "Saldo hipotecario hasta $200.000.000 COP",
            "Sin deducible",
            ("Fallecimiento por cualquier causa.",),
            ("Edad máxima de permanencia de 70 años.",),
            ("Asistencia funeraria",),
        ),
        _plantilla(
            "Vida Hipotecaria Esencial",
            "Cobertura básica para proteger el saldo de vivienda.",
            "69900",
            "Saldo hipotecario hasta $120.000.000 COP",
            "Sin deducible",
            ("Fallecimiento accidental.",),
            ("Oferta fuera de vigencia.",),
            ("Orientación para beneficiarios",),
        ),
    ),
}
