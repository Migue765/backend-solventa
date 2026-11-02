from datetime import UTC, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, raiseload, sessionmaker

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion, EstadoCotizacion
from cotizacion.domain.decision import InstantaneaDecision
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    OfertaNoDisponibleError,
    SeleccionDesactualizadaError,
    SeleccionNoModificableError,
    SolicitudNoEncontradaError,
)
from cotizacion.domain.ofertas import SeleccionOferta, SeleccionProvisional
from cotizacion.domain.productos import PRODUCTOS
from cotizacion.domain.solicitud import SolicitudCotizacion
from cotizacion.domain.suscripcion import (
    DecisionSuscripcion,
    EstadoSagaEmision,
    EstadoSuscripcion,
    SagaEmision,
    Suscripcion,
)
from cotizacion.infrastructure.persistence.models import (
    CoberturaCotizadaModel,
    CotizacionModel,
    DecisionSuscripcionModel,
    InstantaneaDecisionModel,
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
    SeleccionOfertaModel,
    SeleccionProvisionalModel,
    SuscripcionModel,
)


class RepositorioCotizacionesSqlAlchemy:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def guardar(self, cotizacion: Cotizacion) -> None:
        with self._sesiones.begin() as sesion:
            sesion.merge(_a_modelo(cotizacion))
            if cotizacion.instantanea and not sesion.get(
                InstantaneaDecisionModel, cotizacion.cotizacion_id
            ):
                sesion.add_all(_filas_de_decision(cotizacion))

    def obtener(self, cotizacion_id: UUID) -> Cotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.get(CotizacionModel, cotizacion_id)
            return _a_dominio(modelo) if modelo else None

    def consultar_dashboard(self, cliente_id: UUID) -> dict[str, object]:
        # Una sola consulta mantiene coherente la proyeccion incluso durante un pago.
        ultimo_rechazo = (
            select(IntentoPagoSimuladoModel.intento_id)
            .where(
                IntentoPagoSimuladoModel.solicitud_id == CotizacionModel.solicitud_id,
                IntentoPagoSimuladoModel.cliente_id == cliente_id,
                IntentoPagoSimuladoModel.resultado == "rechazado",
            )
            .order_by(
                IntentoPagoSimuladoModel.evaluado_en.desc(),
                IntentoPagoSimuladoModel.intento_id.desc(),
            )
            .limit(1)
            .correlate(CotizacionModel)
            .scalar_subquery()
        )
        consulta = (
            select(
                CotizacionModel,
                SeleccionOfertaModel,
                SuscripcionModel,
                SagaEmisionModel,
                PagoSimuladoModel,
                IntentoPagoSimuladoModel,
            )
            .outerjoin(
                SeleccionOfertaModel,
                SeleccionOfertaModel.cotizacion_id == CotizacionModel.cotizacion_id,
            )
            .outerjoin(
                SuscripcionModel,
                (SuscripcionModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (SuscripcionModel.cliente_id == cliente_id),
            )
            .outerjoin(
                SagaEmisionModel,
                SagaEmisionModel.suscripcion_id == SuscripcionModel.suscripcion_id,
            )
            .outerjoin(
                PagoSimuladoModel,
                (PagoSimuladoModel.suscripcion_id == SuscripcionModel.suscripcion_id)
                & (PagoSimuladoModel.cliente_id == cliente_id),
            )
            .outerjoin(
                IntentoPagoSimuladoModel,
                IntentoPagoSimuladoModel.intento_id == ultimo_rechazo,
            )
            .where(CotizacionModel.cliente_id == cliente_id)
            .options(raiseload(CotizacionModel.coberturas), raiseload(CotizacionModel.instantanea))
            .order_by(CotizacionModel.creada_en.desc(), CotizacionModel.cotizacion_id)
        )
        cotizaciones: list[dict[str, object]] = []
        suscripciones: list[dict[str, object]] = []
        pagos: list[dict[str, object]] = []
        with self._sesiones() as sesion:
            for cotizacion, seleccion, suscripcion, saga, pago, rechazo in sesion.execute(consulta):
                cotizaciones.append(
                    {
                        "id": str(cotizacion.cotizacion_id),
                        "solicitudId": cotizacion.solicitud_id,
                        "productoId": _producto_id_dashboard(cotizacion.producto_id),
                        "estado": cotizacion.estado,
                        "creadaEn": _fecha_dashboard(cotizacion.creada_en),
                        "validaHasta": _fecha_dashboard(cotizacion.valida_hasta),
                        "primaEstimada": {
                            "monto": str(cotizacion.prima_estimada),
                            "moneda": cotizacion.moneda,
                        },
                        "seleccion": (
                            {
                                "ofertaId": str(seleccion.oferta_id),
                                "nombreOferta": seleccion.nombre_oferta,
                                "prima": {
                                    "monto": str(seleccion.prima_mensual),
                                    "moneda": seleccion.moneda,
                                },
                                "periodicidad": seleccion.periodicidad,
                                "seleccionadaEn": _fecha_dashboard(seleccion.seleccionada_en),
                            }
                            if seleccion is not None
                            else None
                        ),
                    }
                )
                if suscripcion is not None:
                    if saga is None:
                        raise ValueError("Suscripcion persistida sin saga")
                    suscripciones.append(
                        {
                            "id": str(suscripcion.suscripcion_id),
                            "solicitudId": suscripcion.solicitud_id,
                            "cotizacionId": str(suscripcion.cotizacion_id),
                            "ofertaId": str(suscripcion.oferta_id),
                            "estado": suscripcion.estado,
                            "creadaEn": _fecha_dashboard(suscripcion.creada_en),
                            "actualizadaEn": _fecha_dashboard(suscripcion.actualizada_en),
                            "saga": {
                                "estado": saga.estado,
                                "version": saga.version,
                                "actualizadaEn": _fecha_dashboard(saga.actualizada_en),
                            },
                        }
                    )
                if pago is not None:
                    pagos.append(
                        {
                            "id": str(pago.pago_id),
                            "solicitudId": pago.solicitud_id,
                            "suscripcionId": str(pago.suscripcion_id),
                            "estado": "confirmado",
                            "simulada": True,
                            "importe": {"monto": str(pago.monto), "moneda": pago.moneda},
                            "periodicidad": pago.periodicidad,
                            "confirmadaEn": _fecha_dashboard(pago.confirmada_en),
                        }
                    )
                elif rechazo is not None:
                    pagos.append(
                        {
                            "id": str(rechazo.intento_id),
                            "solicitudId": rechazo.solicitud_id,
                            "suscripcionId": str(rechazo.suscripcion_id),
                            "estado": "rechazado",
                            "simulada": True,
                            "importe": {"monto": str(rechazo.monto), "moneda": rechazo.moneda},
                            "periodicidad": rechazo.periodicidad,
                            "rechazadaEn": _fecha_dashboard(rechazo.evaluado_en),
                            "motivo": "rechazo_simulado",
                        }
                    )
        return {"cotizaciones": cotizaciones, "suscripciones": suscripciones, "pagos": pagos}

    def guardar_solicitud(self, solicitud: SolicitudCotizacion) -> None:
        modelo = _a_modelo(solicitud.cotizacion)
        modelo.solicitud_id = solicitud.solicitud_id
        modelo.tipo_cliente = solicitud.tipo_cliente
        modelo.tipo_documento = solicitud.tipo_documento
        modelo.identificacion = solicitud.identificacion
        modelo.datos_producto = solicitud.datos_producto
        modelo.correo = solicitud.correo
        modelo.telefono = solicitud.telefono
        modelo.idempotency_key = solicitud.idempotency_key
        modelo.request_fingerprint = solicitud.request_fingerprint
        modelo.creada_en = solicitud.creada_en
        with self._sesiones.begin() as sesion:
            sesion.add(modelo)
            if solicitud.cotizacion.instantanea:
                sesion.add_all(_filas_de_decision(solicitud.cotizacion))

    def buscar_por_idempotencia(
        self, cliente_id: UUID, idempotency_key: UUID
    ) -> SolicitudCotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(CotizacionModel).where(
                    CotizacionModel.cliente_id == cliente_id,
                    CotizacionModel.idempotency_key == idempotency_key,
                )
            )
            return _solicitud_a_dominio(modelo) if modelo else None

    def obtener_solicitud(self, solicitud_id: str, cliente_id: UUID) -> SolicitudCotizacion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(CotizacionModel).where(
                    CotizacionModel.solicitud_id == solicitud_id,
                    CotizacionModel.cliente_id == cliente_id,
                )
            )
            return _solicitud_a_dominio(modelo) if modelo else None

    def guardar_seleccion(
        self, seleccion: SeleccionOferta, cliente_id: UUID, revision: int | None = None
    ) -> SeleccionOferta:
        with self._sesiones.begin() as sesion:
            vigente_hasta = _bloquear_seleccion(sesion, seleccion, cliente_id)
            existente = sesion.get(SeleccionOfertaModel, seleccion.cotizacion_id)
            if existente is not None:
                if existente.idempotency_key == seleccion.idempotency_key:
                    if existente.request_fingerprint != seleccion.request_fingerprint:
                        raise ConflictoIdempotenciaError()
                    return _seleccion_a_dominio(existente)
                if (
                    existente.oferta_id != seleccion.oferta_id
                    or existente.version != seleccion.version
                ):
                    raise OfertaNoDisponibleError()
            provisional = _seleccion_activa(sesion, seleccion.cotizacion_id)
            if provisional is not None or revision is not None:
                if (
                    provisional is None
                    or provisional.revision != revision
                    or provisional.oferta_id != seleccion.oferta_id
                    or provisional.version != seleccion.version
                ):
                    raise SeleccionDesactualizadaError()
            if existente is not None:
                return _seleccion_a_dominio(existente)
            if vigente_hasta <= datetime.now(UTC):
                raise OfertaNoDisponibleError()
            sesion.add(
                SeleccionOfertaModel(
                    cotizacion_id=seleccion.cotizacion_id,
                    solicitud_id=seleccion.solicitud_id,
                    oferta_id=seleccion.oferta_id,
                    version=seleccion.version,
                    nombre_oferta=seleccion.nombre_oferta,
                    prima_mensual=seleccion.prima_mensual.monto,
                    periodicidad=seleccion.periodicidad,
                    moneda=seleccion.prima_mensual.moneda,
                    seleccionada_en=seleccion.seleccionada_en,
                    idempotency_key=seleccion.idempotency_key,
                    request_fingerprint=seleccion.request_fingerprint,
                )
            )
            return seleccion

    def obtener_seleccion(self, solicitud_id: str) -> SeleccionOferta | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(SeleccionOfertaModel).where(
                    SeleccionOfertaModel.solicitud_id == solicitud_id
                )
            )
            if modelo is None:
                return None
            return _seleccion_a_dominio(modelo)

    def buscar_seleccion_provisional(
        self, cliente_id: UUID, clave: UUID
    ) -> SeleccionProvisional | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(SeleccionProvisionalModel).where(
                    SeleccionProvisionalModel.cliente_id == cliente_id,
                    SeleccionProvisionalModel.idempotency_key == clave,
                )
            )
            return _provisional_a_dominio(modelo) if modelo else None

    def consultar_estado_seleccion(
        self, solicitud_id: str, cliente_id: UUID
    ) -> tuple[SeleccionProvisional | None, SeleccionOferta | None]:
        with self._sesiones() as sesion:
            fila = sesion.execute(
                select(
                    CotizacionModel.cotizacion_id, SeleccionProvisionalModel, SeleccionOfertaModel
                )
                .outerjoin(
                    SeleccionProvisionalModel,
                    (SeleccionProvisionalModel.cotizacion_id == CotizacionModel.cotizacion_id)
                    & SeleccionProvisionalModel.activa,
                )
                .outerjoin(
                    SeleccionOfertaModel,
                    SeleccionOfertaModel.cotizacion_id == CotizacionModel.cotizacion_id,
                )
                .where(
                    CotizacionModel.solicitud_id == solicitud_id,
                    CotizacionModel.cliente_id == cliente_id,
                )
            ).one_or_none()
            if fila is None:
                raise SolicitudNoEncontradaError()
            return (
                _provisional_a_dominio(fila[1]) if fila[1] else None,
                _seleccion_a_dominio(fila[2]) if fila[2] else None,
            )

    def guardar_seleccion_provisional(
        self, provisional: SeleccionProvisional, cliente_id: UUID, revision_esperada: int
    ) -> SeleccionProvisional:
        seleccion = provisional.seleccion
        try:
            with self._sesiones.begin() as sesion:
                vigente_hasta = _bloquear_seleccion(sesion, seleccion, cliente_id)
                repetida = sesion.scalar(
                    select(SeleccionProvisionalModel).where(
                        SeleccionProvisionalModel.cliente_id == cliente_id,
                        SeleccionProvisionalModel.idempotency_key == seleccion.idempotency_key,
                    )
                )
                if repetida is not None:
                    if repetida.request_fingerprint != seleccion.request_fingerprint:
                        raise ConflictoIdempotenciaError()
                    return _provisional_a_dominio(repetida)
                if sesion.get(SeleccionOfertaModel, seleccion.cotizacion_id) is not None:
                    raise SeleccionNoModificableError()
                actual = _seleccion_activa(sesion, seleccion.cotizacion_id)
                if (actual.revision if actual else 0) != revision_esperada:
                    raise SeleccionDesactualizadaError()
                if min(vigente_hasta, provisional.vigente_hasta) <= datetime.now(UTC):
                    raise OfertaNoDisponibleError()
                if actual is not None:
                    actual.activa = False
                    sesion.flush()
                sesion.add(
                    SeleccionProvisionalModel(
                        cotizacion_id=seleccion.cotizacion_id,
                        revision=revision_esperada + 1,
                        cliente_id=cliente_id,
                        solicitud_id=seleccion.solicitud_id,
                        activa=True,
                        oferta_id=seleccion.oferta_id,
                        version=seleccion.version,
                        nombre_oferta=seleccion.nombre_oferta,
                        prima_mensual=seleccion.prima_mensual.monto,
                        periodicidad=seleccion.periodicidad,
                        moneda=seleccion.prima_mensual.moneda,
                        seleccionada_en=seleccion.seleccionada_en,
                        vigente_hasta=provisional.vigente_hasta,
                        idempotency_key=seleccion.idempotency_key,
                        request_fingerprint=seleccion.request_fingerprint,
                    )
                )
            return provisional
        except IntegrityError as error:
            # La misma clave puede competir en solicitudes distintas del mismo cliente.
            repetida_dominio = self.buscar_seleccion_provisional(
                cliente_id, seleccion.idempotency_key
            )
            if repetida_dominio is None:
                raise
            if repetida_dominio.seleccion.request_fingerprint != seleccion.request_fingerprint:
                raise ConflictoIdempotenciaError() from error
            return repetida_dominio

    def guardar_suscripcion(self, suscripcion: Suscripcion) -> None:
        with self._sesiones.begin() as sesion:
            sesion.add(
                SuscripcionModel(
                    suscripcion_id=suscripcion.suscripcion_id,
                    solicitud_id=suscripcion.solicitud_id,
                    cotizacion_id=suscripcion.cotizacion_id,
                    cliente_id=suscripcion.cliente_id,
                    oferta_id=suscripcion.oferta_id,
                    oferta_version=suscripcion.oferta_version,
                    estado=suscripcion.estado.value,
                    regla_version=suscripcion.regla_version,
                    creada_en=suscripcion.creada_en,
                    actualizada_en=suscripcion.actualizada_en,
                    idempotency_key=suscripcion.idempotency_key,
                    request_fingerprint=suscripcion.request_fingerprint,
                )
            )
            sesion.flush()
            sesion.add_all(
                [
                    DecisionSuscripcionModel(
                        suscripcion_id=suscripcion.decision.suscripcion_id,
                        regla_version=suscripcion.decision.regla_version,
                        resultado=suscripcion.decision.resultado.value,
                        datos_evaluados=suscripcion.decision.datos_evaluados,
                        hash_decision=suscripcion.decision.hash_decision,
                        decidida_en=suscripcion.decision.decidida_en,
                    ),
                    SagaEmisionModel(
                        suscripcion_id=suscripcion.saga.suscripcion_id,
                        estado=suscripcion.saga.estado.value,
                        version=suscripcion.saga.version,
                        actualizada_en=suscripcion.saga.actualizada_en,
                        ultimo_error=suscripcion.saga.ultimo_error,
                    ),
                    OutboxEventoModel(
                        evento_id=uuid4(),
                        tipo="SuscripcionAprobada",
                        agregado_id=suscripcion.suscripcion_id,
                        payload={
                            "suscripcionId": str(suscripcion.suscripcion_id),
                            "solicitudId": suscripcion.solicitud_id,
                            "estado": suscripcion.estado.value,
                            "reglaVersion": suscripcion.regla_version,
                            "hashDecision": suscripcion.decision.hash_decision,
                        },
                        creado_en=suscripcion.creada_en,
                        intentos=0,
                    ),
                ]
            )

    def obtener_suscripcion(self, solicitud_id: str) -> Suscripcion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(SuscripcionModel).where(SuscripcionModel.solicitud_id == solicitud_id)
            )
            return _suscripcion_a_dominio(sesion, modelo) if modelo else None

    def buscar_suscripcion_por_idempotencia(
        self, cliente_id: UUID, idempotency_key: UUID
    ) -> Suscripcion | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(SuscripcionModel).where(
                    SuscripcionModel.cliente_id == cliente_id,
                    SuscripcionModel.idempotency_key == idempotency_key,
                )
            )
            return _suscripcion_a_dominio(sesion, modelo) if modelo else None


