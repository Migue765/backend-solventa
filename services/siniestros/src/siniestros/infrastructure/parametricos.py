from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4, uuid5

from siniestros.domain import ClaimInput, Config, Problem
from siniestros.domain.parametricos import EventoParametrico, ReglaParametrica, cumple
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import (
    AutomaticPaymentCase,
    Claim,
    ParametricDecision,
    ParametricEvent,
    ParametricQueueState,
    ParametricRule,
)
from siniestros.repository import Repository, fingerprint
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

NAMESPACE_SINIESTROS_PARAMETRICOS = UUID("3f6ad36f-f134-4df5-b506-d72f72e64b23")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _instant(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


class RepositorioEventosParametricos:
    def __init__(self, repository: Repository, config: Config) -> None:
        self.repository = repository
        self.config = config

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self.repository.sessions() as session, session.begin():
            if self.repository.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            elif self.repository.engine.dialect.name != "postgresql":
                raise ValueError("Parametric events support SQLite and PostgreSQL only.")
            yield session

    def registrar_regla(self, regla: ReglaParametrica) -> tuple[dict[str, object], int]:
        if regla.vigenteHasta is not None and regla.vigenteHasta <= regla.vigenteDesde:
            raise Problem(422, "vigencia_invalida")
        payload = regla.model_dump(mode="json")
        digest = fingerprint(payload)
        with self.write() as session:
            existente = session.scalar(
                select(ParametricRule).where(
                    ParametricRule.regla_id == str(regla.reglaId),
                    ParametricRule.version == regla.version,
                )
            )
            if existente is not None:
                if existente.fingerprint != digest:
                    raise Problem(409, "version_regla_conflictiva")
                return self._regla(existente), 200
            creada = datetime.now(UTC)
            modelo = ParametricRule(
                id=str(uuid4()),
                regla_id=str(regla.reglaId),
                version=regla.version,
                fingerprint=digest,
                fuente=regla.fuente,
                tipo_evento=regla.tipoEvento,
                metrica=regla.metrica,
                operador=regla.operador,
                umbral=regla.umbral,
                unidad=regla.unidad,
                ubicacion_codigo=regla.ubicacionCodigo,
                cliente_id=str(regla.clienteId),
                poliza_id=str(regla.polizaId),
                vigente_desde=regla.vigenteDesde,
                vigente_hasta=regla.vigenteHasta,
                creada_en=creada,
            )
            session.add(modelo)
            session.flush()
            return self._regla(modelo), 201

    def recibir(self, fuente: str, eventos: Sequence[EventoParametrico]) -> dict[str, object]:
        unicos: dict[str, tuple[EventoParametrico, dict[str, Any], str]] = {}
        repetidos_lote = 0
        for evento in eventos:
            payload = evento.model_dump(mode="json")
            digest = fingerprint(payload)
            key = str(evento.eventoId)
            previo = unicos.get(key)
            if previo is not None:
                if previo[2] != digest:
                    raise Problem(409, "evento_conflictivo")
                repetidos_lote += 1
                continue
            unicos[key] = (evento, payload, digest)

        with self.write() as session:
            estado = session.get(ParametricQueueState, 1, with_for_update=True)
            if estado is None:
                raise Problem(503, "cola_no_disponible")
            existentes = {
                row.evento_id: row.fingerprint
                for row in session.execute(
                    select(ParametricEvent.evento_id, ParametricEvent.fingerprint).where(
                        ParametricEvent.fuente == fuente,
                        ParametricEvent.evento_id.in_(unicos),
                    )
                )
            }
            nuevos: list[tuple[EventoParametrico, dict[str, Any], str]] = []
            for event_id, item in unicos.items():
                existente = existentes.get(event_id)
                if existente is not None:
                    if existente != item[2]:
                        raise Problem(409, "evento_conflictivo")
                else:
                    nuevos.append(item)

            if estado.pendientes + len(nuevos) > self.config.parametric_queue_capacity:
                raise Problem(429, "backpressure")

            recibido = datetime.now(UTC)
            if nuevos:
                session.execute(
                    insert(ParametricEvent),
                    [
                        {
                            "id": str(uuid4()),
                            "fuente": fuente,
                            "evento_id": str(evento.eventoId),
                            "fingerprint": digest,
                            "payload": payload,
                            "ocurrido_en": evento.ocurridoEn,
                            "recibido_en": recibido,
                            "estado": "pendiente",
                        }
                        for evento, payload, digest in nuevos
                    ],
                )
            estado.pendientes += len(nuevos)
            return {
                "aceptados": len(nuevos),
                "duplicados": len(existentes) + repetidos_lote,
                "pendientes": estado.pendientes,
            }

    def procesar(self, limite: int) -> dict[str, int]:
        with self.write() as session:
            query = (
                select(ParametricEvent)
                .where(ParametricEvent.estado == "pendiente")
                .order_by(ParametricEvent.recibido_en, ParametricEvent.id)
                .limit(limite)
            )
            if self.repository.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            eventos = list(session.scalars(query))
            if not eventos:
                return {"procesados": 0, "casosValidos": 0}

            fuentes = {evento.fuente for evento in eventos}
            tipos = {str(evento.payload["tipoEvento"]) for evento in eventos}
            reglas = list(
                session.scalars(
                    select(ParametricRule).where(
                        ParametricRule.fuente.in_(fuentes),
                        ParametricRule.tipo_evento.in_(tipos),
                    )
                )
            )
            casos = 0
            procesado = datetime.now(UTC)
            decisiones: list[dict[str, object]] = []
            for evento in eventos:
                entrada = EventoParametrico.model_validate(evento.payload)
                vigentes = self._reglas_vigentes(reglas, evento, entrada)
                if not vigentes:
                    decisiones.append(
                        {
                            "id": str(uuid4()),
                            "evento_id": evento.id,
                            "regla_id": None,
                            "regla_version": None,
                            "decision": "sin_regla_vigente",
                            "decidida_en": procesado,
                            "siniestro_id": None,
                        }
                    )
                for regla in vigentes:
                    valido = cumple(
                        regla.operador,
                        entrada.metrica.valor,
                        Decimal(regla.umbral),
                    )
                    siniestro_id = (
                        self._crear_caso(session, evento, entrada, regla, procesado)
                        if valido
                        else None
                    )
                    decisiones.append(
                        {
                            "id": str(uuid4()),
                            "evento_id": evento.id,
                            "regla_id": regla.regla_id,
                            "regla_version": regla.version,
                            "decision": "valido" if valido else "no_valido",
                            "decidida_en": procesado,
                            "siniestro_id": siniestro_id,
                        }
                    )
                    casos += int(valido)
            if decisiones:
                session.execute(insert(ParametricDecision), decisiones)
            session.execute(
                update(ParametricEvent)
                .where(ParametricEvent.id.in_([evento.id for evento in eventos]))
                .values(estado="procesado", procesado_en=procesado)
            )

            estado = session.get(ParametricQueueState, 1, with_for_update=True)
            if estado is None or estado.pendientes < len(eventos):
                raise Problem(503, "cola_no_disponible")
            estado.pendientes -= len(eventos)
            return {"procesados": len(eventos), "casosValidos": casos}

    def casos_pendientes(self, limite: int) -> dict[str, object]:
        with self.repository.sessions() as session:
            casos = session.scalars(
                select(AutomaticPaymentCase)
                .where(AutomaticPaymentCase.estado == "pendiente")
                .order_by(AutomaticPaymentCase.creado_en, AutomaticPaymentCase.id)
                .limit(limite)
            ).all()
            return {"casos": [caso.payload for caso in casos]}

    def confirmar_caso(self, caso_id: UUID, entregado_en: datetime) -> dict[str, object]:
        with self.write() as session:
            caso = session.get(AutomaticPaymentCase, str(caso_id), with_for_update=True)
            if caso is None:
                raise Problem(404, "caso_no_encontrado")
            instante = _utc(entregado_en)
            if caso.entregado_en is not None and _utc(caso.entregado_en) != instante:
                raise Problem(409, "confirmacion_conflictiva")
            if instante < _utc(caso.creado_en) or instante > datetime.now(UTC):
                raise Problem(422, "fecha_entrega_invalida")
            caso.estado = "entregado"
            caso.entregado_en = instante
            return {"casoId": caso.id, "estado": caso.estado, "entregadoEn": _instant(instante)}

    @staticmethod
    def _reglas_vigentes(
        reglas: Sequence[ParametricRule],
        evento: ParametricEvent,
        entrada: EventoParametrico,
    ) -> list[ParametricRule]:
        ocurrido = _utc(evento.ocurrido_en)
        candidatas = [
            regla
            for regla in reglas
            if regla.fuente == evento.fuente
            and regla.tipo_evento == entrada.tipoEvento
            and regla.metrica == entrada.metrica.nombre
            and regla.unidad == entrada.metrica.unidad
            and (
                regla.ubicacion_codigo is None or regla.ubicacion_codigo == entrada.ubicacionCodigo
            )
            and _utc(regla.vigente_desde) <= ocurrido
            and (regla.vigente_hasta is None or ocurrido < _utc(regla.vigente_hasta))
        ]
        actuales: dict[str, ParametricRule] = {}
        for regla in candidatas:
            anterior = actuales.get(regla.regla_id)
            if anterior is None or anterior.version < regla.version:
                actuales[regla.regla_id] = regla
        return list(actuales.values())

    @staticmethod
    def _crear_caso(
        session: Any,
        evento: ParametricEvent,
        entrada: EventoParametrico,
        regla: ParametricRule,
        procesado: datetime,
    ) -> str:
        semilla = f"{evento.fuente}:{evento.evento_id}:{regla.regla_id}:{regla.version}"
        siniestro_id = str(uuid5(NAMESPACE_SINIESTROS_PARAMETRICOS, f"siniestro:{semilla}"))
        caso_id = str(uuid5(NAMESPACE_SINIESTROS_PARAMETRICOS, f"caso:{semilla}"))
        payload = ClaimInput.model_validate(
            {
                "polizaId": str(UUID(regla.poliza_id)),
                "fechaEvento": _instant(entrada.ocurridoEn),
                "tipoEvento": entrada.tipoEvento,
                "descripcion": (
                    f"Evento parametrico validado por la regla {regla.regla_id} "
                    f"version {regla.version}."
                ),
                "lugar": entrada.ubicacionCodigo,
            }
        ).model_dump(mode="json")
        creado = _instant(procesado)
        claim = Claim(
            id=siniestro_id,
            owner=regla.cliente_id,
            operation=str(uuid5(NAMESPACE_SINIESTROS_PARAMETRICOS, f"operacion:{semilla}")),
            fingerprint=fingerprint(payload),
            payload=payload,
            created=creado,
            tipo="parametrico",
            estado="aprobado",
            actualizado_en=procesado,
        )
        session.add(claim)
        session.flush()
        session.add_all(
            [
                CambioEstadoModel(
                    siniestro_id=siniestro_id,
                    secuencia=1,
                    estado="reportado",
                    ocurrido_en=procesado,
                    visible_cliente=True,
                ),
                CambioEstadoModel(
                    siniestro_id=siniestro_id,
                    secuencia=2,
                    estado="aprobado",
                    ocurrido_en=procesado,
                    visible_cliente=True,
                ),
            ]
        )
        salida = {
            "casoId": caso_id,
            "siniestroId": siniestro_id,
            "clienteId": regla.cliente_id,
            "polizaId": regla.poliza_id,
            "fuente": evento.fuente,
            "eventoId": evento.evento_id,
            "reglaId": regla.regla_id,
            "reglaVersion": regla.version,
            "decididoEn": creado,
        }
        session.add(
            AutomaticPaymentCase(
                id=caso_id,
                evento_id=evento.id,
                regla_id=regla.regla_id,
                regla_version=regla.version,
                siniestro_id=siniestro_id,
                payload=salida,
                estado="pendiente",
                creado_en=procesado,
            )
        )
        return siniestro_id

    @staticmethod
    def _regla(regla: ParametricRule) -> dict[str, object]:
        return {
            "reglaId": regla.regla_id,
            "version": regla.version,
            "fuente": regla.fuente,
            "creadaEn": _instant(regla.creada_en),
        }
