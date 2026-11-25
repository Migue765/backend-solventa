from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    RechazoNoEncontradoError,
    SolicitudNoEncontradaError,
    SolicitudNoRechazableError,
)
from cotizacion.domain.historial_solicitudes import RechazoSolicitud, ResumenSolicitud
from cotizacion.domain.ofertas import EstadoOferta, EstadoSeleccionProvisional
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    InstantaneaDecisionModel,
    IntentoPagoSimuladoModel,
    OperacionSocioModel,
    PagoSimuladoModel,
    RechazoSolicitudModel,
    SagaEmisionModel,
    SeleccionOfertaModel,
    SeleccionProvisionalModel,
    SuscripcionModel,
)
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    _con_zona,
    _producto_id_dashboard,
    _provisional_a_dominio,
    _seleccion_a_dominio,
)


class RepositorioHistorialSolicitudesSql:
    def __init__(
        self,
        sesiones: sessionmaker[Session],
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sesiones = sesiones
        self._reloj = reloj

    def listar(
        self,
        *,
        socio_id: str | None = None,
        producto_id: str | None = None,
        cliente_id: UUID | None = None,
        solicitud_id: str | None = None,
    ) -> tuple[ResumenSolicitud, ...]:
        if (socio_id is None) == (cliente_id is None) or (
            socio_id is not None and producto_id is None
        ):
            raise ValueError("Historial requiere un cliente o socio/producto autorizado.")
        intento_actual = (
            select(IntentoPagoSimuladoModel.intento_id)
            .where(
                IntentoPagoSimuladoModel.solicitud_id == CotizacionModel.solicitud_id,
                IntentoPagoSimuladoModel.cliente_id == CotizacionModel.cliente_id,
            )
            .order_by(
                (IntentoPagoSimuladoModel.resultado == "pendiente").desc(),
                IntentoPagoSimuladoModel.creada_en.desc(),
                IntentoPagoSimuladoModel.intento_id.desc(),
            )
            .limit(1)
            .correlate(CotizacionModel)
            .scalar_subquery()
        )
        consulta = (
            select(
                CotizacionModel,
                OperacionSocioModel,
                SeleccionProvisionalModel,
                SeleccionOfertaModel,
                SuscripcionModel,
                SagaEmisionModel,
                PagoSimuladoModel,
                IntentoPagoSimuladoModel,
                RechazoSolicitudModel,
                InstantaneaDecisionModel,
            )
            .outerjoin(
                OperacionSocioModel,
                (OperacionSocioModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (OperacionSocioModel.cliente_id == CotizacionModel.cliente_id)
                & (OperacionSocioModel.solicitud_id == CotizacionModel.solicitud_id),
            )
            .outerjoin(
                SeleccionProvisionalModel,
                (SeleccionProvisionalModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (SeleccionProvisionalModel.cliente_id == CotizacionModel.cliente_id)
                & (SeleccionProvisionalModel.solicitud_id == CotizacionModel.solicitud_id)
                & SeleccionProvisionalModel.activa.is_(True),
            )
            .outerjoin(
                SeleccionOfertaModel,
                (SeleccionOfertaModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (SeleccionOfertaModel.solicitud_id == CotizacionModel.solicitud_id),
            )
            .outerjoin(
                SuscripcionModel,
                (SuscripcionModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (SuscripcionModel.cliente_id == CotizacionModel.cliente_id)
                & (SuscripcionModel.solicitud_id == CotizacionModel.solicitud_id),
            )
            .outerjoin(
                SagaEmisionModel, SagaEmisionModel.suscripcion_id == SuscripcionModel.suscripcion_id
            )
            .outerjoin(
                PagoSimuladoModel,
                (PagoSimuladoModel.suscripcion_id == SuscripcionModel.suscripcion_id)
                & (PagoSimuladoModel.cliente_id == CotizacionModel.cliente_id)
                & (PagoSimuladoModel.solicitud_id == CotizacionModel.solicitud_id),
            )
            .outerjoin(
                IntentoPagoSimuladoModel, IntentoPagoSimuladoModel.intento_id == intento_actual
            )
            .outerjoin(
                RechazoSolicitudModel,
                (RechazoSolicitudModel.cotizacion_id == CotizacionModel.cotizacion_id)
                & (RechazoSolicitudModel.cliente_id == CotizacionModel.cliente_id)
                & (RechazoSolicitudModel.solicitud_id == CotizacionModel.solicitud_id),
            )
            .outerjoin(
                InstantaneaDecisionModel,
                InstantaneaDecisionModel.cotizacion_id == CotizacionModel.cotizacion_id,
            )
            .where(CotizacionModel.solicitud_id.is_not(None))
            .order_by(CotizacionModel.creada_en.desc(), CotizacionModel.cotizacion_id)
        )
        if socio_id is not None:
            consulta = consulta.where(
                OperacionSocioModel.socio_id == socio_id,
                OperacionSocioModel.producto_id == producto_id,
            )
        else:
            consulta = consulta.where(CotizacionModel.cliente_id == cliente_id)
        if solicitud_id is not None:
            consulta = consulta.where(CotizacionModel.solicitud_id == solicitud_id)
        with self._sesiones() as sesion:
            ahora = self._reloj()
            return tuple(
                _resumen(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    ahora=ahora,
                )
                for row in sesion.execute(consulta)
            )

    def consultar_rechazo(self, solicitud_id: str, cliente_id: UUID) -> RechazoSolicitud:
        with self._sesiones() as sesion:
            cotizacion = sesion.scalar(
                select(CotizacionModel).where(
                    CotizacionModel.solicitud_id == solicitud_id,
                    CotizacionModel.cliente_id == cliente_id,
                )
            )
            if cotizacion is None:
                raise SolicitudNoEncontradaError()
            rechazo = sesion.get(RechazoSolicitudModel, cotizacion.cotizacion_id)
            if rechazo is None:
                raise RechazoNoEncontradoError()
            return _rechazo(rechazo)

    def rechazar(self, solicitud_id: str, cliente_id: UUID, key: UUID) -> RechazoSolicitud:
        try:
            with self._sesiones.begin() as sesion:
                cotizacion = sesion.scalar(
                    update(CotizacionModel)
                    .where(
                        CotizacionModel.solicitud_id == solicitud_id,
                        CotizacionModel.cliente_id == cliente_id,
                    )
                    .values(estado=CotizacionModel.estado)
                    .returning(CotizacionModel)
                )
                if cotizacion is None:
                    raise SolicitudNoEncontradaError()
                por_clave = sesion.scalar(
                    select(RechazoSolicitudModel).where(
                        RechazoSolicitudModel.cliente_id == cliente_id,
                        RechazoSolicitudModel.idempotency_key == key,
                    )
                )
                if por_clave is not None:
                    if por_clave.cotizacion_id != cotizacion.cotizacion_id:
                        raise ConflictoIdempotenciaError()
                    return _rechazo(por_clave)
                if (
                    sesion.get(RechazoSolicitudModel, cotizacion.cotizacion_id) is not None
                    or sesion.get(SeleccionOfertaModel, cotizacion.cotizacion_id) is not None
                    or sesion.scalar(
                        select(SuscripcionModel.suscripcion_id).where(
                            SuscripcionModel.cotizacion_id == cotizacion.cotizacion_id
                        )
                    )
                    is not None
                    or sesion.scalar(
                        select(PagoSimuladoModel.pago_id).where(
                            PagoSimuladoModel.solicitud_id == solicitud_id,
                            PagoSimuladoModel.cliente_id == cliente_id,
                        )
                    )
                    is not None
                ):
                    raise SolicitudNoRechazableError()
                modelo = RechazoSolicitudModel(
                    cotizacion_id=cotizacion.cotizacion_id,
                    solicitud_id=solicitud_id,
                    cliente_id=cliente_id,
                    rechazada_en=self._reloj(),
                    idempotency_key=key,
                )
                sesion.add(modelo)
                return _rechazo(modelo)
        except IntegrityError as error:
            with self._sesiones() as sesion:
                if (
                    sesion.scalar(
                        select(RechazoSolicitudModel.cotizacion_id).where(
                            RechazoSolicitudModel.cliente_id == cliente_id,
                            RechazoSolicitudModel.idempotency_key == key,
                        )
                    )
                    is not None
                ):
                    raise ConflictoIdempotenciaError() from error
            raise


def _resumen(
    cotizacion: CotizacionModel,
    operacion: OperacionSocioModel | None,
    provisional: SeleccionProvisionalModel | None,
    aceptacion: SeleccionOfertaModel | None,
    suscripcion: SuscripcionModel | None,
    saga: SagaEmisionModel | None,
    pago: PagoSimuladoModel | None,
    intento: IntentoPagoSimuladoModel | None,
    rechazo: RechazoSolicitudModel | None,
    instantanea: InstantaneaDecisionModel | None,
    *,
    ahora: datetime,
) -> ResumenSolicitud:
    if cotizacion.solicitud_id is None:
        raise ValueError("Cotizacion sin solicitud.")
    evidencia: dict[str, Any] = {}
    if instantanea is not None:
        perfil = instantanea.contenido.get("perfil")
        if not isinstance(perfil, dict):
            raise ValueError("Perfil de decision persistida invalido.")
        datos = perfil.get("evidencia")
        if datos is not None and not isinstance(datos, dict):
            raise ValueError("Evidencia de perfil persistida invalida.")
        if datos is not None:
            evidencia = datos
    vigencias = [_con_zona(cotizacion.valida_hasta)]
    if "vigenteHasta" in evidencia:
        vigencias.append(_fecha(evidencia["vigenteHasta"]))
    fuentes = evidencia.get("fuentes", [])
    if not isinstance(fuentes, list) or any(not isinstance(fuente, dict) for fuente in fuentes):
        raise ValueError("Fuentes de decision persistida invalidas.")
    for fuente in fuentes:
        vigencias.append(_fecha(fuente["vigenteHasta"]))
    consentimiento_id = None
    consentimiento_version = None
    if "consentimientoId" in evidencia or "consentimientoVersion" in evidencia:
        consentimiento_id = UUID(evidencia["consentimientoId"])
        consentimiento_version = evidencia["consentimientoVersion"]
        if type(consentimiento_version) is not int or consentimiento_version < 1:
            raise ValueError("Version de consentimiento persistida invalida.")
    if provisional is not None:
        vigencias.append(_con_zona(provisional.vigente_hasta))
    valida_hasta = min(vigencias)
    completada_en = None
    if suscripcion is not None and saga is None:
        raise ValueError("Suscripcion persistida sin saga.")
    if pago is not None:
        if (
            aceptacion is None
            or suscripcion is None
            or saga is None
            or suscripcion.estado != "aprobada"
        ):
            raise ValueError("Pago confirmado sin aceptacion y suscripcion coherentes.")
        if saga.estado == "emitida" or (
            not pago.emision_solicitada and saga.estado == "pago_confirmado"
        ):
            completada_en = max(_con_zona(pago.confirmada_en), _con_zona(saga.actualizada_en))
    if rechazo is not None and (
        aceptacion is not None or suscripcion is not None or pago is not None
    ):
        raise ValueError("Rechazo cliente incompatible con contratacion persistida.")
    estado: Literal["pendiente", "completada", "rechazada", "vencida"] = (
        "completada"
        if completada_en is not None
        else "rechazada"
        if rechazo is not None
        else "vencida"
        if pago is None and valida_hasta <= ahora
        else "pendiente"
    )
    estado_oferta = None
    if provisional is not None:
        estado_oferta = (
            EstadoOferta.ACEPTADA
            if aceptacion is not None
            else EstadoOferta.RETIRADA
            if rechazo is not None
            else EstadoOferta.VENCIDA
            if valida_hasta <= ahora
            else EstadoOferta.DISPONIBLE
        )
    fechas = [_con_zona(cotizacion.creada_en)]
    if estado == "vencida":
        fechas.append(valida_hasta)
    for modelo, campo in (
        (provisional, "seleccionada_en"),
        (aceptacion, "seleccionada_en"),
        (suscripcion, "actualizada_en"),
        (saga, "actualizada_en"),
        (pago, "confirmada_en"),
        (rechazo, "rechazada_en"),
    ):
        if modelo is not None:
            fechas.append(_con_zona(getattr(modelo, campo)))
    pago_estado: Literal["pendiente", "rechazado", "confirmado"] | None = None
    if pago is not None:
        pago_estado = "confirmado"
    elif intento is not None:
        if intento.resultado not in {"pendiente", "rechazado"}:
            raise ValueError("Intento terminal sin pago confirmado persistido.")
        pago_estado = "pendiente" if intento.resultado == "pendiente" else "rechazado"
        fechas.append(_con_zona(intento.evaluado_en or intento.creada_en))
    return ResumenSolicitud(
        operation_id=operacion.operation_id if operacion else None,
        socio_id=operacion.socio_id if operacion else None,
        cliente_id=cotizacion.cliente_id,
        solicitud_id=cotizacion.solicitud_id,
        cotizacion_id=cotizacion.cotizacion_id,
        producto_id=_producto_id_dashboard(cotizacion.producto_id),
        estado=estado,
        creada_en=_con_zona(cotizacion.creada_en),
        actualizada_en=max(fechas),
        valida_hasta=valida_hasta,
        completada_en=completada_en,
        rechazada_en=_con_zona(rechazo.rechazada_en) if rechazo else None,
        estado_seleccion=EstadoSeleccionProvisional(
            cotizacion.solicitud_id,
            _provisional_a_dominio(provisional) if provisional else None,
            _seleccion_a_dominio(aceptacion) if aceptacion else None,
            estado_oferta,
        ),
        pago_estado=pago_estado,
        emision_estado=saga.estado if saga else None,
        consentimiento_id=consentimiento_id,
        consentimiento_version=consentimiento_version,
    )


def _fecha(valor: object) -> datetime:
    if not isinstance(valor, str):
        raise ValueError("Fecha persistida invalida.")
    fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if fecha.tzinfo is None:
        raise ValueError("Fecha de evidencia sin zona.")
    return fecha.astimezone(UTC)


def _rechazo(modelo: RechazoSolicitudModel) -> RechazoSolicitud:
    return RechazoSolicitud(
        modelo.solicitud_id,
        modelo.cotizacion_id,
        modelo.cliente_id,
        _con_zona(modelo.rechazada_en),
        modelo.idempotency_key,
    )
