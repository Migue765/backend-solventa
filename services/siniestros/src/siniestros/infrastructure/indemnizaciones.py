import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from siniestros.domain import ClaimInput, Problem
from siniestros.domain.indemnizaciones import (
    EfectoProveedor,
    OrdenIndemnizacion,
    ProveedorIndemnizacion,
    SolicitudIndemnizacion,
    huella_orden,
    huella_solicitud,
)
from siniestros.domain.pagos_parametricos import (
    DineroIndemnizacion,
    RespuestaPagoAmbigua,
    ResultadoProveedorPago,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.infrastructure.repositorio import validar
from siniestros.models import (
    Audit,
    Claim,
    Indemnization,
    IndemnizationAttempt,
    IndemnizationEffect,
)
from siniestros.repository import Repository
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
NAMESPACE_INDEMNIZACIONES = UUID("6f0c8f55-58d4-4d8e-9a43-2f1c2b7c0a28")
ACCION_SOLICITUD = "indemnizacion.solicitud"
ACCION_INTENTO = "indemnizacion.intento"
ACCION_RESULTADO = "indemnizacion.resultado"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _instant(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat().replace("+00:00", "Z")


def _registrar(
    session: Session,
    indemnizacion: Indemnization,
    accion: str,
    resultado: str,
    ahora: datetime,
    correlacion: str | None = None,
    http_status: int | None = None,
) -> None:
    session.add(
        Audit(
            id=str(uuid4()),
            actor=None,
            action=accion,
            result=resultado[:64],
            correlation=correlacion or indemnizacion.correlacion,
            created=_utc(ahora).isoformat(timespec="microseconds"),
            operation=indemnizacion.operacion,
            fingerprint=indemnizacion.huella_solicitud,
            http_status=http_status,
        )
    )


def _orden(indemnizacion: Indemnization) -> OrdenIndemnizacion:
    return OrdenIndemnizacion(
        indemnizacionId=UUID(indemnizacion.id),
        siniestroId=UUID(indemnizacion.siniestro_id),
        clienteId=UUID(indemnizacion.cliente_id),
        polizaId=UUID(indemnizacion.poliza_id),
        beneficiarioId=UUID(indemnizacion.beneficiario_id),
        indemnizacion=DineroIndemnizacion(
            monto=Decimal(indemnizacion.monto), moneda=indemnizacion.moneda
        ),
    )


class SimuladorIndemnizacion:
    """Proveedor financiero academico con efecto durable e idempotente por operacion."""

    def __init__(
        self,
        repository: Repository,
        resultado: ResultadoProveedorPago = ResultadoProveedorPago.CONFIRMADO,
    ) -> None:
        self._repository = repository
        self._resultado = resultado

    def consultar(self, indemnizacion_id: UUID, huella: str) -> EfectoProveedor | None:
        with self._repository.sessions() as session:
            efecto = session.get(IndemnizationEffect, str(indemnizacion_id))
            if efecto is None:
                return None
            if efecto.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            return EfectoProveedor(
                resultado=ResultadoProveedorPago(efecto.resultado),
                referencia=efecto.referencia,
            )

    def ejecutar(self, orden: OrdenIndemnizacion, huella: str) -> EfectoProveedor:
        if huella != huella_orden(orden):
            raise Problem(409, "idempotencia_conflictiva")
        identificador = str(orden.indemnizacionId)
        referencia = "SIM-" + uuid5(NAMESPACE_INDEMNIZACIONES, f"efecto:{identificador}").hex[:20]
        with self._repository.sessions.begin() as session:
            dialecto = session.get_bind().dialect.name
            if dialecto not in {"sqlite", "postgresql"}:
                raise ValueError("El simulador requiere SQLite o PostgreSQL.")
            insertar = sqlite_insert if dialecto == "sqlite" else pg_insert
            session.execute(
                insertar(IndemnizationEffect)
                .values(
                    indemnizacion_id=identificador,
                    huella=huella,
                    resultado=self._resultado.value,
                    referencia=referencia.upper(),
                    evaluado_en=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["indemnizacion_id"])
            )
            efecto = session.get(IndemnizationEffect, identificador)
            if efecto is None or efecto.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            return EfectoProveedor(
                resultado=ResultadoProveedorPago(efecto.resultado),
                referencia=efecto.referencia,
            )


class ServicioIndemnizaciones:
    def __init__(
        self,
        repository: Repository,
        proveedor: ProveedorIndemnizacion | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._proveedor = proveedor or SimuladorIndemnizacion(repository)
        self._reloj = reloj

    def solicitar(
        self,
        siniestro_id: UUID,
        operacion: str,
        solicitud: SolicitudIndemnizacion,
        correlacion: str,
    ) -> tuple[dict[str, object], int]:
        huella = huella_solicitud(siniestro_id, solicitud)
        ahora = self._reloj()
        with self._repository.write() as session:
            previa = session.scalar(
                select(Indemnization).where(Indemnization.operacion == operacion)
            )
            if previa is not None:
                if previa.huella_solicitud != huella:
                    raise Problem(409, "idempotencia_conflictiva")
                return self._serializar(session, previa), 200
            if session.scalar(
                select(Indemnization.id).where(Indemnization.siniestro_id == str(siniestro_id))
            ):
                raise Problem(409, "indemnizacion_existente")
            siniestro = session.get(Claim, str(siniestro_id))
            if siniestro is None:
                raise Problem(404, "siniestro_no_encontrado")
            if siniestro.tipo == "parametrico":
                raise Problem(409, "pago_parametrico_automatico")
            if siniestro.estado != "aprobado":
                raise Problem(409, "siniestro_no_aprobado")
            validar(session, siniestro)
            aviso = ClaimInput.model_validate(siniestro.payload)
            identificador = str(uuid5(NAMESPACE_INDEMNIZACIONES, f"indemnizacion:{siniestro.id}"))
            orden = OrdenIndemnizacion(
                indemnizacionId=UUID(identificador),
                siniestroId=siniestro_id,
                clienteId=UUID(siniestro.owner),
                polizaId=aviso.polizaId,
                beneficiarioId=solicitud.beneficiarioId,
                indemnizacion=solicitud.indemnizacion,
            )
            indemnizacion = Indemnization(
                id=identificador,
                siniestro_id=siniestro.id,
                operacion=operacion,
                huella_solicitud=huella,
                huella=huella_orden(orden),
                cliente_id=siniestro.owner,
                poliza_id=str(aviso.polizaId),
                beneficiario_id=str(solicitud.beneficiarioId),
                monto=solicitud.indemnizacion.monto,
                moneda=solicitud.indemnizacion.moneda,
                estado="pendiente",
                correlacion=correlacion,
                creado_en=ahora,
                actualizado_en=ahora,
                proximo_intento_en=ahora,
                intentos=0,
            )
            session.add(indemnizacion)
            _registrar(session, indemnizacion, ACCION_SOLICITUD, "aceptada", ahora, None, 202)
            session.flush()
            logger.info(
                "indemnizacion.aceptada indemnizacion_id=%s siniestro_id=%s correlationId=%s",
                identificador,
                siniestro.id,
                correlacion,
            )
            return self._serializar(session, indemnizacion), 202

    def detalle(self, siniestro_id: UUID) -> dict[str, object]:
        with self._repository.sessions() as session:
            indemnizacion = session.scalar(
                select(Indemnization).where(Indemnization.siniestro_id == str(siniestro_id))
            )
            if indemnizacion is None:
                raise Problem(404, "indemnizacion_no_encontrada")
            return self._serializar(session, indemnizacion)

    def procesar(self, limite: int) -> dict[str, int]:
        confirmados = 0
        rechazados = 0
        pendientes = 0
        for _ in range(limite):
            reserva = self._reservar()
            if reserva is None:
                break
            resultado = self._resolver(*reserva)
            confirmados += int(resultado == "confirmado")
            rechazados += int(resultado == "rechazado")
            pendientes += int(resultado == "pendiente")
        return {"confirmados": confirmados, "rechazados": rechazados, "pendientes": pendientes}

    def _reservar(
        self,
        lease_segundos: int = 30,
    ) -> tuple[OrdenIndemnizacion, str, UUID, int] | None:
        ahora = self._reloj()
        with self._repository.write() as session:
            consulta = (
                select(Indemnization)
                .where(
                    Indemnization.estado == "pendiente",
                    Indemnization.proximo_intento_en <= ahora,
                    or_(
                        Indemnization.lease_token.is_(None),
                        Indemnization.lease_hasta <= ahora,
                    ),
                )
                .order_by(
                    Indemnization.proximo_intento_en,
                    Indemnization.creado_en,
                    Indemnization.id,
                )
                .limit(1)
            )
            if self._repository.engine.dialect.name == "postgresql":
                consulta = consulta.with_for_update(skip_locked=True)
            indemnizacion = session.scalar(consulta)
            if indemnizacion is None:
                return None
            # Un intento cuyo lease vencio sin respuesta no se pierde: queda ambiguo.
            for abandonado in session.scalars(
                select(IndemnizationAttempt).where(
                    IndemnizationAttempt.indemnizacion_id == indemnizacion.id,
                    IndemnizationAttempt.estado == "en_proceso",
                )
            ):
                abandonado.estado = "ambiguo"
                abandonado.finalizado_en = ahora
                abandonado.codigo = "lease_vencido"
            token = uuid4()
            numero = indemnizacion.intentos + 1
            indemnizacion.lease_token = str(token)
            indemnizacion.lease_hasta = ahora + timedelta(seconds=lease_segundos)
            indemnizacion.intentos = numero
            indemnizacion.actualizado_en = ahora
            session.add(
                IndemnizationAttempt(
                    id=str(uuid4()),
                    indemnizacion_id=indemnizacion.id,
                    numero=numero,
                    estado="en_proceso",
                    iniciado_en=ahora,
                )
            )
            _registrar(session, indemnizacion, ACCION_INTENTO, f"iniciado:{numero}", ahora)
            return _orden(indemnizacion), indemnizacion.huella, token, numero

    def _resolver(
        self,
        orden: OrdenIndemnizacion,
        huella: str,
        token: UUID,
        numero: int,
    ) -> str:
        try:
            efecto = self._proveedor.consultar(orden.indemnizacionId, huella)
            if efecto is None:
                efecto = self._proveedor.ejecutar(orden, huella)
            if not isinstance(efecto, EfectoProveedor):
                raise RespuestaPagoAmbigua("respuesta_invalida")
        except RespuestaPagoAmbigua as error:
            self._aplazar(orden.indemnizacionId, token, numero, error.codigo)
            return "pendiente"
        except (OSError, TimeoutError):
            self._aplazar(orden.indemnizacionId, token, numero, "proveedor_no_disponible")
            return "pendiente"
        self._finalizar(orden.indemnizacionId, token, numero, efecto)
        return efecto.resultado.value

    def _aplazar(self, indemnizacion_id: UUID, token: UUID, numero: int, codigo: str) -> None:
        ahora = self._reloj()
        with self._repository.write() as session:
            indemnizacion = self._reservada(session, indemnizacion_id, token)
            intento = self._intento(session, indemnizacion_id, numero)
            intento.estado = "ambiguo"
            intento.finalizado_en = ahora
            intento.codigo = codigo[:100]
            indemnizacion.lease_token = None
            indemnizacion.lease_hasta = None
            indemnizacion.proximo_intento_en = ahora + timedelta(seconds=min(2**numero, 300))
            indemnizacion.actualizado_en = ahora
            indemnizacion.ultimo_error = codigo[:100]
            _registrar(session, indemnizacion, ACCION_INTENTO, f"ambiguo:{numero}", ahora)
        logger.warning(
            "indemnizacion.ambigua indemnizacion_id=%s intento=%s codigo=%s",
            indemnizacion_id,
            numero,
            codigo,
        )

    def _finalizar(
        self,
        indemnizacion_id: UUID,
        token: UUID,
        numero: int,
        efecto: EfectoProveedor,
    ) -> None:
        ahora = self._reloj()
        with self._repository.write() as session:
            indemnizacion = self._reservada(session, indemnizacion_id, token)
            intento = self._intento(session, indemnizacion_id, numero)
            intento.estado = efecto.resultado.value
            intento.finalizado_en = ahora
            intento.codigo = None
            intento.referencia = efecto.referencia
            indemnizacion.estado = efecto.resultado.value
            indemnizacion.referencia = efecto.referencia
            indemnizacion.finalizado_en = ahora
            indemnizacion.actualizado_en = ahora
            indemnizacion.lease_token = None
            indemnizacion.lease_hasta = None
            indemnizacion.ultimo_error = None
            _registrar(session, indemnizacion, ACCION_RESULTADO, efecto.resultado.value, ahora)
            if efecto.resultado == ResultadoProveedorPago.CONFIRMADO:
                self._marcar_pagado(session, indemnizacion, ahora)

    @staticmethod
    def _marcar_pagado(session: Session, indemnizacion: Indemnization, ahora: datetime) -> None:
        siniestro = session.get(Claim, indemnizacion.siniestro_id)
        if siniestro is None:
            raise Problem(503, "trazabilidad_incompleta")
        if siniestro.estado == "pagado":
            return
        if siniestro.estado != "aprobado":
            # El desembolso confirmado se conserva aunque el siniestro haya cambiado.
            logger.error(
                "indemnizacion.estado_siniestro_inesperado siniestro_id=%s estado=%s",
                siniestro.id,
                siniestro.estado,
            )
            return
        secuencia = (
            session.scalar(
                select(func.max(CambioEstadoModel.secuencia)).where(
                    CambioEstadoModel.siniestro_id == siniestro.id
                )
            )
            or 0
        )
        siniestro.estado = "pagado"
        siniestro.actualizado_en = ahora
        session.add(
            CambioEstadoModel(
                siniestro_id=siniestro.id,
                secuencia=secuencia + 1,
                estado="pagado",
                ocurrido_en=ahora,
                visible_cliente=True,
            )
        )

    @staticmethod
    def _reservada(session: Session, indemnizacion_id: UUID, token: UUID) -> Indemnization:
        indemnizacion = session.get(Indemnization, str(indemnizacion_id))
        if (
            indemnizacion is None
            or indemnizacion.lease_token != str(token)
            or indemnizacion.estado != "pendiente"
        ):
            raise Problem(409, "reserva_indemnizacion_conflictiva")
        return indemnizacion

    @staticmethod
    def _intento(session: Session, indemnizacion_id: UUID, numero: int) -> IndemnizationAttempt:
        intento = session.scalar(
            select(IndemnizationAttempt).where(
                IndemnizationAttempt.indemnizacion_id == str(indemnizacion_id),
                IndemnizationAttempt.numero == numero,
            )
        )
        if intento is None or intento.estado != "en_proceso":
            raise Problem(409, "intento_indemnizacion_conflictivo")
        return intento

    @staticmethod
    def _serializar(session: Session, indemnizacion: Indemnization) -> dict[str, object]:
        intentos = session.scalars(
            select(IndemnizationAttempt)
            .where(IndemnizationAttempt.indemnizacion_id == indemnizacion.id)
            .order_by(IndemnizationAttempt.numero)
        ).all()
        auditoria = session.scalars(
            select(Audit)
            .where(
                Audit.operation == indemnizacion.operacion,
                Audit.action.in_([ACCION_SOLICITUD, ACCION_INTENTO, ACCION_RESULTADO]),
            )
            .order_by(Audit.created, Audit.id)
        ).all()
        return {
            "indemnizacionId": indemnizacion.id,
            "siniestroId": indemnizacion.siniestro_id,
            "operacionId": indemnizacion.operacion,
            "clienteId": indemnizacion.cliente_id,
            "polizaId": indemnizacion.poliza_id,
            "beneficiarioId": indemnizacion.beneficiario_id,
            "indemnizacion": {
                "monto": f"{Decimal(indemnizacion.monto):.2f}",
                "moneda": indemnizacion.moneda,
            },
            "estado": indemnizacion.estado,
            "referencia": indemnizacion.referencia,
            "creadoEn": _instant(indemnizacion.creado_en),
            "finalizadoEn": _instant(indemnizacion.finalizado_en),
            "ultimoError": indemnizacion.ultimo_error,
            "intentos": [
                {
                    "numero": intento.numero,
                    "estado": intento.estado,
                    "iniciadoEn": _instant(intento.iniciado_en),
                    "finalizadoEn": _instant(intento.finalizado_en),
                    "codigo": intento.codigo,
                    "referencia": intento.referencia,
                }
                for intento in intentos
            ],
            "auditoria": [
                {
                    "accion": registro.action,
                    "resultado": registro.result,
                    "correlationId": registro.correlation,
                    "fecha": registro.created.replace("+00:00", "Z"),
                }
                for registro in auditoria
            ],
        }
