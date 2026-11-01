from datetime import UTC
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cotizacion.application.iniciar_solicitud_socio import OperacionSocio
from cotizacion.domain.oferta_hipotecaria import OfertaHipotecariaPersistida
from cotizacion.infrastructure.persistence.admision_socios import validar_reserva
from cotizacion.infrastructure.persistence.models import OfertaHipotecariaModel, OperacionSocioModel


class RepositorioOperacionesSociosSql:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def buscar(self, socio: str, operation_id: UUID) -> OperacionSocio | None:
        with self._sesiones() as sesion:
            row = sesion.get(OperacionSocioModel, {"socio_id": socio, "operation_id": operation_id})
            if row is None:
                return None
            oferta = sesion.get(
                OfertaHipotecariaModel,
                {"socio_id": socio, "operation_id": operation_id},
            )
            return OperacionSocio(
                operation_id=row.operation_id,
                socio_id=row.socio_id,
                usuario_id=row.usuario_id,
                cliente_id=row.cliente_id,
                producto_id=row.producto_id,
                solicitud_id=row.solicitud_id,
                cotizacion_id=row.cotizacion_id,
                perfilamiento_referencia=row.perfilamiento_referencia,
                fingerprint=row.fingerprint,
                creada_en=(
                    row.creada_en.replace(tzinfo=UTC)
                    if row.creada_en.tzinfo is None
                    else row.creada_en
                ),
                oferta_hipotecaria=_oferta_a_dominio(oferta) if oferta is not None else None,
            )

    def guardar(self, operacion: OperacionSocio, token: UUID) -> None:
        with self._sesiones.begin() as sesion:
            validar_reserva(sesion, operacion.socio_id, operacion.operation_id, token)
            sesion.add(
                OperacionSocioModel(
                    operation_id=operacion.operation_id,
                    socio_id=operacion.socio_id,
                    usuario_id=operacion.usuario_id,
                    cliente_id=operacion.cliente_id,
                    producto_id=operacion.producto_id,
                    solicitud_id=operacion.solicitud_id,
                    cotizacion_id=operacion.cotizacion_id,
                    perfilamiento_referencia=operacion.perfilamiento_referencia,
                    fingerprint=operacion.fingerprint,
                    creada_en=operacion.creada_en,
                )
            )
            if operacion.oferta_hipotecaria is not None:
                sesion.flush()
                oferta = operacion.oferta_hipotecaria
                sesion.add(
                    OfertaHipotecariaModel(
                        socio_id=oferta.socio_id,
                        operation_id=oferta.operation_id,
                        oferta_id=oferta.oferta_id,
                        version=oferta.version,
                        contenido=oferta.contenido,
                        evidencia_decision=oferta.evidencia_decision,
                        hash_oferta=oferta.hash_oferta,
                        hash_decision=oferta.hash_decision,
                        creada_en=oferta.creada_en,
                    )
                )


def _oferta_a_dominio(row: OfertaHipotecariaModel) -> OfertaHipotecariaPersistida:
    creada_en = row.creada_en.replace(tzinfo=UTC) if row.creada_en.tzinfo is None else row.creada_en
    return OfertaHipotecariaPersistida(
        operation_id=row.operation_id,
        socio_id=row.socio_id,
        oferta_id=row.oferta_id,
        version=row.version,
        contenido=row.contenido,
        evidencia_decision=row.evidencia_decision,
        hash_oferta=row.hash_oferta,
        hash_decision=row.hash_decision,
        creada_en=creada_en,
    )
