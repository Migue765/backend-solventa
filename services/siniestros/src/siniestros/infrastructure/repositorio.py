from datetime import datetime
from uuid import UUID

from siniestros.domain.estado import (
    DESCRIPCIONES,
    CambioEstado,
    EstadoConsultado,
    RegistroSiniestro,
    consultar_estado,
)
from siniestros.infrastructure.models import CambioEstadoModel, SiniestroModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class RepositorioSiniestros:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def consultar(self, siniestro_id: UUID, cliente_id: UUID) -> dict[str, object] | None:
        """Devuelve None tanto si no existe como si pertenece a otro cliente."""
        with self.sesiones() as sesion:
            siniestro = sesion.scalar(
                select(SiniestroModel).where(
                    SiniestroModel.siniestro_id == siniestro_id,
                    SiniestroModel.cliente_id == cliente_id,
                )
            )
            if siniestro is None:
                return None
            cambios = sesion.scalars(
                select(CambioEstadoModel)
                .where(CambioEstadoModel.siniestro_id == siniestro_id)
                .order_by(CambioEstadoModel.secuencia)
            ).all()
            return _serializar(
                consultar_estado(
                    RegistroSiniestro(
                        siniestro.siniestro_id,
                        siniestro.poliza_id,
                        siniestro.tipo,
                        siniestro.estado,
                        siniestro.ocurrido_en,
                        siniestro.reportado_en,
                        siniestro.actualizado_en,
                    ),
                    [
                        CambioEstado(cambio.estado, cambio.ocurrido_en, cambio.visible_cliente)
                        for cambio in cambios
                    ],
                )
            )


def _serializar(consulta: EstadoConsultado) -> dict[str, object]:
    siniestro = consulta.siniestro
    return {
        "id": str(siniestro.siniestro_id),
        "polizaId": str(siniestro.poliza_id),
        "tipo": siniestro.tipo,
        "estado": siniestro.estado,
        "descripcion": DESCRIPCIONES[siniestro.estado],
        "ocurridoEn": _instante(siniestro.ocurrido_en),
        "reportadoEn": _instante(siniestro.reportado_en),
        "actualizadoEn": _instante(siniestro.actualizado_en),
        "historial": [
            {
                "estado": cambio.estado,
                "descripcion": DESCRIPCIONES[cambio.estado],
                "ocurridoEn": _instante(cambio.ocurrido_en),
            }
            for cambio in consulta.historial
        ],
    }


def _instante(valor: datetime) -> str:
    return valor.isoformat().replace("+00:00", "Z")
