import hashlib
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from polizas.domain.condiciones import PRODUCTOS
from polizas.domain.emision import EvidenciaEmision, estado_vigencia
from polizas.domain.reporte import instante
from polizas.infrastructure.models import (
    CancelacionModel,
    PolizaModel,
    RenovacionModel,
    VersionPolizaModel,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class ConflictoEmision(Exception):
    pass


class RepositorioPolizas:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def emitir(self, evidencia: EvidenciaEmision, ahora: datetime) -> dict[str, object]:
        huella = hashlib.sha256(
            evidencia.model_dump_json(
                by_alias=True,
                exclude={
                    key
                    for key in ("condiciones_emision", "contrato_vuelo")
                    if getattr(evidencia, key) is None
                },
            ).encode()
        ).hexdigest()
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
                    contrato_vuelo=(
                        evidencia.contrato_vuelo.model_dump(mode="json")
                        if evidencia.contrato_vuelo is not None
                        else None
                    ),
                )
                sesion.add(poliza)
                sesion.flush()
                if evidencia.condiciones_emision is not None:
                    condiciones = evidencia.condiciones_emision
                    sesion.add(
                        VersionPolizaModel(
                            poliza_id=poliza.poliza_id,
                            version=1,
                            plan=condiciones.plan,
                            prima=str(int(Decimal(condiciones.prima))),
                            coberturas=[
                                c.codigo
                                for c in PRODUCTOS[evidencia.tipo].coberturas
                                if c.nombre in condiciones.coberturas
                            ],
                            efectivo_desde=inicio,
                            origen="emision",
                            endoso_id=None,
                            creada_en=ahora,
                        )
                    )
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
        if len(existentes) != 1:
            raise ConflictoEmision()
        if existentes[0].evidencia_hash != huella:
            legacy = hashlib.sha256(
                evidencia.model_dump_json(
                    by_alias=True, exclude={"condiciones_emision", "contrato_vuelo"}
                ).encode()
            ).hexdigest()
            if existentes[0].evidencia_hash != legacy:
                raise ConflictoEmision()
            logger.warning(
                "Emision anterior repetida sin reconstruir condiciones historicas: pago=%s",
                evidencia.pago_id,
            )
        return existentes[0]

    def contratos_vuelo(self, fecha: date) -> list[dict[str, object]]:
        with self.sesiones() as sesion:
            return [
                {
                    "polizaId": str(poliza.poliza_id),
                    "clienteId": str(poliza.cliente_id),
                    "emitidaEn": _utc(poliza.emitida_en).isoformat(),
                    **poliza.contrato_vuelo,
                }
                for poliza in sesion.scalars(
                    select(PolizaModel).where(
                        PolizaModel.tipo == "parametric",
                        PolizaModel.estado == "activa",
                        PolizaModel.fecha_inicio == fecha,
                        PolizaModel.contrato_vuelo.is_not(None),
                    )
                )
                if poliza.contrato_vuelo is not None
            ]

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

    def reporte(self, corte: datetime) -> list[dict[str, object]]:
        """Polizas emitidas hasta el corte, sin datos del cliente, con su estado al corte."""
        with self.sesiones() as sesion:
            polizas = sesion.scalars(
                select(PolizaModel)
                .where(PolizaModel.emitida_en <= corte)
                .order_by(PolizaModel.poliza_id)
            ).all()
            canceladas = {
                poliza_id
                for poliza_id, creada_en in sesion.execute(
                    select(CancelacionModel.poliza_id, CancelacionModel.creada_en)
                )
                if _utc(creada_en) <= corte
            }
            # La renovacion extiende fecha_fin en sitio; la primera confirmada despues del
            # corte conserva el fin vigente a esa fecha.
            fin_al_corte: dict[UUID, date] = {}
            for poliza_id, fin_anterior, renovada_en in sesion.execute(
                select(
                    RenovacionModel.poliza_id,
                    RenovacionModel.fin_anterior,
                    RenovacionModel.actualizada_en,
                )
                .where(RenovacionModel.estado == "renovada")
                .order_by(RenovacionModel.actualizada_en)
            ):
                if _utc(renovada_en) > corte:
                    fin_al_corte.setdefault(poliza_id, fin_anterior)
            registros: list[dict[str, object]] = []
            for poliza in polizas:
                if _utc(poliza.emitida_en) > corte:
                    continue
                fin = fin_al_corte.get(poliza.poliza_id, poliza.fecha_fin)
                registros.append(
                    {
                        "id": str(poliza.poliza_id),
                        "tipo": poliza.tipo,
                        "pagoId": str(poliza.pago_id),
                        "emitidaEn": instante(_utc(poliza.emitida_en)),
                        "fechaInicio": poliza.fecha_inicio.isoformat(),
                        "fechaFin": fin.isoformat(),
                        "estado": (
                            "cancelada"
                            if poliza.poliza_id in canceladas
                            else estado_vigencia(poliza.fecha_inicio, fin, corte)
                        ),
                    }
                )
            return registros


def _utc(valor: datetime) -> datetime:
    # SQLite devuelve instantes sin zona aunque la columna sea timezone=True.
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _serializar(poliza: PolizaModel, ahora: datetime) -> dict[str, object]:
    return {
        "id": str(poliza.poliza_id),
        "suscripcionId": str(poliza.suscripcion_id),
        "tipo": poliza.tipo,
        "fechaInicio": poliza.fecha_inicio.isoformat(),
        "fechaFin": poliza.fecha_fin.isoformat(),
        "estado": (
            "cancelada"
            if poliza.estado == "cancelada"
            else estado_vigencia(poliza.fecha_inicio, poliza.fecha_fin, ahora)
        ),
        "simulada": True,
    }