def _a_modelo(cotizacion: Cotizacion) -> CotizacionModel:
    prima_estimada = cotizacion.prima_estimada
    valida_hasta = cotizacion.valida_hasta
    if prima_estimada is None or valida_hasta is None:
        raise ValueError("No se puede persistir una cotización sin calcular.")
    return CotizacionModel(
        cotizacion_id=cotizacion.cotizacion_id,
        cliente_id=cotizacion.cliente_id,
        producto_id=cotizacion.producto_id,
        estado=cotizacion.estado.value,
        moneda=cotizacion.moneda,
        prima_estimada=prima_estimada.monto,
        valida_hasta=valida_hasta,
        coberturas=[
            CoberturaCotizadaModel(
                cobertura_id=cobertura.cobertura_id,
                suma_asegurada=cobertura.suma_asegurada.monto,
                deducible=cobertura.deducible.monto,
                prima=_monto_prima(cobertura.prima),
                orden=orden,
            )
            for orden, cobertura in enumerate(cotizacion.coberturas)
        ],
    )


def _fecha_seleccion(fecha: datetime) -> datetime:
    return fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha.astimezone(UTC)


def _bloquear_seleccion(sesion: Session, seleccion: SeleccionOferta, cliente_id: UUID) -> datetime:
    # Un UPDATE sin cambio de negocio serializa tambien SQLite, donde FOR UPDATE no bloquea.
    vigencia = sesion.scalar(
        update(CotizacionModel)
        .where(
            CotizacionModel.cotizacion_id == seleccion.cotizacion_id,
            CotizacionModel.solicitud_id == seleccion.solicitud_id,
            CotizacionModel.cliente_id == cliente_id,
        )
        .values(estado=CotizacionModel.estado)
        .returning(CotizacionModel.valida_hasta)
    )
    if vigencia is None:
        raise SolicitudNoEncontradaError()
    return _fecha_seleccion(vigencia)


