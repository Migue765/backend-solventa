from datetime import UTC, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion, EstadoCotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.ofertas import SeleccionOferta
from cotizacion.domain.solicitud import SolicitudCotizacion
from cotizacion.infrastructure.persistence.models import (
    CoberturaCotizadaModel,
    CotizacionModel,
    SeleccionOfertaModel,
)


class RepositorioCotizacionesSqlAlchemy:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def guardar(self, cotizacion: Cotizacion) -> None:
        with self._sesiones.begin() as sesion:
            sesion.merge(_a_modelo(cotizacion))

    def obtener(self, cotizacion_id: UUID) -> Cotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.get(CotizacionModel, cotizacion_id)
            return _a_dominio(modelo) if modelo else None

    def guardar_solicitud(self, solicitud: SolicitudCotizacion) -> None:
        modelo = _a_modelo(solicitud.cotizacion)
        modelo.solicitud_id = solicitud.solicitud_id
        modelo.tipo_cliente = solicitud.tipo_cliente
        modelo.identificacion = solicitud.identificacion
        modelo.patente_vehiculo = solicitud.patente_vehiculo
        modelo.correo = solicitud.correo
        modelo.telefono = solicitud.telefono
        modelo.idempotency_key = solicitud.idempotency_key
        modelo.request_fingerprint = solicitud.request_fingerprint
        modelo.creada_en = solicitud.creada_en
        with self._sesiones.begin() as sesion:
            sesion.add(modelo)

    def buscar_por_idempotencia(
        self, cliente_id: UUID, idempotency_key: UUID
    ) -> SolicitudCotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(CotizacionModel).where(
                    CotizacionModel.cliente_id == cliente_id,
                    CotizacionModel.idempotency_key == idempotency_key,
                )
            )
            return _solicitud_a_dominio(modelo) if modelo else None

    def obtener_solicitud(self, solicitud_id: str, cliente_id: UUID) -> SolicitudCotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(CotizacionModel).where(
                    CotizacionModel.solicitud_id == solicitud_id,
                    CotizacionModel.cliente_id == cliente_id,
                )
            )
            return _solicitud_a_dominio(modelo) if modelo else None

    def guardar_seleccion(self, seleccion: SeleccionOferta) -> None:
        with self._sesiones.begin() as sesion:
            sesion.merge(
                SeleccionOfertaModel(
                    cotizacion_id=seleccion.cotizacion_id,
                    solicitud_id=seleccion.solicitud_id,
                    oferta_id=seleccion.oferta_id,
                    version=seleccion.version,
                    nombre_oferta=seleccion.nombre_oferta,
                    prima_mensual=seleccion.prima_mensual.monto,
                    moneda=seleccion.prima_mensual.moneda,
                    seleccionada_en=seleccion.seleccionada_en,
                    idempotency_key=seleccion.idempotency_key,
                    request_fingerprint=seleccion.request_fingerprint,
                )
            )

    def obtener_seleccion(self, solicitud_id: str) -> SeleccionOferta | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(SeleccionOfertaModel).where(
                    SeleccionOfertaModel.solicitud_id == solicitud_id
                )
            )
            if modelo is None:
                return None
            seleccionada_en = modelo.seleccionada_en
            if seleccionada_en.tzinfo is None:
                seleccionada_en = seleccionada_en.replace(tzinfo=UTC)
            return SeleccionOferta(
                solicitud_id=modelo.solicitud_id,
                cotizacion_id=modelo.cotizacion_id,
                oferta_id=modelo.oferta_id,
                version=modelo.version,
                nombre_oferta=modelo.nombre_oferta,
                prima_mensual=Dinero(modelo.prima_mensual, modelo.moneda),
                seleccionada_en=seleccionada_en,
                idempotency_key=modelo.idempotency_key,
                request_fingerprint=modelo.request_fingerprint,
            )


def _a_modelo(cotizacion: Cotizacion) -> CotizacionModel:
    return CotizacionModel(
        cotizacion_id=cotizacion.cotizacion_id,
        cliente_id=cotizacion.cliente_id,
        producto_id=cotizacion.producto_id,
        estado=cotizacion.estado.value,
        moneda=cotizacion.moneda,
        prima_estimada=cotizacion.prima_estimada.monto,
        valida_hasta=cotizacion.valida_hasta,
        coberturas=[
            CoberturaCotizadaModel(
                cobertura_id=c.cobertura_id,
                suma_asegurada=c.suma_asegurada.monto,
                deducible=c.deducible.monto,
                prima=c.prima.monto,
                orden=orden,
            )
            for orden, c in enumerate(cotizacion.coberturas)
        ],
    )


def _a_dominio(modelo: CotizacionModel) -> Cotizacion:
    moneda = modelo.moneda
    valida_hasta = modelo.valida_hasta
    if valida_hasta.tzinfo is None:
        # Motores sin soporte de zona horaria (ej. SQLite) devuelven fechas naive en UTC.
        valida_hasta = valida_hasta.replace(tzinfo=timezone.utc)
    return Cotizacion(
        cotizacion_id=modelo.cotizacion_id,
        cliente_id=modelo.cliente_id,
        producto_id=modelo.producto_id,
        estado=EstadoCotizacion(modelo.estado),
        prima_estimada=Dinero(modelo.prima_estimada, moneda),
        valida_hasta=valida_hasta,
        coberturas=[
            CoberturaCotizada(
                cobertura_id=c.cobertura_id,
                suma_asegurada=Dinero(c.suma_asegurada, moneda),
                deducible=Dinero(c.deducible, moneda),
                prima=Dinero(c.prima, moneda),
            )
            for c in modelo.coberturas
        ],
    )


def _solicitud_a_dominio(modelo: CotizacionModel) -> SolicitudCotizacion:
    if not all(
        (
            modelo.solicitud_id,
            modelo.tipo_cliente,
            modelo.identificacion,
            modelo.patente_vehiculo,
            modelo.correo,
            modelo.telefono,
            modelo.idempotency_key,
            modelo.request_fingerprint,
        )
    ):
        raise ValueError("La cotización no contiene una solicitud completa.")
    creada_en = modelo.creada_en
    if creada_en.tzinfo is None:
        creada_en = creada_en.replace(tzinfo=UTC)
    return SolicitudCotizacion(
        solicitud_id=modelo.solicitud_id,
        cotizacion=_a_dominio(modelo),
        tipo_cliente=modelo.tipo_cliente,
        identificacion=modelo.identificacion,
        patente_vehiculo=modelo.patente_vehiculo,
        correo=modelo.correo,
        telefono=modelo.telefono,
        idempotency_key=modelo.idempotency_key,
        request_fingerprint=modelo.request_fingerprint,
        creada_en=creada_en,
    )
