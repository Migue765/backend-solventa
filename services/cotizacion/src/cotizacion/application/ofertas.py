import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.ports import (
    PerfilRiesgoGateway,
    SeleccionOfertaRepository,
    SolicitudCotizacionRepository,
)
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    OfertaNoDisponibleError,
    ReglaDeNegocioError,
    SeleccionNoEncontradaError,
    SolicitudNoEncontradaError,
)
from cotizacion.domain.ofertas import (
    EstadoOferta,
    Oferta,
    ResultadoOfertas,
    SeleccionOferta,
)
from cotizacion.domain.productos import PRODUCTOS


class GestionarOfertas:
    def __init__(
        self,
        solicitudes: SolicitudCotizacionRepository,
        selecciones: SeleccionOfertaRepository,
        perfiles_riesgo: PerfilRiesgoGateway,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._solicitudes = solicitudes
        self._selecciones = selecciones
        self._perfiles_riesgo = perfiles_riesgo
        self._reloj = reloj

    def listar(self, solicitud_id: str, cliente_id: UUID) -> ResultadoOfertas:
        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        perfil_riesgo = self._perfiles_riesgo.consultar(
            solicitud.tipo_documento, solicitud.identificacion
        )
        return _generar_ofertas(
            solicitud_id,
            _producto_publico(solicitud.cotizacion.producto_id),
            solicitud.creada_en,
            solicitud.cotizacion.valida_hasta,
            self._reloj(),
            perfil_riesgo.factor_prima,
            perfil_riesgo.tarifa_aplicada,
            solicitud.datos_producto,
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
    datos_producto: dict[str, str],
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
        for indice, plantilla in enumerate(_plantillas_producto(producto_id, datos_producto))
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
    producto_id = next(
        (
            producto.producto_id
            for producto in PRODUCTOS
            if producto.producto_interno_id == producto_interno_id
        ),
        None,
    )
    if producto_id is None:
        raise ReglaDeNegocioError("El producto no admite ofertas.")
    return producto_id


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


def _plantillas_producto(producto_id: str, datos: dict[str, str]) -> tuple[PlantillaOferta, ...]:
    # Trabajo en proceso ... :) Las tarifas y ofertas son simuladas, sin emisión ni pagos.
    # No hay consulta a aerolíneas ni monitoreo meteorológico.
    if producto_id == "parametric":
        cobertura = datos.get("coverageType", "delay-cancellation")
        if cobertura == "delay-cancellation":
            return _PLANTILLAS["parametric"]
        if cobertura not in {"missed-connection", "comprehensive"}:
            raise ReglaDeNegocioError("La cobertura de vuelo no admite ofertas.")
        combinada = cobertura == "comprehensive"
        return tuple(
            replace(
                plantilla,
                nombre=f"Vuelo {'Combinado' if combinada else 'Conexión'} {nivel}",
                descripcion="Protección de conexión por afectaciones de la aerolínea.",
                prima=plantilla.prima * (Decimal("1.4") if combinada else Decimal("1.1")),
                coberturas=(
                    (*plantilla.coberturas, "Pérdida de conexión por afectación del vuelo previo.")
                    if combinada
                    else ("Pérdida de conexión por afectación del vuelo previo.",)
                ),
                condiciones=(
                    "Aplica al vuelo y fecha declarados, sujeto a las condiciones del plan.",
                    "Requiere conexión en un mismo itinerario por afectación de la aerolínea.",
                    "No cubre llegar tarde al aeropuerto ni pérdidas por culpa del pasajero.",
                ),
            )
            for plantilla, nivel in zip(
                _PLANTILLAS["parametric"], ("Premium", "Plus", "Esencial"), strict=True
            )
        )
    if producto_id == "parametric-weather":
        evento = datos["weatherEvent"]
        if evento not in {"heavy-rain", "drought"}:
            raise ReglaDeNegocioError("El evento climático no admite ofertas.")
        nombre = "Lluvia Intensa" if evento == "heavy-rain" else "Sequía"
        monto = Decimal(datos["insuredAmount"])
        monto_formateado = f"{int(monto):,}".replace(",", ".")
        dias = (
            date.fromisoformat(datos["endDate"]) - date.fromisoformat(datos["startDate"])
        ).days + 1
        tasa_diaria = Decimal("0.0001") if evento == "heavy-rain" else Decimal("0.00012")
        return tuple(
            _plantilla(
                f"Clima {nombre} {nivel}",
                f"Protección ante {nombre.lower()} en {datos['location']}.",
                str(max(Decimal("1000"), monto * tasa_diaria * dias * factor)),
                f"Hasta ${monto_formateado} COP ante {nombre.lower()}",
                "Sin deducible",
                (f"Evento de {nombre.lower()} durante el periodo declarado.",),
                (
                    f"Municipio y departamento declarados: {datos['location']}.",
                    f"Monto asegurado declarado: {datos['insuredAmount']} COP.",
                    "Disparador, umbral y compensación sujetos a las condiciones del plan.",
                ),
                ("Orientación sobre las condiciones del plan",),
                vigencia=f"Del {datos['startDate']} al {datos['endDate']} ({dias} días inclusive)",
            )
            for nivel, factor in (
                ("Integral", Decimal("1")),
                ("Plus", Decimal("0.85")),
                ("Esencial", Decimal("0.70")),
            )
        )
    plantillas = _PLANTILLAS.get(producto_id)
    if plantillas is None:
        raise ReglaDeNegocioError("El producto no admite ofertas.")
    return plantillas


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
            ("Cobertura de responsabilidad civil para daños a terceros.",),
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
            ("Cobertura médica por accidente durante el viaje declarado.",),
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
            ("Cobertura de fallecimiento accidental, sujeta a las condiciones del plan.",),
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
            ("Cobertura de rotura accidental de pantalla, sujeta a las condiciones del plan.",),
            ("Diagnóstico sin costo",),
        ),
    ),
    "parametric": (
        _plantilla(
            "Vuelo Puntual Premium",
            "Protección ante retrasos o cancelaciones del vuelo.",
            "29900",
            "Compensación hasta $1.500.000 COP",
            "Sin deducible",
            ("Cancelación.", "Retraso superior a tres horas."),
            ("Confirmación del evento sujeta a las condiciones del plan.",),
            ("Orientación sobre la cobertura",),
            vigencia="Por vuelo",
        ),
        _plantilla(
            "Vuelo Puntual Plus",
            "Respaldo para demoras y cancelaciones.",
            "19900",
            "Compensación hasta $900.000 COP",
            "Sin deducible",
            ("Cancelación.", "Retraso superior a cuatro horas."),
            ("Aplica al vuelo y fecha declarados, sujeto a las condiciones del plan.",),
            ("Orientación sobre la cobertura",),
            vigencia="Por vuelo",
        ),
        _plantilla(
            "Vuelo Puntual Esencial",
            "Compensación básica por cancelación del vuelo.",
            "12900",
            "Compensación hasta $500.000 COP",
            "Sin deducible",
            ("Cancelación confirmada por la aerolínea.",),
            ("Confirmación del evento sujeta a las condiciones del plan.",),
            ("Orientación sobre la cobertura",),
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
            ("Cobertura por desempleo involuntario, sujeta a las condiciones del plan.",),
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
            ("Cobertura de fallecimiento accidental sobre el saldo hipotecario declarado.",),
            ("Orientación para beneficiarios",),
        ),
    ),
}