def _seleccion_activa(sesion: Session, cotizacion_id: UUID) -> SeleccionProvisionalModel | None:
    return sesion.scalar(
        select(SeleccionProvisionalModel).where(
            SeleccionProvisionalModel.cotizacion_id == cotizacion_id,
            SeleccionProvisionalModel.activa,
        )
    )


def _seleccion_a_dominio(
    modelo: SeleccionOfertaModel | SeleccionProvisionalModel,
) -> SeleccionOferta:
    return SeleccionOferta(
        solicitud_id=modelo.solicitud_id,
        cotizacion_id=modelo.cotizacion_id,
        oferta_id=modelo.oferta_id,
        version=modelo.version,
        nombre_oferta=modelo.nombre_oferta,
        prima_mensual=Dinero(modelo.prima_mensual, modelo.moneda),
        seleccionada_en=_fecha_seleccion(modelo.seleccionada_en),
        idempotency_key=modelo.idempotency_key,
        request_fingerprint=modelo.request_fingerprint,
        periodicidad=modelo.periodicidad,
    )


def _provisional_a_dominio(modelo: SeleccionProvisionalModel) -> SeleccionProvisional:
    return SeleccionProvisional(
        _seleccion_a_dominio(modelo), modelo.revision, _fecha_seleccion(modelo.vigente_hasta)
    )


