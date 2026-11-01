from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.oferta_socio import OfertaSocio
from cotizacion.infrastructure.persistence.models import OfertaSocioModel, OutboxEventoModel


class RepositorioOfertasSociosSql:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def buscar(self, socio: str, operation_id: UUID) -> OfertaSocio | None:
        with self._sesiones() as sesion:
            row = sesion.scalar(
                select(OfertaSocioModel).where(
                    OfertaSocioModel.socio_id == socio,
                    OfertaSocioModel.operation_id == operation_id,
                )
            )
            return _a_dominio(row) if row is not None else None

    def guardar(self, oferta: OfertaSocio) -> OfertaSocio:
        try:
            with self._sesiones.begin() as sesion:
                sesion.add(
                    OfertaSocioModel(
                        oferta_socio_id=oferta.oferta_socio_id,
                        socio_id=oferta.socio_id,
                        operation_id=oferta.operation_id,
                        cliente_id=oferta.cliente_id,
                        producto_id=oferta.producto_id,
                        solicitud_id=oferta.solicitud_id,
                        cotizacion_id=oferta.cotizacion_id,
                        hash_decision=oferta.hash_decision,
                        hash_contenido=oferta.hash_contenido,
                        contenido=oferta.contenido,
                        calculada_en=oferta.calculada_en,
                        vigente_hasta=oferta.vigente_hasta,
                    )
                )
                sesion.flush()
                sesion.add(
                    OutboxEventoModel(
                        evento_id=uuid4(),
                        tipo="OfertaSocioRegistrada",
                        agregado_id=oferta.oferta_socio_id,
                        payload={
                            "ofertaId": str(oferta.oferta_socio_id),
                            "operationId": str(oferta.operation_id),
                            "socioId": oferta.socio_id,
                            "cotizacionId": str(oferta.cotizacion_id),
                            "hashDecision": oferta.hash_decision,
                            "hashContenido": oferta.hash_contenido,
                        },
                        creado_en=oferta.calculada_en,
                        intentos=0,
                    )
                )
        except IntegrityError:
            # Otro proceso confirmó primero la oferta de la misma operación: se devuelve esa.
            existente = self.buscar(oferta.socio_id, oferta.operation_id)
            if existente is None:
                raise
            return existente
        return oferta


def _a_dominio(row: OfertaSocioModel) -> OfertaSocio:
    return OfertaSocio(
        oferta_socio_id=row.oferta_socio_id,
        socio_id=row.socio_id,
        operation_id=row.operation_id,
        cliente_id=row.cliente_id,
        producto_id=row.producto_id,
        solicitud_id=row.solicitud_id,
        cotizacion_id=row.cotizacion_id,
        hash_decision=row.hash_decision,
        hash_contenido=row.hash_contenido,
        contenido=row.contenido,
        calculada_en=_con_zona(row.calculada_en),
        vigente_hasta=_con_zona(row.vigente_hasta),
    )


def _con_zona(fecha: datetime) -> datetime:
    return fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha
