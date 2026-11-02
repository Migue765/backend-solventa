import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.application.ports import ProveedorPagoSimulado, ReservaEmision
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    PagoNoEncontradoError,
    PagoNoPermitidoError,
    ServicioPolizasNoDisponibleError,
    SolicitudNoEncontradaError,
    SuscripcionNoEncontradaError,
)
from cotizacion.domain.pago import (
    PagoSimulado,
    PagoSimuladoRechazado,
    ResultadoPagoSimulado,
    ResultadoSimuladorPago,
)
from cotizacion.domain.productos import PRODUCTOS
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    CotizacionModel,
    DecisionSuscripcionModel,
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
    SeleccionOfertaModel,
    SuscripcionModel,
)

logger = logging.getLogger(__name__)


class RepositorioPagosSqlAlchemy:
    def __init__(
        self,
        sesiones: sessionmaker[Session],
        emitir_polizas: bool = False,
        proveedor: ProveedorPagoSimulado | None = None,
        conciliar_emision: bool = False,
    ) -> None:
        if conciliar_emision and not emitir_polizas:
            raise ValueError("La conciliacion requiere la emision academica habilitada.")
        self._sesiones = sesiones
        self._emitir_polizas = emitir_polizas
        self._conciliar_emision = conciliar_emision
        self._proveedor = proveedor if proveedor is not None else SimuladorPago()

    def evidencia_emision(self, pago: PagoSimulado) -> dict[str, object] | None:
        with self._sesiones() as sesion:
            modelo = sesion.get(PagoSimuladoModel, pago.pago_id)
            if modelo is None:
                raise ServicioPolizasNoDisponibleError()
            if not modelo.emision_solicitada:
                return None
            solicitud = self._solicitud(sesion, modelo.solicitud_id, modelo.cliente_id)
            tipo = next(
                producto.producto_id
                for producto in PRODUCTOS
                if producto.producto_interno_id == solicitud.producto_id
            )
            datos = solicitud.datos_producto or {}
            fechas: dict[str, object] | None = None
            if tipo == "travel":
                fechas = {"inicio": datos["departureDate"], "fin": datos["returnDate"]}
            elif tipo == "parametric":
                fechas = {"inicio": datos["flightDate"], "fin": datos["flightDate"]}
            elif tipo == "parametric-weather":
                fechas = {"inicio": datos["startDate"], "fin": datos["endDate"]}
            return {
                "clienteId": str(modelo.cliente_id),
                "suscripcionId": str(modelo.suscripcion_id),
                "pagoId": str(modelo.pago_id),
                "tipo": tipo,
                "confirmadaEn": _utc(modelo.confirmada_en).isoformat(),
                "simulada": True,
                "fechasContratadas": fechas,
            }

    def marcar_emitida(self, pago: PagoSimulado, poliza_id: UUID) -> None:
        with self._sesiones.begin() as sesion:
            ahora = datetime.now(UTC)
            actualizada = sesion.scalar(
                update(SagaEmisionModel)
                .where(
                    SagaEmisionModel.suscripcion_id == pago.suscripcion_id,
                    SagaEmisionModel.estado == "pago_confirmado",
                    SagaEmisionModel.version == pago.saga_version,
                )
                .values(
                    estado="emitida",
                    version=SagaEmisionModel.version + 1,
                    actualizada_en=ahora,
                )
                .returning(SagaEmisionModel.suscripcion_id)
            )
            if actualizada is None:
                saga = sesion.get(SagaEmisionModel, pago.suscripcion_id)
                if saga is None or saga.estado != "emitida":
                    raise ServicioPolizasNoDisponibleError()
            else:
                sesion.add(
                    OutboxEventoModel(
                        evento_id=uuid4(),
                        tipo="PolizaAcademicaEmitida",
                        agregado_id=pago.suscripcion_id,
                        payload={"polizaId": str(poliza_id), "simulada": True},
                        creado_en=ahora,
                        intentos=0,
                    )
                )
            if self._conciliar_emision:
                sesion.execute(
                    update(ConciliacionEmisionModel)
                    .where(
                        ConciliacionEmisionModel.pago_id == pago.pago_id,
                        ConciliacionEmisionModel.estado != "completada",
                    )
                    .values(
                        estado="completada",
                        completada_en=ahora,
                        lease_token=None,
                        lease_hasta=None,
                        ultimo_error=None,
                    )
                )

    def reservar_emision(
        self, ahora: datetime, max_intentos: int = 8, lease_segundos: int = 30
    ) -> ReservaEmision | None:
        if not self._conciliar_emision:
            return None
        trabajo = ConciliacionEmisionModel
        disponibles = and_(
            trabajo.proximo_intento_en <= ahora,
            or_(
                trabajo.estado == "pendiente",
                and_(trabajo.estado == "en_proceso", trabajo.lease_hasta <= ahora),
            ),
        )
        with self._sesiones.begin() as sesion:
            agotado = (
                select(trabajo.pago_id)
                .where(disponibles, trabajo.intentos >= max_intentos)
                .order_by(trabajo.proximo_intento_en, trabajo.pago_id)
                .limit(1)
                .scalar_subquery()
            )
            agotados = sesion.scalars(
                update(trabajo)
                .where(trabajo.pago_id == agotado, disponibles, trabajo.intentos >= max_intentos)
                .values(
                    estado="agotada",
                    ultimo_error="limite_reservas",
                    lease_token=None,
                    lease_hasta=None,
                )
                .returning(trabajo.pago_id)
                .execution_options(synchronize_session=False)
            ).all()
            if agotados:
                logger.error("conciliacion_emision.agotada cantidad=%s", len(agotados))
            candidato = (
                select(trabajo.pago_id)
                .where(disponibles, trabajo.intentos < max_intentos)
                .order_by(trabajo.proximo_intento_en, trabajo.pago_id)
                .limit(1)
                .scalar_subquery()
            )
            token = uuid4()
            reserva = sesion.execute(
                update(trabajo)
                .where(trabajo.pago_id == candidato, disponibles, trabajo.intentos < max_intentos)
                .values(
                    estado="en_proceso",
                    lease_token=token,
                    lease_hasta=ahora + timedelta(seconds=lease_segundos),
                    intentos=trabajo.intentos + 1,
                )
                .returning(trabajo.pago_id, trabajo.intentos)
                .execution_options(synchronize_session=False)
            ).one_or_none()
            if reserva is None:
                return None
            pago = sesion.get(PagoSimuladoModel, reserva.pago_id)
            if pago is None:
                raise ServicioPolizasNoDisponibleError()
            saga = sesion.get(SagaEmisionModel, pago.suscripcion_id)
            if saga is not None and saga.estado == "emitida":
                sesion.execute(
                    update(trabajo)
                    .where(trabajo.pago_id == pago.pago_id)
                    .values(
                        estado="completada",
                        completada_en=ahora,
                        lease_token=None,
                        lease_hasta=None,
                        ultimo_error=None,
                    )
                )
                return None
            if (
                not pago.emision_solicitada
                or saga is None
                or saga.estado != "pago_confirmado"
                or saga.version != pago.saga_version
            ):
                sesion.execute(
                    update(trabajo)
                    .where(trabajo.pago_id == pago.pago_id)
                    .values(
                        estado="agotada",
                        lease_token=None,
                        lease_hasta=None,
                        ultimo_error="estado_no_elegible",
                    )
                )
                logger.error("conciliacion_emision.estado_no_elegible pago_id=%s", pago.pago_id)
                return None
            return ReservaEmision(_a_dominio(pago), token, reserva.intentos)

    def aplazar_emision(
        self, reserva: ReservaEmision, ahora: datetime, codigo: str, max_intentos: int = 8
    ) -> bool:
        with self._sesiones.begin() as sesion:
            resultado = sesion.scalar(
                update(ConciliacionEmisionModel)
                .where(
                    ConciliacionEmisionModel.pago_id == reserva.pago.pago_id,
                    ConciliacionEmisionModel.estado == "en_proceso",
                    ConciliacionEmisionModel.lease_token == reserva.token,
                )
                .values(
                    estado="agotada" if reserva.intentos >= max_intentos else "pendiente",
                    proximo_intento_en=ahora
                    + timedelta(seconds=min(2 ** min(reserva.intentos, 5), 30)),
                    ultimo_error=codigo,
                    lease_token=None,
                    lease_hasta=None,
                )
                .returning(ConciliacionEmisionModel.pago_id)
            )
        if resultado is not None and reserva.intentos >= max_intentos:
            logger.error(
                "conciliacion_emision.agotada pago_id=%s intentos=%s",
                reserva.pago.pago_id,
                reserva.intentos,
            )
        return resultado is not None

    def obtener_pago_simulado(self, solicitud_id: str, cliente_id: UUID) -> ResultadoPagoSimulado:
        with self._sesiones() as sesion:
            self._solicitud(sesion, solicitud_id, cliente_id)
            pago = self._existente(sesion, solicitud_id, cliente_id)
            if pago is None:
                rechazo = sesion.scalar(
                    select(IntentoPagoSimuladoModel)
                    .where(
                        IntentoPagoSimuladoModel.solicitud_id == solicitud_id,
                        IntentoPagoSimuladoModel.cliente_id == cliente_id,
                        IntentoPagoSimuladoModel.resultado == "rechazado",
                    )
                    .order_by(
                        IntentoPagoSimuladoModel.evaluado_en.desc(),
                        IntentoPagoSimuladoModel.intento_id.desc(),
                    )
                    .limit(1)
                )
                if rechazo is None:
                    raise PagoNoEncontradoError()
                return _rechazo_a_dominio(rechazo)
            return pago

    def confirmar_pago_simulado(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID, ahora: datetime
    ) -> ResultadoPagoSimulado:
        try:
            with self._sesiones.begin() as sesion:
                solicitud = self._solicitud(sesion, solicitud_id, cliente_id)
                existente = self._existente(sesion, solicitud_id, cliente_id, idempotency_key)
                if existente is not None:
                    return existente
                suscripcion = sesion.scalar(
                    select(SuscripcionModel).where(
                        SuscripcionModel.solicitud_id == solicitud_id,
                        SuscripcionModel.cliente_id == cliente_id,
                    )
                )
                if suscripcion is None:
                    raise SuscripcionNoEncontradaError()
                seleccion = sesion.get(SeleccionOfertaModel, solicitud.cotizacion_id)
                decision = sesion.get(DecisionSuscripcionModel, suscripcion.suscripcion_id)
                saga = sesion.get(SagaEmisionModel, suscripcion.suscripcion_id)
                if (
                    suscripcion.estado != "aprobada"
                    or decision is None
                    or decision.resultado != "aprobada"
                    or saga is None
                    or saga.estado != "pago_pendiente"
                    or seleccion is None
                    or seleccion.oferta_id != suscripcion.oferta_id
                    or seleccion.version != suscripcion.oferta_version
                    or seleccion.prima_mensual <= 0
                    or seleccion.periodicidad
                    not in {"mensual", "por_viaje", "por_vuelo", "por_periodo"}
                    or _utc(solicitud.valida_hasta) <= ahora
                ):
                    raise PagoNoPermitidoError()
                resultado_simulado = self._proveedor.evaluar()
                if not isinstance(resultado_simulado, ResultadoSimuladorPago):
                    raise ValueError("El simulador devolvio un resultado invalido.")
                intento = IntentoPagoSimuladoModel(
                    intento_id=uuid4(),
                    solicitud_id=solicitud_id,
                    suscripcion_id=suscripcion.suscripcion_id,
                    cliente_id=cliente_id,
                    idempotency_key=idempotency_key,
                    oferta_id=seleccion.oferta_id,
                    oferta_version=seleccion.version,
                    monto=seleccion.prima_mensual,
                    moneda=seleccion.moneda,
                    periodicidad=seleccion.periodicidad,
                    resultado=resultado_simulado.value,
                    evaluado_en=ahora,
                    saga_version=saga.version,
                    saga_actualizada_en=_utc(saga.actualizada_en),
                )
                sesion.add(intento)
                # La clave se reserva para ambos resultados en la misma transaccion.
                sesion.flush()
                if resultado_simulado is ResultadoSimuladorPago.RECHAZADO:
                    pendiente = sesion.scalar(
                        update(SagaEmisionModel)
                        .where(
                            SagaEmisionModel.suscripcion_id == suscripcion.suscripcion_id,
                            SagaEmisionModel.estado == "pago_pendiente",
                            SagaEmisionModel.version == intento.saga_version,
                        )
                        .values(version=SagaEmisionModel.version)
                        .returning(SagaEmisionModel.suscripcion_id)
                    )
                    if pendiente is None:
                        raise PagoNoPermitidoError()
                    sesion.add(
                        OutboxEventoModel(
                            evento_id=uuid4(),
                            tipo="PagoSimuladoRechazado",
                            agregado_id=suscripcion.suscripcion_id,
                            payload={
                                "intentoId": str(intento.intento_id),
                                "solicitudId": solicitud_id,
                                "suscripcionId": str(suscripcion.suscripcion_id),
                                "simulada": True,
                                "motivo": "rechazo_simulado",
                            },
                            creado_en=ahora,
                            intentos=0,
                        )
                    )
                    return _rechazo_a_dominio(intento)
                pago = PagoSimuladoModel(
                    pago_id=intento.intento_id,
                    solicitud_id=solicitud_id,
                    suscripcion_id=suscripcion.suscripcion_id,
                    cliente_id=cliente_id,
                    idempotency_key=idempotency_key,
                    oferta_id=seleccion.oferta_id,
                    oferta_version=seleccion.version,
                    monto=seleccion.prima_mensual,
                    moneda=seleccion.moneda,
                    periodicidad=seleccion.periodicidad,
                    confirmada_en=ahora,
                    saga_version=saga.version + 1,
                    emision_solicitada=self._emitir_polizas,
                )
                sesion.add(pago)
                # La unicidad serializa pagos rivales; el CAS protege la transición de saga.
                sesion.flush()
                if self._conciliar_emision:
                    sesion.add(
                        ConciliacionEmisionModel(
                            pago_id=pago.pago_id,
                            autorizada_en=ahora,
                            estado="pendiente",
                            intentos=0,
                            proximo_intento_en=ahora + timedelta(seconds=2),
                        )
                    )
                resultado = sesion.scalar(
                    update(SagaEmisionModel)
                    .where(
                        SagaEmisionModel.suscripcion_id == suscripcion.suscripcion_id,
                        SagaEmisionModel.estado == "pago_pendiente",
                        SagaEmisionModel.version == saga.version,
                    )
                    .values(
                        estado="pago_confirmado",
                        version=pago.saga_version,
                        actualizada_en=ahora,
                        ultimo_error=None,
                    )
                    .returning(SagaEmisionModel.suscripcion_id)
                )
                if resultado is None:
                    raise PagoNoPermitidoError()
                suscripcion.actualizada_en = ahora
                sesion.add(
                    OutboxEventoModel(
                        evento_id=uuid4(),
                        tipo="PagoSimuladoConfirmado",
                        agregado_id=suscripcion.suscripcion_id,
                        payload={
                            "pagoId": str(pago.pago_id),
                            "solicitudId": solicitud_id,
                            "suscripcionId": str(suscripcion.suscripcion_id),
                            "simulada": True,
                            "referenciaInterna": f"SIM-{pago.pago_id}",
                            "importe": {"monto": str(pago.monto), "moneda": pago.moneda},
                            "periodicidad": pago.periodicidad,
                        },
                        creado_en=ahora,
                        intentos=0,
                    )
                )
                return _a_dominio(pago)
        except (IntegrityError, PagoNoPermitidoError):
            # Un rival puede confirmar entre la lectura del pago y la de saga.
            # Solo se recupera un pago persistido verificable; otros errores se propagan.
            with self._sesiones() as sesion:
                existente = self._existente(sesion, solicitud_id, cliente_id, idempotency_key)
                if existente is not None:
                    return existente
            raise

    @staticmethod
    def _solicitud(sesion: Session, solicitud_id: str, cliente_id: UUID) -> CotizacionModel:
        solicitud = sesion.scalar(
            select(CotizacionModel).where(
                CotizacionModel.solicitud_id == solicitud_id,
                CotizacionModel.cliente_id == cliente_id,
            )
        )
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        return solicitud

    @staticmethod
    def _existente(
        sesion: Session, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID | None = None
    ) -> ResultadoPagoSimulado | None:
        if idempotency_key is not None:
            intento = sesion.scalar(
                select(IntentoPagoSimuladoModel).where(
                    IntentoPagoSimuladoModel.cliente_id == cliente_id,
                    IntentoPagoSimuladoModel.idempotency_key == idempotency_key,
                )
            )
            if intento is not None:
                if intento.solicitud_id != solicitud_id:
                    raise ConflictoIdempotenciaError()
                if intento.resultado == "rechazado":
                    return _rechazo_a_dominio(intento)
            por_clave = sesion.scalar(
                select(PagoSimuladoModel).where(
                    PagoSimuladoModel.cliente_id == cliente_id,
                    PagoSimuladoModel.idempotency_key == idempotency_key,
                )
            )
            if por_clave is not None and por_clave.solicitud_id != solicitud_id:
                raise ConflictoIdempotenciaError()
        pago = sesion.scalar(
            select(PagoSimuladoModel).where(
                PagoSimuladoModel.solicitud_id == solicitud_id,
                PagoSimuladoModel.cliente_id == cliente_id,
            )
        )
        return _a_dominio(pago) if pago else None


def _utc(fecha: datetime) -> datetime:
    return fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha


def _a_dominio(pago: PagoSimuladoModel) -> PagoSimulado:
    return PagoSimulado(
        pago_id=pago.pago_id,
        solicitud_id=pago.solicitud_id,
        suscripcion_id=pago.suscripcion_id,
        oferta_id=pago.oferta_id,
        oferta_version=pago.oferta_version,
        importe=Dinero(pago.monto, pago.moneda),
        periodicidad=pago.periodicidad,
        confirmada_en=_utc(pago.confirmada_en),
        saga_version=pago.saga_version,
    )


def _rechazo_a_dominio(intento: IntentoPagoSimuladoModel) -> PagoSimuladoRechazado:
    return PagoSimuladoRechazado(
        pago_id=intento.intento_id,
        solicitud_id=intento.solicitud_id,
        suscripcion_id=intento.suscripcion_id,
        oferta_id=intento.oferta_id,
        oferta_version=intento.oferta_version,
        importe=Dinero(intento.monto, intento.moneda),
        periodicidad=intento.periodicidad,
        rechazada_en=_utc(intento.evaluado_en),
        saga_version=intento.saga_version,
        saga_actualizada_en=_utc(intento.saga_actualizada_en),
    )