def _suscripcion_a_dominio(sesion: Session, modelo: SuscripcionModel) -> Suscripcion:
    decision = sesion.get(DecisionSuscripcionModel, modelo.suscripcion_id)
    saga = sesion.get(SagaEmisionModel, modelo.suscripcion_id)
    if decision is None or saga is None:
        raise ValueError("La suscripción persistida no tiene decisión o saga.")
    return Suscripcion(
        suscripcion_id=modelo.suscripcion_id,
        solicitud_id=modelo.solicitud_id,
        cotizacion_id=modelo.cotizacion_id,
        cliente_id=modelo.cliente_id,
        oferta_id=modelo.oferta_id,
        oferta_version=modelo.oferta_version,
        estado=EstadoSuscripcion(modelo.estado),
        regla_version=modelo.regla_version,
        creada_en=_con_zona(modelo.creada_en),
        actualizada_en=_con_zona(modelo.actualizada_en),
        idempotency_key=modelo.idempotency_key,
        request_fingerprint=modelo.request_fingerprint,
        decision=DecisionSuscripcion(
            suscripcion_id=decision.suscripcion_id,
            regla_version=decision.regla_version,
            resultado=EstadoSuscripcion(decision.resultado),
            datos_evaluados=decision.datos_evaluados,
            hash_decision=decision.hash_decision,
            decidida_en=_con_zona(decision.decidida_en),
        ),
        saga=SagaEmision(
            suscripcion_id=saga.suscripcion_id,
            estado=EstadoSagaEmision(saga.estado),
            version=saga.version,
            actualizada_en=_con_zona(saga.actualizada_en),
            ultimo_error=saga.ultimo_error,
        ),
    )


def _con_zona(fecha: datetime) -> datetime:
    return fecha if fecha.tzinfo is not None else fecha.replace(tzinfo=UTC)


def _monto_prima(prima: Dinero | None) -> Decimal:
    if prima is None:
        raise ValueError("No se puede persistir una cobertura sin calcular.")
    return prima.monto


def _filas_de_decision(
    cotizacion: Cotizacion,
) -> list[InstantaneaDecisionModel | OutboxEventoModel]:
    instantanea = cotizacion.instantanea
    assert instantanea is not None
    return [
        InstantaneaDecisionModel(
            cotizacion_id=instantanea.cotizacion_id,
            regla_id=instantanea.regla_id,
            regla_version=instantanea.regla_version,
            perfil_id=instantanea.perfil_id,
            perfil_modelo_version=instantanea.perfil_modelo_version,
            hash_decision=instantanea.hash_decision,
            hash_contenido=instantanea.hash_contenido,
            contenido=instantanea.contenido,
            creada_en=instantanea.creada_en,
        ),
        OutboxEventoModel(
            evento_id=uuid4(),
            tipo="InstantaneaDecisionRegistrada",
            agregado_id=instantanea.cotizacion_id,
            payload={
                "decisionId": str(instantanea.cotizacion_id),
                "reglaVersion": instantanea.regla_version,
                "hashDecision": instantanea.hash_decision,
                "hashContenido": instantanea.hash_contenido,
            },
            creado_en=instantanea.creada_en,
            intentos=0,
        ),
    ]


