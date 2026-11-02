import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.application.ports import ReservaEmision, ReservaPagoSimulado
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    PagoEnProcesoError,
    PagoNoEncontradoError,
    PagoNoPermitidoError,
    ServicioPolizasNoDisponibleError,
    SolicitudNoEncontradaError,
    SuscripcionNoEncontradaError,
)
from cotizacion.domain.pago import (
    PagoSimulado,
    PagoSimuladoPendiente,
    PagoSimuladoRechazado,
    ResultadoPagoSimulado,
    ResultadoSimuladorPago,
    huella_pago_simulado,
)
from cotizacion.domain.productos import PRODUCTOS
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    CotizacionModel,
    DecisionSuscripcionModel,
    EfectoPagoSimuladoModel,
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
        conciliar_emision: bool = False,
    ) -> None:
        if conciliar_emision and not emitir_polizas:
            raise ValueError("La conciliacion requiere la emision academica habilitada.")
        self._sesiones = sesiones
        self._emitir_polizas = emitir_polizas
        self._conciliar_emision = conciliar_emision

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

    def obtener_pago_simulado(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID | None = None
    ) -> ResultadoPagoSimulado:
        with self._sesiones() as sesion:
            self._solicitud(sesion, solicitud_id, cliente_id)
            if idempotency_key is not None:
                resultado = self._por_clave(sesion, solicitud_id, cliente_id, idempotency_key)
                if resultado is None:
                    raise PagoNoEncontradoError()
                return resultado
            pago = sesion.scalar(
                select(PagoSimuladoModel).where(
                    PagoSimuladoModel.solicitud_id == solicitud_id,
                    PagoSimuladoModel.cliente_id == cliente_id,
                )
            )
            if pago is not None:
                return _a_dominio(pago)
            intento = sesion.scalar(
                select(IntentoPagoSimuladoModel)
                .where(
                    IntentoPagoSimuladoModel.solicitud_id == solicitud_id,
                    IntentoPagoSimuladoModel.cliente_id == cliente_id,
                )
                .order_by(
                    (IntentoPagoSimuladoModel.resultado == "pendiente").desc(),
                    IntentoPagoSimuladoModel.creada_en.desc(),
                    IntentoPagoSimuladoModel.intento_id.desc(),
                )
                .limit(1)
            )
            if intento is None:
                raise PagoNoEncontradoError()
            return self._resultado_intento(sesion, intento)

    def obtener_intento_pago_simulado(self, intento_id: UUID) -> ResultadoPagoSimulado:
        with self._sesiones() as sesion:
            intento = sesion.get(IntentoPagoSimuladoModel, intento_id)
            if intento is None:
                raise PagoNoEncontradoError()
            return self._resultado_intento(sesion, intento)

    def reservar_pago_simulado(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID, ahora: datetime
    ) -> tuple[ResultadoPagoSimulado, bool]:
        ahora = _utc(ahora)
        try:
            with self._sesiones.begin() as sesion:
                solicitud = self._solicitud(sesion, solicitud_id, cliente_id)
                existente = self._existente(sesion, solicitud_id, cliente_id, idempotency_key)
                if existente is not None:
                    return existente, False
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
                guardia = sesion.scalar(
                    update(SagaEmisionModel)
                    .where(
                        SagaEmisionModel.suscripcion_id == suscripcion.suscripcion_id,
                        SagaEmisionModel.estado == "pago_pendiente",
                        SagaEmisionModel.version == saga.version,
                    )
                    .values(version=SagaEmisionModel.version)
                    .returning(SagaEmisionModel.suscripcion_id)
                )
                if guardia is None:
                    raise PagoNoPermitidoError()
                existente = self._existente(sesion, solicitud_id, cliente_id, idempotency_key)
                if existente is not None:
                    return existente, False
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
                    resultado="pendiente",
                    evaluado_en=None,
                    saga_version=saga.version,
                    saga_actualizada_en=_utc(saga.actualizada_en),
                    creada_en=ahora,
                    emision_solicitada=self._emitir_polizas,
                    conciliacion_emision_solicitada=self._conciliar_emision,
                    intentos_conciliacion=0,
                    proximo_intento_en=ahora + timedelta(seconds=2),
                )
                pendiente = _pendiente_a_dominio(intento)
                intento.huella = huella_pago_simulado(pendiente)
                sesion.add(intento)
                sesion.flush()
                return pendiente, True
        except (IntegrityError, PagoNoPermitidoError):
            with self._sesiones() as sesion:
                existente = self._existente(sesion, solicitud_id, cliente_id, idempotency_key)
                if existente is not None:
                    return existente, False
            raise

    def reclamar_pago_simulado(
        self, ahora: datetime, intento_id: UUID | None = None, lease_segundos: int = 30
    ) -> ReservaPagoSimulado | None:
        if lease_segundos <= 0:
            raise ValueError("El lease debe ser positivo.")
        trabajo = IntentoPagoSimuladoModel
        disponible = and_(
            trabajo.resultado == "pendiente",
            or_(trabajo.lease_token.is_(None), trabajo.lease_hasta <= ahora),
        )
        candidato = (
            select(trabajo.intento_id)
            .where(disponible)
            .where(
                trabajo.intento_id == intento_id
                if intento_id is not None
                else trabajo.proximo_intento_en <= ahora
            )
            .order_by(trabajo.proximo_intento_en, trabajo.intento_id)
            .limit(1)
            .scalar_subquery()
        )
        with self._sesiones.begin() as sesion:
            token = uuid4()
            intento = sesion.scalar(
                update(trabajo)
                .where(trabajo.intento_id == candidato, disponible)
                .values(
                    lease_token=token,
                    lease_hasta=ahora + timedelta(seconds=lease_segundos),
                    intentos_conciliacion=trabajo.intentos_conciliacion + 1,
                )
                .returning(trabajo)
                .execution_options(synchronize_session=False)
            )
            if intento is None:
                return None
            pendiente = _pendiente_a_dominio(intento)
            if intento.huella is None or intento.huella != huella_pago_simulado(pendiente):
                raise ValueError("La instantanea del intento de pago no es integra.")
            return ReservaPagoSimulado(
                pendiente, intento.huella, token, intento.intentos_conciliacion
            )

    def aplazar_pago_simulado(
        self, reserva: ReservaPagoSimulado, ahora: datetime, codigo: str
    ) -> bool:
        with self._sesiones.begin() as sesion:
            actualizado = sesion.scalar(
                update(IntentoPagoSimuladoModel)
                .where(
                    IntentoPagoSimuladoModel.intento_id == reserva.pago.pago_id,
                    IntentoPagoSimuladoModel.resultado == "pendiente",
                    IntentoPagoSimuladoModel.lease_token == reserva.token,
                    IntentoPagoSimuladoModel.lease_hasta > ahora,
                )
                .values(
                    lease_token=None,
                    lease_hasta=None,
                    ultimo_error=codigo,
                    proximo_intento_en=ahora
                    + timedelta(seconds=min(2 ** min(reserva.intentos, 5), 30)),
                )
                .returning(IntentoPagoSimuladoModel.intento_id)
            )
        return actualizado is not None

    def finalizar_pago_simulado(
        self, reserva: ReservaPagoSimulado, resultado: ResultadoSimuladorPago, ahora: datetime
    ) -> ResultadoPagoSimulado:
        if not isinstance(resultado, ResultadoSimuladorPago):
            raise ValueError("El resultado terminal del simulador no es valido.")
        with self._sesiones.begin() as sesion:
            intento = sesion.get(IntentoPagoSimuladoModel, reserva.pago.pago_id)
            if intento is None:
                raise PagoNoEncontradoError()
            if (
                intento.huella != reserva.huella
                or huella_pago_simulado(_pendiente_a_dominio(intento)) != reserva.huella
            ):
                raise ConflictoIdempotenciaError()
            actualizado = sesion.scalar(
                update(IntentoPagoSimuladoModel)
                .where(
                    IntentoPagoSimuladoModel.intento_id == intento.intento_id,
                    IntentoPagoSimuladoModel.resultado == "pendiente",
                    IntentoPagoSimuladoModel.lease_token == reserva.token,
                    IntentoPagoSimuladoModel.lease_hasta > ahora,
                )
                .values(
                    resultado=resultado.value,
                    evaluado_en=ahora,
                    lease_token=None,
                    lease_hasta=None,
                    ultimo_error=None,
                )
                .returning(IntentoPagoSimuladoModel.intento_id)
                .execution_options(synchronize_session=False)
            )
            sesion.refresh(intento)
            if actualizado is None:
                return self._resultado_intento(sesion, intento)
            efecto = sesion.get(EfectoPagoSimuladoModel, intento.intento_id)
            if (
                efecto is None
                or efecto.huella != reserva.huella
                or efecto.resultado != resultado.value
            ):
                raise ValueError("El terminal no coincide con el efecto durable del simulador.")
            confirmado = resultado is ResultadoSimuladorPago.CONFIRMADO
            transicion = sesion.scalar(
                update(SagaEmisionModel)
                .where(
                    SagaEmisionModel.suscripcion_id == intento.suscripcion_id,
                    SagaEmisionModel.estado == "pago_pendiente",
                    SagaEmisionModel.version == intento.saga_version,
                )
                .values(
                    **(
                        {
                            "estado": "pago_confirmado",
                            "version": intento.saga_version + 1,
                            "actualizada_en": ahora,
                            "ultimo_error": None,
                        }
                        if confirmado
                        else {"version": SagaEmisionModel.version}
                    )
                )
                .returning(SagaEmisionModel.suscripcion_id)
            )
            if transicion is None:
                raise PagoNoPermitidoError()
            if not confirmado:
                sesion.add(
                    OutboxEventoModel(
                        evento_id=uuid4(),
                        tipo="PagoSimuladoRechazado",
                        agregado_id=intento.suscripcion_id,
                        payload={
                            "intentoId": str(intento.intento_id),
                            "solicitudId": intento.solicitud_id,
                            "suscripcionId": str(intento.suscripcion_id),
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
                solicitud_id=intento.solicitud_id,
                suscripcion_id=intento.suscripcion_id,
                cliente_id=intento.cliente_id,
                idempotency_key=intento.idempotency_key,
                oferta_id=intento.oferta_id,
                oferta_version=intento.oferta_version,
                monto=intento.monto,
                moneda=intento.moneda,
                periodicidad=intento.periodicidad,
                confirmada_en=ahora,
                saga_version=intento.saga_version + 1,
                emision_solicitada=intento.emision_solicitada,
            )
            sesion.add(pago)
            sesion.flush()
            if intento.conciliacion_emision_solicitada:
                sesion.add(
                    ConciliacionEmisionModel(
                        pago_id=pago.pago_id,
                        autorizada_en=intento.creada_en,
                        estado="pendiente",
                        intentos=0,
                        proximo_intento_en=ahora + timedelta(seconds=2),
                    )
                )
            sesion.execute(
                update(SuscripcionModel)
                .where(SuscripcionModel.suscripcion_id == intento.suscripcion_id)
                .values(actualizada_en=ahora)
            )
            sesion.add(
                OutboxEventoModel(
                    evento_id=uuid4(),
                    tipo="PagoSimuladoConfirmado",
                    agregado_id=pago.suscripcion_id,
                    payload={
                        "pagoId": str(pago.pago_id),
                        "solicitudId": pago.solicitud_id,
                        "suscripcionId": str(pago.suscripcion_id),
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

    @classmethod
    def _existente(
        cls, sesion: Session, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID
    ) -> ResultadoPagoSimulado | None:
        pago = sesion.scalar(
            select(PagoSimuladoModel).where(
                PagoSimuladoModel.solicitud_id == solicitud_id,
                PagoSimuladoModel.cliente_id == cliente_id,
            )
        )
        resultado = cls._por_clave(sesion, solicitud_id, cliente_id, idempotency_key)
        if resultado is not None:
            return resultado
        if pago is not None:
            return _a_dominio(pago)
        pendiente = sesion.scalar(
            select(IntentoPagoSimuladoModel).where(
                IntentoPagoSimuladoModel.solicitud_id == solicitud_id,
                IntentoPagoSimuladoModel.cliente_id == cliente_id,
                IntentoPagoSimuladoModel.resultado == "pendiente",
            )
        )
        if pendiente is not None:
            if pendiente.idempotency_key == idempotency_key:
                return cls._resultado_intento(sesion, pendiente)
            raise PagoEnProcesoError(pendiente.intento_id)
        return None

    @classmethod
    def _por_clave(
        cls, sesion: Session, solicitud_id: str, cliente_id: UUID, clave: UUID
    ) -> ResultadoPagoSimulado | None:
        intento = sesion.scalar(
            select(IntentoPagoSimuladoModel).where(
                IntentoPagoSimuladoModel.cliente_id == cliente_id,
                IntentoPagoSimuladoModel.idempotency_key == clave,
            )
        )
        if intento is not None:
            if intento.solicitud_id != solicitud_id:
                raise ConflictoIdempotenciaError()
            return cls._resultado_intento(sesion, intento)
        historico = sesion.scalar(
            select(PagoSimuladoModel).where(
                PagoSimuladoModel.cliente_id == cliente_id,
                PagoSimuladoModel.idempotency_key == clave,
            )
        )
        if historico is not None:
            if historico.solicitud_id != solicitud_id:
                raise ConflictoIdempotenciaError()
            return _a_dominio(historico)
        return None

    @staticmethod
    def _resultado_intento(
        sesion: Session, intento: IntentoPagoSimuladoModel
    ) -> ResultadoPagoSimulado:
        if intento.resultado == "pendiente":
            return _pendiente_a_dominio(intento)
        if intento.resultado == "rechazado":
            return _rechazo_a_dominio(intento)
        pago = sesion.get(PagoSimuladoModel, intento.intento_id)
        if intento.resultado != "confirmado" or pago is None:
            raise ValueError("El resultado del intento de pago no es consistente.")
        return _a_dominio(pago)


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
    if intento.evaluado_en is None:
        raise ValueError("El rechazo no tiene fecha terminal.")
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


def _pendiente_a_dominio(intento: IntentoPagoSimuladoModel) -> PagoSimuladoPendiente:
    return PagoSimuladoPendiente(
        pago_id=intento.intento_id,
        solicitud_id=intento.solicitud_id,
        suscripcion_id=intento.suscripcion_id,
        cliente_id=intento.cliente_id,
        oferta_id=intento.oferta_id,
        oferta_version=intento.oferta_version,
        importe=Dinero(intento.monto, intento.moneda),
        periodicidad=intento.periodicidad,
        creada_en=_utc(intento.creada_en),
        saga_version=intento.saga_version,
        saga_actualizada_en=_utc(intento.saga_actualizada_en),
        emision_solicitada=intento.emision_solicitada,
        conciliacion_emision_solicitada=intento.conciliacion_emision_solicitada,
    )
