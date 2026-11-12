import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from polizas.domain.renovacion import (
    MotivoNoRenovable,
    SolicitudRenovacion,
    huella,
    motivo_no_renovable,
    oferta,
    ventana,
)
from polizas.infrastructure.models import PagoRenovacionModel, PolizaModel, RenovacionModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

ResultadoPago = Literal["confirmado", "rechazado"]


class PolizaNoEncontrada(Exception):
    pass


class PolizaNoRenovable(Exception):
    def __init__(self, motivo: MotivoNoRenovable) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class CondicionesCambiaron(Exception):
    pass


class IdempotenciaConflictiva(Exception):
    pass


class RenovacionEnCurso(Exception):
    def __init__(self, renovacion_id: UUID | None) -> None:
        super().__init__(str(renovacion_id))
        self.renovacion_id = renovacion_id


class PasarelaPagoRenovacion(Protocol):
    def cobrar(self, renovacion_id: UUID, monto: str, moneda: str) -> ResultadoPago: ...


class SimuladorPagoRenovacion:
    """Pasarela academica: el primer resultado persistido es el unico efecto del cobro."""

    def __init__(self, sesiones: sessionmaker[Session], resultado: str) -> None:
        if resultado not in {"confirmado", "rechazado"}:
            raise ValueError("PAGO_RENOVACION_SIMULADO solo admite confirmado o rechazado.")
        self._sesiones = sesiones
        self._resultado = resultado

    def cobrar(self, renovacion_id: UUID, monto: str, moneda: str) -> ResultadoPago:
        with self._sesiones.begin() as sesion:
            insertar = sqlite_insert if sesion.get_bind().dialect.name == "sqlite" else pg_insert
            sesion.execute(
                insertar(PagoRenovacionModel)
                .values(
                    renovacion_id=renovacion_id,
                    monto=monto,
                    moneda=moneda,
                    resultado=self._resultado,
                    evaluado_en=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["renovacion_id"])
            )
            efecto = sesion.get(PagoRenovacionModel, renovacion_id)
            if efecto is None or efecto.monto != monto or efecto.moneda != moneda:
                raise RuntimeError("El efecto del pago no corresponde a la renovacion.")
            return "confirmado" if efecto.resultado == "confirmado" else "rechazado"