def _a_dominio(modelo: CotizacionModel) -> Cotizacion:
    moneda = modelo.moneda
    valida_hasta = modelo.valida_hasta
    if valida_hasta.tzinfo is None:
        # Motores sin soporte de zona horaria (ej. SQLite) devuelven fechas naive en UTC.
        valida_hasta = valida_hasta.replace(tzinfo=timezone.utc)
    instantanea = _instantanea_a_dominio(modelo.instantanea)
    detalle = instantanea.contenido.get("tarificacion", {}) if instantanea else {}
    return Cotizacion(
        cotizacion_id=modelo.cotizacion_id,
        cliente_id=modelo.cliente_id,
        producto_id=modelo.producto_id,
        estado=EstadoCotizacion(modelo.estado),
        prima_estimada=Dinero(modelo.prima_estimada, moneda),
        valida_hasta=valida_hasta,
        instantanea=instantanea,
        coberturas=[
            CoberturaCotizada(
                cobertura_id=cobertura.cobertura_id,
                suma_asegurada=Dinero(cobertura.suma_asegurada, moneda),
                deducible=Dinero(cobertura.deducible, moneda),
                prima=Dinero(cobertura.prima, moneda),
                deducible_descuenta_base=detalle.get("deducibleDescuentaBase", True),
            )
            for cobertura in modelo.coberturas
        ],
    )


