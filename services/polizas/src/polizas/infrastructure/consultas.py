from datetime import UTC, datetime
from uuid import UUID

from polizas.domain.consulta import condiciones_detalle, resumen, tiene_condiciones
from polizas.infrastructure.models import PolizaModel
from polizas.infrastructure.renovaciones import PolizaNoEncontrada
from polizas.infrastructure.versiones import condiciones_vigentes
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class RepositorioConsultas:
    """Lecturas del cliente autenticado: nunca revelan polizas de otra identidad."""

    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def listar(self, cliente_id: UUID, ahora: datetime) -> list[dict[str, object]]:
        with self.sesiones() as sesion:
            return [
                _resumen(poliza, ahora)
                for poliza in sesion.scalars(
                    select(PolizaModel)
                    .where(PolizaModel.cliente_id == cliente_id)
                    .order_by(PolizaModel.emitida_en.desc(), PolizaModel.poliza_id)
                )
            ]

    def detalle(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        with self.sesiones() as sesion:
            poliza = sesion.get(PolizaModel, poliza_id)
            # Una poliza ajena responde igual que una inexistente para no confirmar su existencia.
            if poliza is None or poliza.cliente_id != cliente_id:
                raise PolizaNoEncontrada()
            vigentes = (
                condiciones_vigentes(sesion, poliza) if tiene_condiciones(poliza.tipo) else None
            )
            return {
                **_resumen(poliza, ahora),
                "suscripcionId": str(poliza.suscripcion_id),
                "emitidaEn": _utc(poliza.emitida_en).isoformat(),
                **condiciones_detalle(poliza.tipo, vigentes),
                "consultadaEn": _utc(ahora).isoformat(),
            }


def _resumen(poliza: PolizaModel, ahora: datetime) -> dict[str, object]:
    result = resumen(
        poliza.poliza_id,
        poliza.tipo,
        poliza.fecha_inicio,
        poliza.fecha_fin,
        _utc(poliza.emitida_en),
        ahora,
    )
    if poliza.estado == "cancelada":
        result["estado"] = "cancelada"
    return result


def _utc(instante: datetime) -> datetime:
    return instante.replace(tzinfo=UTC) if instante.tzinfo is None else instante.astimezone(UTC)
