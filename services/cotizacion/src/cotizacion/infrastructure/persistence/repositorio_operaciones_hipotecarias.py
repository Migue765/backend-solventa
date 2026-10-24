from datetime import UTC
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.operacion_hipotecaria import OperacionHipotecaria
from cotizacion.infrastructure.persistence.admision_socios import validar_reserva
from cotizacion.infrastructure.persistence.models import OperacionHipotecariaModel


class RepositorioOperacionesHipotecarias:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def buscar(self, socio_id: str, operation_id: UUID) -> OperacionHipotecaria | None:
        with self._sesiones() as sesion:
            row = sesion.get(
                OperacionHipotecariaModel,
                {"operation_id": operation_id, "socio_id": socio_id},
            )
            return _a_dominio(row) if row is not None else None

    def guardar(self, operacion: OperacionHipotecaria, token: UUID) -> None:
        with self._sesiones.begin() as sesion:
            validar_reserva(sesion, operacion.socio_id, operacion.operation_id, token)
            sesion.add(
                OperacionHipotecariaModel(
                    operation_id=operacion.operation_id,
                    socio_id=operacion.socio_id,
                    asesor_id=operacion.asesor_id,
                    cliente_id=operacion.cliente_id,
                    consentimiento_id=operacion.consentimiento_id,
                    consentimiento_version=operacion.consentimiento_version,
                    referencia_credito=operacion.referencia_credito,
                    monto_credito=operacion.monto_credito,
                    plazo_meses=operacion.plazo_meses,
                    solicitud_id=operacion.solicitud_id,
                    cotizacion_id=operacion.cotizacion_id,
                    perfilamiento_referencia=operacion.perfilamiento_referencia,
                    estado=operacion.estado,
                    request_fingerprint=operacion.request_fingerprint,
                    creada_en=operacion.creada_en,
                )
            )


def _a_dominio(row: OperacionHipotecariaModel) -> OperacionHipotecaria:
    creada_en = row.creada_en
    if creada_en.tzinfo is None:
        creada_en = creada_en.replace(tzinfo=UTC)
    return OperacionHipotecaria(
        operation_id=row.operation_id,
        socio_id=row.socio_id,
        asesor_id=row.asesor_id,
        cliente_id=row.cliente_id,
        consentimiento_id=row.consentimiento_id,
        consentimiento_version=row.consentimiento_version,
        referencia_credito=row.referencia_credito,
        monto_credito=row.monto_credito,
        plazo_meses=row.plazo_meses,
        solicitud_id=row.solicitud_id,
        cotizacion_id=row.cotizacion_id,
        perfilamiento_referencia=row.perfilamiento_referencia,
        estado=row.estado,
        request_fingerprint=row.request_fingerprint,
        creada_en=creada_en,
    )