def _instantanea_a_dominio(modelo: InstantaneaDecisionModel | None) -> InstantaneaDecision | None:
    if modelo is None:
        return None
    return InstantaneaDecision(
        cotizacion_id=modelo.cotizacion_id,
        regla_id=modelo.regla_id,
        regla_version=modelo.regla_version,
        perfil_id=modelo.perfil_id,
        perfil_modelo_version=modelo.perfil_modelo_version,
        hash_decision=modelo.hash_decision,
        hash_contenido=modelo.hash_contenido,
        contenido=modelo.contenido,
        creada_en=(
            modelo.creada_en if modelo.creada_en.tzinfo else modelo.creada_en.replace(tzinfo=UTC)
        ),
    )


def _fecha_dashboard(fecha: datetime) -> str:
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return fecha.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _producto_id_dashboard(producto_id: UUID) -> str:
    for producto in PRODUCTOS:
        if producto.producto_interno_id == producto_id:
            return producto.producto_id
    return str(producto_id)


def _solicitud_a_dominio(modelo: CotizacionModel) -> SolicitudCotizacion:
    solicitud_id = modelo.solicitud_id
    tipo_cliente = modelo.tipo_cliente
    tipo_documento = modelo.tipo_documento
    identificacion = modelo.identificacion
    datos_producto = modelo.datos_producto
    correo = modelo.correo
    telefono = modelo.telefono
    idempotency_key = modelo.idempotency_key
    request_fingerprint = modelo.request_fingerprint
    if (
        solicitud_id is None
        or tipo_cliente is None
        or tipo_documento is None
        or identificacion is None
        or datos_producto is None
        or correo is None
        or telefono is None
        or idempotency_key is None
        or request_fingerprint is None
    ):
        raise ValueError("La cotización no contiene una solicitud completa.")
    creada_en = modelo.creada_en
    if creada_en.tzinfo is None:
        creada_en = creada_en.replace(tzinfo=UTC)
    return SolicitudCotizacion(
        solicitud_id=solicitud_id,
        cotizacion=_a_dominio(modelo),
        tipo_cliente=tipo_cliente,
        tipo_documento=tipo_documento,
        identificacion=identificacion,
        datos_producto=datos_producto,
        correo=correo,
        telefono=telefono,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        creada_en=creada_en,
    )
