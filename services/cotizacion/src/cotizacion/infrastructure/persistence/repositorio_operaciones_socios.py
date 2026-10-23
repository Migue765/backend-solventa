from dataclasses import asdict
from datetime import UTC
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cotizacion.application.iniciar_solicitud_socio import OperacionSocio
from cotizacion.infrastructure.persistence.admision_socios import validar_reserva
from cotizacion.infrastructure.persistence.models import OperacionSocioModel


class RepositorioOperacionesSociosSql:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def buscar(self, socio: str, operation_id: UUID) -> OperacionSocio | None:
        with self._sesiones() as sesion:
            row = sesion.get(OperacionSocioModel, {"socio_id": socio, "operation_id": operation_id})
            if row is None:
                return None
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
            )

    def guardar(self, operacion: OperacionSocio, token: UUID) -> None:
        with self._sesiones.begin() as sesion:
            validar_reserva(sesion, operacion.socio_id, operacion.operation_id, token)
            sesion.add(OperacionSocioModel(**asdict(operacion)))
