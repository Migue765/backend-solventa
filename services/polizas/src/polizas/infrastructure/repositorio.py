import hashlib
from datetime import datetime
from uuid import UUID, uuid4

from polizas.domain.emision import EvidenciaEmision, estado_vigencia
from polizas.infrastructure.models import PolizaModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class ConflictoEmision(Exception):
    pass


class RepositorioPolizas:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def emitir(self, evidencia: EvidenciaEmision, ahora: datetime) -> dict[str, object]:
        huella = hashlib.sha256(evidencia.model_dump_json(by_alias=True).encode()).hexdigest()
        inicio, fin = evidencia.vigencia()
        try:
            with self.sesiones.begin() as sesion:
                existente = self._existente(sesion, evidencia, huella)
                if existente is not None:
                    return _serializar(existente, ahora)
                poliza = PolizaModel(
                    poliza_id=uuid4(),
                    cliente_id=evidencia.cliente_id,
                    suscripcion_id=evidencia.suscripcion_id,
                    pago_id=evidencia.pago_id,
                    tipo=evidencia.tipo,
                    fecha_inicio=inicio,
                    fecha_fin=fin,
                    emitida_en=ahora,
                    evidencia_hash=huella,
                    regla_version="academica-mes-calendario-v1",
                )
                sesion.add(poliza)
                sesion.flush()
                return _serializar(poliza, ahora)
        except IntegrityError:
            with self.sesiones() as sesion:
                existente = self._existente(sesion, evidencia, huella)
                if existente is not None:
                    return _serializar(existente, ahora)
            raise

    @staticmethod
    def _existente(sesion: Session, evidencia: EvidenciaEmision, huella: str) -> PolizaModel | None:
        existentes = sesion.scalars(
            select(PolizaModel).where(
                or_(
                    PolizaModel.suscripcion_id == evidencia.suscripcion_id,
                    PolizaModel.pago_id == evidencia.pago_id,
                )
            )
        ).all()
        if not existentes:
            return None
        if len(existentes) != 1 or existentes[0].evidencia_hash != huella:
            raise ConflictoEmision()
        return existentes[0]

    def listar(self, cliente_id: UUID, ahora: datetime) -> list[dict[str, object]]:
        with self.sesiones() as sesion:
            return [
                _serializar(poliza, ahora)
                for poliza in sesion.scalars(
                    select(PolizaModel)
                    .where(PolizaModel.cliente_id == cliente_id)
                    .order_by(PolizaModel.emitida_en.desc(), PolizaModel.poliza_id)
                )
            ]


def _serializar(poliza: PolizaModel, ahora: datetime) -> dict[str, object]:
    return {
        "id": str(poliza.poliza_id),
        "suscripcionId": str(poliza.suscripcion_id),
        "tipo": poliza.tipo,
        "fechaInicio": poliza.fecha_inicio.isoformat(),
        "fechaFin": poliza.fecha_fin.isoformat(),
        "estado": estado_vigencia(poliza.fecha_inicio, poliza.fecha_fin, ahora),
        "simulada": True,
    }