class RepositorioRenovaciones:
    def __init__(self, sesiones: sessionmaker[Session], pasarela: PasarelaPagoRenovacion) -> None:
        self.sesiones = sesiones
        self.pasarela = pasarela

    def consultar(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        hoy = ahora.astimezone(UTC).date()
        with self.sesiones() as sesion:
            poliza = _poliza(sesion, poliza_id, cliente_id)
            motivo = motivo_no_renovable(poliza.tipo, poliza.fecha_inicio, poliza.fecha_fin, hoy)
            ultima = sesion.scalars(
                select(RenovacionModel)
                .where(RenovacionModel.poliza_id == poliza_id)
                .order_by(RenovacionModel.creada_en.desc(), RenovacionModel.renovacion_id)
                .limit(1)
            ).first()
            desde, hasta = ventana(poliza.fecha_fin)
            return {
                "polizaId": str(poliza.poliza_id),
                "tipo": poliza.tipo,
                "vigenciaActual": {
                    "inicio": poliza.fecha_inicio.isoformat(),
                    "fin": poliza.fecha_fin.isoformat(),
                },
                "renovable": motivo is None,
                "motivo": motivo,
                "ventana": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
                "oferta": (
                    oferta(poliza.poliza_id, poliza.tipo, poliza.fecha_fin)
                    if motivo is None
                    else None
                ),
                "ultimoIntento": _serializar(ultima) if ultima is not None else None,
            }

    def renovar(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        solicitud: SolicitudRenovacion,
        ahora: datetime,
    ) -> tuple[dict[str, object], bool]:
        solicitud_hash = huella(
            {"polizaId": str(poliza_id), "condicionesHash": solicitud.condiciones_hash}
        )
        try:
            with self.sesiones.begin() as sesion:
                existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                if existente is None:
                    renovacion_id = self._registrar_intento(
                        sesion, poliza_id, cliente_id, clave, solicitud, solicitud_hash, ahora
                    )
                    nueva = True
                else:
                    renovacion_id, nueva = existente.renovacion_id, False
        except IntegrityError:
            with self.sesiones() as sesion:
                existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                if existente is None:
                    raise RenovacionEnCurso(_reserva_activa(sesion, poliza_id)) from None
                renovacion_id, nueva = existente.renovacion_id, False
        return self.resolver(renovacion_id), nueva

    def _registrar_intento(
        self,
        sesion: Session,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        solicitud: SolicitudRenovacion,
        solicitud_hash: str,
        ahora: datetime,
    ) -> UUID:
        poliza = _poliza(sesion, poliza_id, cliente_id)
        motivo = motivo_no_renovable(
            poliza.tipo, poliza.fecha_inicio, poliza.fecha_fin, ahora.astimezone(UTC).date()
        )
        if motivo is not None:
            raise PolizaNoRenovable(motivo)
        condiciones = oferta(poliza.poliza_id, poliza.tipo, poliza.fecha_fin)
        if condiciones["condicionesHash"] != solicitud.condiciones_hash:
            raise CondicionesCambiaron()
        vigencia = condiciones["nuevaVigencia"]
        assert isinstance(vigencia, dict)
        renovacion = RenovacionModel(
            renovacion_id=uuid4(),
            poliza_id=poliza_id,
            cliente_id=cliente_id,
            idempotency_key=clave,
            solicitud_hash=solicitud_hash,
            reserva_periodo=_reserva(poliza_id, poliza.fecha_fin.isoformat()),
            fin_anterior=poliza.fecha_fin,
            nuevo_inicio=date.fromisoformat(vigencia["inicio"]),
            nuevo_fin=date.fromisoformat(vigencia["fin"]),
            condiciones=condiciones,
            condiciones_hash=solicitud.condiciones_hash,
            regla_version=str(condiciones["reglaVersion"]),
            estado="pago_pendiente",
            motivo=None,
            intentos_pago=0,
            creada_en=ahora,
            actualizada_en=ahora,
        )
        sesion.add(renovacion)
        sesion.flush()
        return renovacion.renovacion_id

    def resolver(self, renovacion_id: UUID) -> dict[str, object]:
        with self.sesiones() as sesion:
            renovacion = sesion.get(RenovacionModel, renovacion_id)
            assert renovacion is not None
            if renovacion.estado != "pago_pendiente":
                return _serializar(renovacion)
            prima = renovacion.condiciones["prima"]
            assert isinstance(prima, dict)
        try:
            resultado = self.pasarela.cobrar(
                renovacion_id, str(prima["monto"]), str(prima["moneda"])
            )
        except Exception:
            # Resultado ambiguo: el intento queda pendiente y solo se concilia con la misma
            # renovacion; nunca se crea un segundo cobro.
            logger.warning("renovacion.pago_ambiguo renovacion_id=%s", renovacion_id)
            with self.sesiones.begin() as sesion:
                pendiente = sesion.get(RenovacionModel, renovacion_id)
                assert pendiente is not None
                pendiente.intentos_pago += 1
                pendiente.actualizada_en = datetime.now(UTC)
                return _serializar(pendiente)
        with self.sesiones.begin() as sesion:
            renovacion = sesion.get(RenovacionModel, renovacion_id)
            assert renovacion is not None
            if renovacion.estado != "pago_pendiente":
                return _serializar(renovacion)
            renovacion.intentos_pago += 1
            renovacion.actualizada_en = datetime.now(UTC)
            if resultado == "rechazado":
                renovacion.estado = "rechazada"
                renovacion.motivo = "pago_rechazado"
                renovacion.reserva_periodo = None
                return _serializar(renovacion)
            extendida = sesion.execute(
                update(PolizaModel)
                .where(
                    PolizaModel.poliza_id == renovacion.poliza_id,
                    PolizaModel.fecha_fin == renovacion.fin_anterior,
                )
                .values(fecha_fin=renovacion.nuevo_fin)
            )
            if extendida.rowcount != 1:  # type: ignore[attr-defined]
                raise RuntimeError("La vigencia de la poliza no coincide con la renovacion.")
            renovacion.estado = "renovada"
            return _serializar(renovacion)

    def conciliar_pendientes(self, antiguedad: timedelta = timedelta(seconds=30)) -> int:
        limite = datetime.now(UTC) - antiguedad
        with self.sesiones() as sesion:
            pendientes = sesion.scalars(
                select(RenovacionModel.renovacion_id).where(
                    RenovacionModel.estado == "pago_pendiente",
                    RenovacionModel.actualizada_en <= limite,
                )
            ).all()
        for renovacion_id in pendientes:
            self.resolver(renovacion_id)
        return len(pendientes)


def _poliza(sesion: Session, poliza_id: UUID, cliente_id: UUID) -> PolizaModel:
    poliza = sesion.get(PolizaModel, poliza_id)
    if poliza is None or poliza.cliente_id != cliente_id:
        raise PolizaNoEncontrada()
    return poliza


def _por_clave(
    sesion: Session, cliente_id: UUID, clave: UUID, poliza_id: UUID, solicitud_hash: str
) -> RenovacionModel | None:
    existente = sesion.scalars(
        select(RenovacionModel).where(
            RenovacionModel.cliente_id == cliente_id,
            RenovacionModel.idempotency_key == clave,
        )
    ).first()
    if existente is not None and (
        existente.poliza_id != poliza_id or existente.solicitud_hash != solicitud_hash
    ):
        raise IdempotenciaConflictiva()
    return existente


def _reserva(poliza_id: UUID, fin_anterior: str) -> str:
    return f"{poliza_id}:{fin_anterior}"


def _reserva_activa(sesion: Session, poliza_id: UUID) -> UUID | None:
    poliza = sesion.get(PolizaModel, poliza_id)
    if poliza is None:
        return None
    return sesion.scalar(
        select(RenovacionModel.renovacion_id).where(
            RenovacionModel.reserva_periodo == _reserva(poliza_id, poliza.fecha_fin.isoformat())
        )
    )


def _utc(instante: datetime) -> datetime:
    # SQLite devuelve instantes sin zona aunque se guardaron en UTC.
    return instante.replace(tzinfo=UTC) if instante.tzinfo is None else instante.astimezone(UTC)


def _serializar(renovacion: RenovacionModel) -> dict[str, object]:
    condiciones = renovacion.condiciones
    return {
        "id": str(renovacion.renovacion_id),
        "polizaId": str(renovacion.poliza_id),
        "estado": "pendiente" if renovacion.estado == "pago_pendiente" else renovacion.estado,
        "motivo": renovacion.motivo,
        "finAnterior": renovacion.fin_anterior.isoformat(),
        "nuevaVigencia": condiciones["nuevaVigencia"],
        "coberturas": condiciones["coberturas"],
        "prima": condiciones["prima"],
        "condiciones": condiciones["condiciones"],
        "condicionesHash": renovacion.condiciones_hash,
        "reglaVersion": renovacion.regla_version,
        "creadaEn": _utc(renovacion.creada_en).isoformat(),
        "actualizadaEn": _utc(renovacion.actualizada_en).isoformat(),
        "simulada": True,
    }
