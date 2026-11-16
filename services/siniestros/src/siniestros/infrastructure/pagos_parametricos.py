import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError
from siniestros.domain import Problem
from siniestros.domain.pagos_parametricos import (
    CasoPagoParametrico,
    ProveedorPagoParametrico,
    RespuestaPagoAmbigua,
    ResultadoProveedorPago,
    huella_pago_parametrico,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import (
    AutomaticPaymentCase,
    Claim,
    ParametricDecision,
    ParametricEvent,
    ParametricPayment,
    ParametricPaymentAttempt,
    ParametricPaymentEffect,
    ParametricRule,
)
from siniestros.repository import Repository
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
NAMESPACE_PAGOS_PARAMETRICOS = UUID("a4084657-28a6-49fd-a833-6948ac98c09d")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _instant(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat().replace("+00:00", "Z")


class SimuladorPagoParametrico:
    def __init__(
        self,
        repository: Repository,
        resultado: ResultadoProveedorPago = ResultadoProveedorPago.CONFIRMADO,
    ) -> None:
        self._repository = repository
        self._resultado = resultado

    def consultar(self, pago_id: UUID, huella: str) -> ResultadoProveedorPago | None:
        with self._repository.sessions() as session:
            pago = session.get(ParametricPayment, str(pago_id))
            if pago is None or pago.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            efecto = session.get(ParametricPaymentEffect, str(pago_id))
            if efecto is None:
                return None
            if efecto.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            return ResultadoProveedorPago(efecto.resultado)

    def ejecutar(
        self,
        pago_id: UUID,
        caso: CasoPagoParametrico,
        huella: str,
    ) -> ResultadoProveedorPago:
        if huella != huella_pago_parametrico(caso):
            raise Problem(409, "idempotencia_conflictiva")
        with self._repository.sessions.begin() as session:
            pago = session.get(ParametricPayment, str(pago_id))
            if pago is None or pago.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            dialecto = session.get_bind().dialect.name
            if dialecto not in {"sqlite", "postgresql"}:
                raise ValueError("El simulador requiere SQLite o PostgreSQL.")
            insertar = sqlite_insert if dialecto == "sqlite" else pg_insert
            session.execute(
                insertar(ParametricPaymentEffect)
                .values(
                    pago_id=str(pago_id),
                    huella=huella,
                    resultado=self._resultado.value,
                    evaluado_en=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["pago_id"])
            )
            efecto = session.get(ParametricPaymentEffect, str(pago_id))
            if efecto is None or efecto.huella != huella:
                raise Problem(409, "idempotencia_conflictiva")
            return ResultadoProveedorPago(efecto.resultado)


class ProcesadorPagosParametricos:
    def __init__(
        self,
        repository: Repository,
        proveedor: ProveedorPagoParametrico | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._proveedor = proveedor or SimuladorPagoParametrico(repository)
        self._reloj = reloj

    def procesar(self, limite: int) -> dict[str, int]:
        incorporados = self._incorporar(limite)
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
        return {
            "casosIncorporados": incorporados,
            "confirmados": confirmados,
            "rechazados": rechazados,
            "pendientes": pendientes,
        }

    def detalle(self, caso_id: UUID) -> dict[str, object]:
        with self._repository.sessions() as session:
            pago = session.scalar(
                select(ParametricPayment).where(ParametricPayment.caso_id == str(caso_id))
            )
            if pago is None:
                raise Problem(404, "pago_parametrico_no_encontrado")
            return self._detalle(session, pago)

    def detalle_por_siniestro(self, siniestro_id: UUID) -> dict[str, object]:
        with self._repository.sessions() as session:
            pago = session.scalar(
                select(ParametricPayment).where(ParametricPayment.siniestro_id == str(siniestro_id))
            )
            if pago is None:
                raise Problem(404, "pago_parametrico_no_encontrado")
            return self._detalle(session, pago)

    @staticmethod
    def _detalle(session: Session, pago: ParametricPayment) -> dict[str, object]:
        caso = session.get(AutomaticPaymentCase, pago.caso_id)
        evento = session.get(ParametricEvent, pago.evento_id)
        if caso is None or evento is None:
            raise Problem(503, "trazabilidad_incompleta")
        intentos = session.scalars(
            select(ParametricPaymentAttempt)
            .where(ParametricPaymentAttempt.pago_id == pago.id)
            .order_by(ParametricPaymentAttempt.numero)
        ).all()
        if [intento.numero for intento in intentos] != list(range(1, pago.intentos + 1)):
            raise Problem(503, "trazabilidad_incompleta")
        return {
            "pagoId": pago.id,
            "casoId": pago.caso_id,
            "siniestroId": pago.siniestro_id,
            "evento": {
                "fuente": evento.fuente,
                "eventoId": evento.evento_id,
                "ocurridoEn": _instant(evento.ocurrido_en),
                "recibidoEn": _instant(evento.recibido_en),
            },
            "regla": {
                "reglaId": pago.regla_id,
                "version": pago.regla_version,
            },
            "beneficiarioId": pago.beneficiario_id,
            "indemnizacion": {
                "monto": f"{Decimal(pago.monto):.2f}",
                "moneda": pago.moneda,
            },
            "estado": pago.estado,
            "creadoEn": _instant(pago.creado_en),
            "finalizadoEn": _instant(pago.finalizado_en),
            "ultimoError": pago.ultimo_error,
            "intentos": [
                {
                    "numero": intento.numero,
                    "estado": intento.estado,
                    "iniciadoEn": _instant(intento.iniciado_en),
                    "finalizadoEn": _instant(intento.finalizado_en),
                    "codigo": intento.codigo,
                }
                for intento in intentos
            ],
        }

    def _incorporar(self, limite: int) -> int:
        ahora = self._reloj()
        incorporados = 0
        with self._repository.write() as session:
            consulta = (
                select(AutomaticPaymentCase)
                .where(
                    AutomaticPaymentCase.estado == "pendiente",
                    AutomaticPaymentCase.rechazado_en.is_(None),
                )
                .order_by(AutomaticPaymentCase.creado_en, AutomaticPaymentCase.id)
                .limit(limite)
            )
            if self._repository.engine.dialect.name == "postgresql":
                consulta = consulta.with_for_update(skip_locked=True)
            for caso_modelo in session.scalars(consulta):
                try:
                    caso = CasoPagoParametrico.model_validate(caso_modelo.payload)
                except ValidationError:
                    self._rechazar_caso(caso_modelo, ahora, "datos_financieros_invalidos")
                    continue
                if str(caso.casoId) != caso_modelo.id:
                    self._rechazar_caso(caso_modelo, ahora, "identidad_caso_conflictiva")
                    continue
                decision = session.scalar(
                    select(ParametricDecision).where(
                        ParametricDecision.evento_id == caso_modelo.evento_id,
                        ParametricDecision.regla_id == str(caso.reglaId),
                        ParametricDecision.regla_version == caso.reglaVersion,
                        ParametricDecision.decision == "valido",
                        ParametricDecision.siniestro_id == str(caso.siniestroId),
                    )
                )
                siniestro = session.get(Claim, str(caso.siniestroId))
                if (
                    decision is None
                    or siniestro is None
                    or siniestro.tipo != "parametrico"
                    or siniestro.estado not in {"aprobado", "pagado"}
                    or siniestro.owner != str(caso.clienteId)
                ):
                    self._rechazar_caso(caso_modelo, ahora, "origen_no_validado")
                    continue
                codigo = self._validar_contra_regla(session, caso_modelo, caso, siniestro)
                if codigo is not None:
                    self._rechazar_caso(caso_modelo, ahora, codigo)
                    continue
                huella = huella_pago_parametrico(caso)
                existente = session.scalar(
                    select(ParametricPayment).where(ParametricPayment.caso_id == caso_modelo.id)
                )
                if existente is not None:
                    if existente.huella != huella:
                        raise Problem(409, "idempotencia_conflictiva")
                else:
                    session.add(
                        ParametricPayment(
                            id=str(
                                uuid5(
                                    NAMESPACE_PAGOS_PARAMETRICOS,
                                    f"pago:{caso_modelo.id}",
                                )
                            ),
                            caso_id=caso_modelo.id,
                            evento_id=caso_modelo.evento_id,
                            siniestro_id=str(caso.siniestroId),
                            regla_id=str(caso.reglaId),
                            regla_version=caso.reglaVersion,
                            beneficiario_id=str(caso.beneficiarioId),
                            monto=caso.indemnizacion.monto,
                            moneda=caso.indemnizacion.moneda,
                            huella=huella,
                            estado="pendiente",
                            creado_en=ahora,
                            actualizado_en=ahora,
                            proximo_intento_en=ahora,
                            intentos=0,
                        )
                    )
                    incorporados += 1
                caso_modelo.estado = "entregado"
                caso_modelo.entregado_en = ahora
        return incorporados

    @staticmethod
    def _validar_contra_regla(
        session: Session,
        caso_modelo: AutomaticPaymentCase,
        caso: CasoPagoParametrico,
        siniestro: Claim,
    ) -> str | None:
        if (
            caso_modelo.regla_id != str(caso.reglaId)
            or caso_modelo.regla_version != caso.reglaVersion
            or caso_modelo.siniestro_id != str(caso.siniestroId)
        ):
            return "identidad_caso_conflictiva"
        evento = session.get(ParametricEvent, caso_modelo.evento_id)
        if evento is None or evento.fuente != caso.fuente or evento.evento_id != str(caso.eventoId):
            return "evento_no_asociado"
        regla = session.scalar(
            select(ParametricRule).where(
                ParametricRule.regla_id == str(caso.reglaId),
                ParametricRule.version == caso.reglaVersion,
            )
        )
        if regla is None or regla.fuente != caso.fuente:
            return "regla_no_encontrada"
        if regla.cliente_id != str(caso.clienteId):
            return "origen_no_validado"
        if regla.poliza_id != str(caso.polizaId) or siniestro.payload.get("polizaId") != str(
            caso.polizaId
        ):
            return "poliza_invalida"
        if regla.beneficiario_id is None or regla.beneficiario_id != str(caso.beneficiarioId):
            return "beneficiario_invalido"
        if (
            regla.monto_indemnizacion is None
            or regla.moneda is None
            or Decimal(regla.monto_indemnizacion) != caso.indemnizacion.monto
            or regla.moneda != caso.indemnizacion.moneda
        ):
            return "monto_invalido"
        return None

    @staticmethod
    def _rechazar_caso(
        caso: AutomaticPaymentCase,
        ahora: datetime,
        codigo: str,
    ) -> None:
        caso.rechazado_en = ahora
        caso.ultimo_error = codigo
        logger.error(
            "pago_parametrico.caso_rechazado caso_id=%s codigo=%s",
            caso.id,
            codigo,
        )

    def _reservar(
        self,
        lease_segundos: int = 30,
    ) -> tuple[UUID, CasoPagoParametrico, str, UUID, int] | None:
        ahora = self._reloj()
        with self._repository.write() as session:
            disponible = (
                ParametricPayment.estado == "pendiente",
                ParametricPayment.proximo_intento_en <= ahora,
                or_(
                    ParametricPayment.lease_token.is_(None),
                    ParametricPayment.lease_hasta <= ahora,
                ),
            )
            consulta = (
                select(ParametricPayment)
                .where(*disponible)
                .order_by(
                    ParametricPayment.proximo_intento_en,
                    ParametricPayment.creado_en,
                    ParametricPayment.id,
                )
                .limit(1)
            )
            if self._repository.engine.dialect.name == "postgresql":
                consulta = consulta.with_for_update(skip_locked=True)
            pago = session.scalar(consulta)
            if pago is None:
                return None
            caso_modelo = session.get(AutomaticPaymentCase, pago.caso_id)
            if caso_modelo is None:
                raise Problem(503, "trazabilidad_incompleta")
            caso = CasoPagoParametrico.model_validate(caso_modelo.payload)
            huerfanos = session.scalars(
                select(ParametricPaymentAttempt).where(
                    ParametricPaymentAttempt.pago_id == pago.id,
                    ParametricPaymentAttempt.estado == "en_proceso",
                )
            ).all()
            for huerfano in huerfanos:
                huerfano.estado = "ambiguo"
                huerfano.finalizado_en = ahora
                huerfano.codigo = "lease_vencido"
                logger.warning(
                    "pago_parametrico.intento_huerfano pago_id=%s numero=%s",
                    pago.id,
                    huerfano.numero,
                )
            token = uuid4()
            numero = pago.intentos + 1
            pago.lease_token = str(token)
            pago.lease_hasta = ahora + timedelta(seconds=lease_segundos)
            pago.intentos = numero
            pago.actualizado_en = ahora
            session.add(
                ParametricPaymentAttempt(
                    id=str(uuid4()),
                    pago_id=pago.id,
                    numero=numero,
                    estado="en_proceso",
                    iniciado_en=ahora,
                )
            )
            return UUID(pago.id), caso, pago.huella, token, numero

    def _resolver(
        self,
        pago_id: UUID,
        caso: CasoPagoParametrico,
        huella: str,
        token: UUID,
        numero: int,
    ) -> str:
        try:
            resultado = self._proveedor.consultar(pago_id, huella)
            if resultado is None:
                resultado = self._proveedor.ejecutar(pago_id, caso, huella)
            if not isinstance(resultado, ResultadoProveedorPago):
                raise RespuestaPagoAmbigua("respuesta_invalida")
        except RespuestaPagoAmbigua as error:
            self._aplazar(pago_id, token, numero, error.codigo)
            return "pendiente"
        self._finalizar(pago_id, token, numero, resultado)
        return resultado.value

    def _aplazar(self, pago_id: UUID, token: UUID, numero: int, codigo: str) -> None:
        ahora = self._reloj()
        with self._repository.write() as session:
            pago = self._pago_reservado(session, pago_id, token)
            intento = self._intento(session, pago_id, numero)
            intento.estado = "ambiguo"
            intento.finalizado_en = ahora
            intento.codigo = codigo[:100]
            pago.lease_token = None
            pago.lease_hasta = None
            pago.proximo_intento_en = ahora + timedelta(seconds=min(2**numero, 300))
            pago.actualizado_en = ahora
            pago.ultimo_error = codigo[:100]

    def _finalizar(
        self,
        pago_id: UUID,
        token: UUID,
        numero: int,
        resultado: ResultadoProveedorPago,
    ) -> None:
        ahora = self._reloj()
        with self._repository.write() as session:
            pago = self._pago_reservado(session, pago_id, token)
            intento = self._intento(session, pago_id, numero)
            intento.estado = resultado.value
            intento.finalizado_en = ahora
            intento.codigo = None
            pago.estado = resultado.value
            pago.finalizado_en = ahora
            pago.actualizado_en = ahora
            pago.lease_token = None
            pago.lease_hasta = None
            pago.ultimo_error = None
            if resultado == ResultadoProveedorPago.CONFIRMADO:
                siniestro = session.get(Claim, pago.siniestro_id)
                if siniestro is None or siniestro.tipo != "parametrico":
                    raise Problem(503, "trazabilidad_incompleta")
                if siniestro.estado != "pagado":
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
    def _pago_reservado(session: Session, pago_id: UUID, token: UUID) -> ParametricPayment:
        pago = session.get(ParametricPayment, str(pago_id))
        if pago is None or pago.lease_token != str(token) or pago.estado != "pendiente":
            raise Problem(409, "reserva_pago_conflictiva")
        return pago

    @staticmethod
    def _intento(
        session: Session,
        pago_id: UUID,
        numero: int,
    ) -> ParametricPaymentAttempt:
        intento = session.scalar(
            select(ParametricPaymentAttempt).where(
                ParametricPaymentAttempt.pago_id == str(pago_id),
                ParametricPaymentAttempt.numero == numero,
            )
        )
        if intento is None or intento.estado != "en_proceso":
            raise Problem(409, "intento_pago_conflictivo")
        return intento
