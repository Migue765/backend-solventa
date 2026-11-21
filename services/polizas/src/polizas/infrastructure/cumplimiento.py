import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from polizas.domain.cumplimiento import (
    ErrorCumplimiento,
    GuardarReporte,
    SolicitudIntercambio,
    intercambio_academico,
)
from polizas.domain.reporte import huella, instante
from polizas.infrastructure.consultas import _utc
from polizas.infrastructure.models import (
    AuditoriaCumplimientoModel,
    IntercambioReaseguroModel,
    ReporteRegulatorioModel,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class RepositorioCumplimiento:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def recuperar(
        self, actor: UUID, clave: UUID, correlacion: str, ahora: datetime
    ) -> dict[str, Any]:
        with self.sesiones.begin() as sesion:
            reporte = sesion.scalar(
                select(ReporteRegulatorioModel).where(
                    ReporteRegulatorioModel.actor_id == actor,
                    ReporteRegulatorioModel.idempotency_key == clave,
                )
            )
            if reporte is None:
                raise ErrorCumplimiento("recurso_no_encontrado", 404)
            self._auditar(sesion, actor, reporte.reporte_id, "reintento", correlacion, ahora)
            return _reporte(reporte)

    def guardar(
        self, actor: UUID, clave: UUID, solicitud: GuardarReporte, correlacion: str, ahora: datetime
    ) -> tuple[dict[str, Any], bool]:
        try:
            return self._guardar(actor, clave, solicitud, correlacion, ahora)
        except IntegrityError:
            return self._guardar(actor, clave, solicitud, correlacion, ahora)

    def _guardar(
        self, actor: UUID, clave: UUID, solicitud: GuardarReporte, correlacion: str, ahora: datetime
    ) -> tuple[dict[str, Any], bool]:
        with self.sesiones.begin() as sesion:
            existente = sesion.scalar(
                select(ReporteRegulatorioModel).where(
                    ReporteRegulatorioModel.actor_id == actor,
                    ReporteRegulatorioModel.idempotency_key == clave,
                )
            )
            if existente is not None:
                if existente.contenido_hash != solicitud.contenidoHash:
                    raise ErrorCumplimiento("idempotencia_conflictiva", 409)
                self._auditar(sesion, actor, existente.reporte_id, "reintento", correlacion, ahora)
                return _reporte(existente), False
            reporte = ReporteRegulatorioModel(
                reporte_id=uuid4(),
                actor_id=actor,
                idempotency_key=clave,
                contenido_hash=solicitud.contenidoHash,
                contenido=solicitud.contenido,
                creado_en=ahora,
            )
            sesion.add(reporte)
            self._auditar(sesion, actor, reporte.reporte_id, "generacion", correlacion, ahora)
            sesion.flush()
            return _reporte(reporte), True

    def consultar(
        self, actor: UUID, recurso: UUID, tipo: str, accion: str, correlacion: str, ahora: datetime
    ) -> dict[str, Any]:
        with self.sesiones.begin() as sesion:
            if tipo == "reportes":
                reporte = sesion.get(ReporteRegulatorioModel, recurso)
                if reporte is None:
                    raise ErrorCumplimiento("recurso_no_encontrado", 404)
                resultado = _reporte(reporte)
            else:
                intercambio = sesion.get(IntercambioReaseguroModel, recurso)
                if intercambio is None:
                    raise ErrorCumplimiento("recurso_no_encontrado", 404)
                resultado = _intercambio(intercambio)
            self._auditar(sesion, actor, recurso, accion, correlacion, ahora)
            return resultado

    def intercambiar(
        self,
        actor: UUID,
        clave: UUID,
        solicitud: SolicitudIntercambio,
        correlacion: str,
        ahora: datetime,
    ) -> tuple[dict[str, Any], bool]:
        try:
            return self._intercambiar(actor, clave, solicitud, correlacion, ahora)
        except IntegrityError:
            return self._intercambiar(actor, clave, solicitud, correlacion, ahora)

    def _intercambiar(
        self,
        actor: UUID,
        clave: UUID,
        solicitud: SolicitudIntercambio,
        correlacion: str,
        ahora: datetime,
    ) -> tuple[dict[str, Any], bool]:
        solicitud_hash = huella(solicitud.model_dump(mode="json"))
        with self.sesiones.begin() as sesion:
            existente = sesion.scalar(
                select(IntercambioReaseguroModel).where(
                    IntercambioReaseguroModel.actor_id == actor,
                    IntercambioReaseguroModel.idempotency_key == clave,
                )
            )
            if existente is None:
                existente = sesion.scalar(
                    select(IntercambioReaseguroModel).where(
                        IntercambioReaseguroModel.reporte_id == solicitud.reporteId,
                    )
                )
            if existente is not None:
                if (
                    existente.solicitud_hash != solicitud_hash
                    or existente.idempotency_key != clave
                    or existente.actor_id != actor
                ):
                    raise ErrorCumplimiento("idempotencia_conflictiva", 409)
                self._auditar(
                    sesion, actor, existente.intercambio_id, "reintento", correlacion, ahora
                )
                return _intercambio(existente), False
            reporte = sesion.get(ReporteRegulatorioModel, solicitud.reporteId)
            if reporte is None:
                raise ErrorCumplimiento("recurso_no_encontrado", 404)
            intercambio_id = uuid4()
            resultado = intercambio_academico(
                json.loads(reporte.contenido), intercambio_id, solicitud.escenario
            )
            intercambio = IntercambioReaseguroModel(
                intercambio_id=intercambio_id,
                reporte_id=reporte.reporte_id,
                actor_id=actor,
                idempotency_key=clave,
                solicitud_hash=solicitud_hash,
                contenido=resultado,
                creado_en=ahora,
            )
            sesion.add(intercambio)
            self._auditar(sesion, actor, intercambio_id, "intercambio", correlacion, ahora)
            sesion.flush()
            return _intercambio(intercambio), True

    @staticmethod
    def _auditar(
        sesion: Session, actor: UUID, recurso: UUID, accion: str, correlacion: str, ahora: datetime
    ) -> None:
        sesion.add(
            AuditoriaCumplimientoModel(
                auditoria_id=uuid4(),
                actor_id=actor,
                recurso_id=recurso,
                accion=accion,
                correlation_id=correlacion,
                ocurrido_en=ahora,
            )
        )


def _reporte(reporte: ReporteRegulatorioModel) -> dict[str, Any]:
    return {
        "reporteId": str(reporte.reporte_id),
        "contenidoHash": reporte.contenido_hash,
        "generadoEn": instante(_utc(reporte.creado_en)),
        "contenido": json.loads(reporte.contenido),
    }


def _intercambio(intercambio: IntercambioReaseguroModel) -> dict[str, Any]:
    return {
        "intercambioId": str(intercambio.intercambio_id),
        "reporteId": str(intercambio.reporte_id),
        "generadoEn": instante(_utc(intercambio.creado_en)),
        **intercambio.contenido,
    }
