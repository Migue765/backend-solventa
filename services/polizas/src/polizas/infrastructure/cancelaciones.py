from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from polizas.domain.cancelacion import (
    ConfirmacionCancelacion,
    MotivoNoCancelable,
    PeriodoPagado,
    liquidacion,
    motivo_no_cancelable,
)
from polizas.domain.renovacion import huella
from polizas.infrastructure.models import (
    AuditoriaPolizaModel,
    CancelacionModel,
    PolizaModel,
    RenovacionModel,
)
from polizas.infrastructure.renovaciones import IdempotenciaConflictiva, PolizaNoEncontrada
from polizas.infrastructure.versiones import condiciones_vigentes
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class PolizaNoCancelable(Exception):
    def __init__(self, motivo: MotivoNoCancelable) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class LiquidacionCambio(Exception):
    pass


class _CambioConcurrente(Exception):
    pass


RECHAZOS = (PolizaNoCancelable, LiquidacionCambio, IdempotenciaConflictiva)
CODIGOS = {
    LiquidacionCambio: "liquidacion_cambio",
    IdempotenciaConflictiva: "idempotencia_conflictiva",
}


class RepositorioCancelaciones:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def consultar(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        hoy = _hoy(ahora)
        with self.sesiones() as sesion:
            poliza = _poliza(sesion, poliza_id, cliente_id)
            motivo = _motivo(sesion, poliza, hoy)
            registrada = sesion.scalars(
                select(CancelacionModel).where(CancelacionModel.poliza_id == poliza_id)
            ).first()
            return {
                "polizaId": str(poliza.poliza_id),
                "tipo": poliza.tipo,
                "estado": poliza.estado,
                "vigencia": {
                    "inicio": poliza.fecha_inicio.isoformat(),
                    "fin": poliza.fecha_fin.isoformat(),
                },
                "cancelable": motivo is None,
                "motivo": motivo,
                "liquidacion": _liquidacion(sesion, poliza, hoy) if motivo is None else None,
                "cancelacion": _serializar(registrada) if registrada is not None else None,
            }

    def cancelar(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        confirmacion: ConfirmacionCancelacion,
        correlation_id: str,
        ahora: datetime,
    ) -> tuple[dict[str, object], bool]:
        solicitud_hash = huella(
            {"polizaId": str(poliza_id), **confirmacion.model_dump(mode="json", by_alias=True)}
        )
        try:
            try:
                with self.sesiones.begin() as sesion:
                    existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                    if existente is not None:
                        return _serializar(existente), False
                    cancelacion = self._aplicar(
                        sesion,
                        poliza_id,
                        cliente_id,
                        clave,
                        confirmacion,
                        solicitud_hash,
                        correlation_id,
                        ahora,
                    )
                    return _serializar(cancelacion), True
            except (IntegrityError, _CambioConcurrente):
                with self.sesiones() as sesion:
                    existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                    if existente is not None:
                        return _serializar(existente), False
                    poliza = _poliza(sesion, poliza_id, cliente_id)
                    if poliza.estado == "cancelada":
                        raise PolizaNoCancelable("poliza_cancelada") from None
                    raise LiquidacionCambio() from None
        except RECHAZOS as rechazo:
            self._auditar_rechazo(
                poliza_id, cliente_id, confirmacion, rechazo, correlation_id, ahora
            )
            raise

    def _aplicar(
        self,
        sesion: Session,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        confirmacion: ConfirmacionCancelacion,
        solicitud_hash: str,
        correlation_id: str,
        ahora: datetime,
    ) -> CancelacionModel:
        poliza = _poliza(sesion, poliza_id, cliente_id)
        hoy = _hoy(ahora)
        motivo = _motivo(sesion, poliza, hoy)
        if motivo is not None:
            raise PolizaNoCancelable(motivo)
        calculada = _liquidacion(sesion, poliza, hoy)
        if calculada["liquidacionHash"] != confirmacion.liquidacion_hash:
            raise LiquidacionCambio()
        # La condicion sobre estado y fin evita cancelar sobre una renovacion concurrente.
        cancelada = sesion.execute(
            update(PolizaModel)
            .where(
                PolizaModel.poliza_id == poliza_id,
                PolizaModel.estado == "activa",
                PolizaModel.fecha_fin == poliza.fecha_fin,
            )
            .values(estado="cancelada")
        )
        if cancelada.rowcount != 1:  # type: ignore[attr-defined]
            raise _CambioConcurrente()
        cancelacion = CancelacionModel(
            cancelacion_id=uuid4(),
            poliza_id=poliza_id,
            cliente_id=cliente_id,
            idempotency_key=clave,
            solicitud_hash=solicitud_hash,
            motivo=confirmacion.motivo_cancelacion,
            liquidacion=calculada,
            liquidacion_hash=confirmacion.liquidacion_hash,
            fecha_efectiva=hoy,
            creada_en=ahora,
        )
        sesion.add(cancelacion)
        sesion.add(
            _auditoria(
                poliza_id,
                cliente_id,
                "confirmada",
                confirmacion.motivo_cancelacion,
                {
                    "cancelacionId": str(cancelacion.cancelacion_id),
                    "fechaEfectiva": hoy.isoformat(),
                    "devolucion": calculada["devolucion"],
                    "liquidacionHash": confirmacion.liquidacion_hash,
                },
                correlation_id,
                ahora,
            )
        )
        sesion.flush()
        return cancelacion

    def _auditar_rechazo(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        confirmacion: ConfirmacionCancelacion,
        rechazo: Exception,
        correlation_id: str,
        ahora: datetime,
    ) -> None:
        motivo = getattr(rechazo, "motivo", None) or CODIGOS.get(type(rechazo))
        detalle: dict[str, object] = {
            "error": type(rechazo).__name__,
            "solicitud": confirmacion.model_dump(mode="json", by_alias=True),
        }
        with self.sesiones.begin() as sesion:
            sesion.add(
                _auditoria(
                    poliza_id, cliente_id, "rechazada", motivo, detalle, correlation_id, ahora
                )
            )


def _poliza(sesion: Session, poliza_id: UUID, cliente_id: UUID) -> PolizaModel:
    poliza = sesion.get(PolizaModel, poliza_id)
    if poliza is None or poliza.cliente_id != cliente_id:
        raise PolizaNoEncontrada()
    return poliza


def _motivo(sesion: Session, poliza: PolizaModel, hoy: date) -> MotivoNoCancelable | None:
    pendiente = sesion.scalar(
        select(RenovacionModel.renovacion_id)
        .where(
            RenovacionModel.poliza_id == poliza.poliza_id,
            RenovacionModel.estado == "pago_pendiente",
        )
        .limit(1)
    )
    return motivo_no_cancelable(
        poliza.tipo,
        poliza.fecha_inicio,
        poliza.fecha_fin,
        hoy,
        cancelada=poliza.estado == "cancelada",
        renovacion_pendiente=pendiente is not None,
    )


def _liquidacion(sesion: Session, poliza: PolizaModel, hoy: date) -> dict[str, object]:
    renovadas = sesion.scalars(
        select(RenovacionModel)
        .where(
            RenovacionModel.poliza_id == poliza.poliza_id,
            RenovacionModel.estado == "renovada",
        )
        .order_by(RenovacionModel.nuevo_inicio)
    ).all()
    actual = (poliza.fecha_inicio, renovadas[0].fin_anterior if renovadas else poliza.fecha_fin)
    futuros: list[PeriodoPagado] = []
    for renovacion in renovadas:
        if renovacion.nuevo_inicio > hoy:
            prima = renovacion.condiciones["prima"]
            assert isinstance(prima, dict)
            futuros.append(
                PeriodoPagado(renovacion.nuevo_inicio, renovacion.nuevo_fin, int(prima["monto"]))
            )
        else:
            actual = (renovacion.nuevo_inicio, renovacion.nuevo_fin)
    return liquidacion(poliza.tipo, condiciones_vigentes(sesion, poliza)[1], actual, futuros, hoy)


def _por_clave(
    sesion: Session, cliente_id: UUID, clave: UUID, poliza_id: UUID, solicitud_hash: str
) -> CancelacionModel | None:
    existente = sesion.scalars(
        select(CancelacionModel).where(
            CancelacionModel.cliente_id == cliente_id,
            CancelacionModel.idempotency_key == clave,
        )
    ).first()
    if existente is not None and (
        existente.poliza_id != poliza_id or existente.solicitud_hash != solicitud_hash
    ):
        raise IdempotenciaConflictiva()
    return existente


def _serializar(cancelacion: CancelacionModel) -> dict[str, object]:
    return {
        "id": str(cancelacion.cancelacion_id),
        "polizaId": str(cancelacion.poliza_id),
        "estado": "cancelada",
        "motivoCancelacion": cancelacion.motivo,
        "fechaEfectiva": cancelacion.fecha_efectiva.isoformat(),
        "liquidacion": cancelacion.liquidacion,
        "confirmadaEn": _utc(cancelacion.creada_en).isoformat(),
        "simulada": True,
    }


def _auditoria(
    poliza_id: UUID,
    cliente_id: UUID,
    resultado: str,
    motivo: str | None,
    detalle: dict[str, object],
    correlation_id: str,
    ahora: datetime,
) -> AuditoriaPolizaModel:
    return AuditoriaPolizaModel(
        auditoria_id=uuid4(),
        poliza_id=poliza_id,
        cliente_id=cliente_id,
        accion="cancelacion",
        resultado=resultado,
        motivo=motivo,
        detalle=detalle,
        correlation_id=correlation_id[:64],
        ocurrido_en=ahora,
    )


def _hoy(ahora: datetime) -> date:
    return ahora.astimezone(UTC).date()


def _utc(instante: datetime) -> datetime:
    return instante.replace(tzinfo=UTC) if instante.tzinfo is None else instante.astimezone(UTC)
