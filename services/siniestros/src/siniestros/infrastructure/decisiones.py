"""Persistencia de la HU27: decisiones, asignaciones y transiciones auditadas."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from siniestros.domain import Problem
from siniestros.domain.decision import (
    COLAS,
    ESTADOS_DECISION_AUTOMATICA,
    POLITICA,
    VERSION_POLITICA,
    DecisionRevision,
    Dictamen,
    ResultadoEvaluacion,
    TransicionNoPermitida,
    dictaminar,
    dictaminar_revision,
    ruta,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.infrastructure.repositorio import validar
from siniestros.models import Base, Claim
from siniestros.repository import Repository, fingerprint
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column


class DecisionSiniestroModel(Base):
    __tablename__ = "decisiones_siniestro"
    __table_args__ = (
        UniqueConstraint("siniestro_id", "operacion"),
        CheckConstraint("origen IN ('automatica', 'humana')"),
        CheckConstraint("actor_tipo IN ('sistema', 'revisor')"),
        CheckConstraint(
            "resultado IN ('aprobado', 'inspeccion', 'revision', 'bloqueado_fraude', 'rechazado')"
        ),
        Index("ix_decisiones_siniestro_siniestro", "siniestro_id", "decidida_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"))
    operacion: Mapped[str] = mapped_column(String(36))
    huella: Mapped[str] = mapped_column(String(64))
    origen: Mapped[str] = mapped_column(String(12))
    actor_tipo: Mapped[str] = mapped_column(String(12))
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resultado: Mapped[str] = mapped_column(String(20))
    estado_anterior: Mapped[str] = mapped_column(String(30))
    estado_nuevo: Mapped[str] = mapped_column(String(30))
    transiciones: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    reglas: Mapped[dict[str, Any]] = mapped_column(JSON)
    motivo: Mapped[str] = mapped_column(String(1000))
    decidida_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlacion: Mapped[str] = mapped_column(String(36))


class AsignacionSiniestroModel(Base):
    __tablename__ = "asignaciones_siniestro"
    __table_args__ = (
        UniqueConstraint("decision_id"),
        CheckConstraint("tipo IN ('perito', 'cola')"),
        CheckConstraint("estado IN ('activa', 'cerrada')"),
        Index("ix_asignaciones_siniestro_destino", "tipo", "destino", "estado", "asignada_en"),
        Index("ix_asignaciones_siniestro_siniestro", "siniestro_id", "estado"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"))
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisiones_siniestro.id"))
    tipo: Mapped[str] = mapped_column(String(10))
    destino: Mapped[str] = mapped_column(String(36))
    estado: Mapped[str] = mapped_column(String(10))
    asignada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _instante(valor: datetime | None) -> str | None:
    return None if valor is None else _utc(valor).isoformat().replace("+00:00", "Z")


class ServicioDecisiones:
    """Aprueba o enruta siniestros con la maquina de estados y decisiones idempotentes."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def decidir_automaticamente(
        self, siniestro_id: UUID, evaluacion: ResultadoEvaluacion, cid: str
    ) -> tuple[dict[str, Any], int]:
        payload = evaluacion.model_dump(mode="json")
        huella = fingerprint({"origen": "automatica", **payload})
        operacion = str(evaluacion.evaluacionId)
        with self.repository.write() as session:
            siniestro = self._siniestro(session, siniestro_id)
            existente = self._existente(session, siniestro.id, operacion, huella)
            if existente is not None:
                return self._registro(session, existente), 200
            validar(session, siniestro)
            if siniestro.estado not in ESTADOS_DECISION_AUTOMATICA:
                raise Problem(409, "transicion_no_permitida")
            dictamen = dictaminar(evaluacion)
            obligatorios = ("en_evaluacion",) if siniestro.estado == "reportado" else ()
            reglas = {
                "politica": POLITICA,
                "version": VERSION_POLITICA,
                "aplicada": dictamen.regla,
                "evaluacion": payload["reglas"],
                "motivoEvaluacion": evaluacion.motivo,
            }
            decision = self._aplicar(
                session,
                siniestro,
                operacion,
                huella,
                ("automatica", "sistema", None),
                dictamen,
                obligatorios,
                reglas,
                cid,
            )
            return self._registro(session, decision), 201

    def decidir_revision(
        self, siniestro_id: UUID, operacion: str, entrada: DecisionRevision, cid: str
    ) -> tuple[dict[str, Any], int]:
        payload = entrada.model_dump(mode="json")
        huella = fingerprint({"origen": "humana", **payload})
        with self.repository.write() as session:
            siniestro = self._siniestro(session, siniestro_id)
            existente = self._existente(session, siniestro.id, operacion, huella)
            if existente is not None:
                return self._registro(session, existente), 200
            validar(session, siniestro)
            try:
                dictamen = dictaminar_revision(siniestro.estado, entrada)
            except TransicionNoPermitida as error:
                raise Problem(409, "transicion_no_permitida") from error
            reglas = {
                "politica": POLITICA,
                "version": VERSION_POLITICA,
                "aplicada": dictamen.regla,
                "evaluacion": [],
            }
            decision = self._aplicar(
                session,
                siniestro,
                operacion,
                huella,
                ("humana", "revisor", str(entrada.revisorId)),
                dictamen,
                (),
                reglas,
                cid,
            )
            return self._registro(session, decision), 201

    def consultar(self, siniestro_id: UUID) -> dict[str, Any]:
        with self.repository.sessions() as session:
            siniestro = self._siniestro(session, siniestro_id)
            decisiones = session.scalars(
                select(DecisionSiniestroModel)
                .where(DecisionSiniestroModel.siniestro_id == siniestro.id)
                .order_by(DecisionSiniestroModel.decidida_en, DecisionSiniestroModel.id)
            ).all()
            activa = session.scalar(
                select(AsignacionSiniestroModel).where(
                    AsignacionSiniestroModel.siniestro_id == siniestro.id,
                    AsignacionSiniestroModel.estado == "activa",
                )
            )
            return {
                "siniestroId": siniestro.id,
                "estado": siniestro.estado,
                "asignacionActiva": None if activa is None else _asignacion(activa),
                "decisiones": [self._registro(session, decision) for decision in decisiones],
            }

    def asignaciones(self, tipo: str, destino: str) -> dict[str, Any]:
        if tipo == "cola" and destino not in COLAS:
            raise Problem(404, "cola_no_encontrada")
        with self.repository.sessions() as session:
            filas = session.execute(
                select(AsignacionSiniestroModel, Claim.estado)
                .join(Claim, Claim.id == AsignacionSiniestroModel.siniestro_id)
                .where(
                    AsignacionSiniestroModel.tipo == tipo,
                    AsignacionSiniestroModel.destino == destino,
                    AsignacionSiniestroModel.estado == "activa",
                )
                .order_by(AsignacionSiniestroModel.asignada_en, AsignacionSiniestroModel.id)
            ).all()
            return {
                "tipo": tipo,
                "destino": destino,
                "asignaciones": [
                    {**_asignacion(asignacion), "estadoSiniestro": estado}
                    for asignacion, estado in filas
                ],
            }

    @staticmethod
    def _siniestro(session: Session, siniestro_id: UUID) -> Claim:
        siniestro = session.get(Claim, str(siniestro_id))
        if siniestro is None:
            raise Problem(404, "siniestro_no_encontrado")
        return siniestro

    @staticmethod
    def _existente(
        session: Session, siniestro_id: str, operacion: str, huella: str
    ) -> DecisionSiniestroModel | None:
        existente = session.scalar(
            select(DecisionSiniestroModel).where(
                DecisionSiniestroModel.siniestro_id == siniestro_id,
                DecisionSiniestroModel.operacion == operacion,
            )
        )
        if existente is not None and existente.huella != huella:
            raise Problem(409, "idempotencia_conflictiva")
        return existente

    def _aplicar(
        self,
        session: Session,
        siniestro: Claim,
        operacion: str,
        huella: str,
        actor: tuple[str, str, str | None],
        dictamen: Dictamen,
        obligatorios: tuple[str, ...],
        reglas: dict[str, Any],
        cid: str,
    ) -> DecisionSiniestroModel:
        origen, actor_tipo, actor_id = actor
        anterior = siniestro.estado
        try:
            pasos = ruta(anterior, dictamen.estado, obligatorios)
        except TransicionNoPermitida as error:
            raise Problem(409, "transicion_no_permitida") from error
        ultimo = session.scalar(
            select(func.max(CambioEstadoModel.secuencia)).where(
                CambioEstadoModel.siniestro_id == siniestro.id
            )
        )
        if ultimo is None or siniestro.actualizado_en is None:
            raise Problem(503, "servicio_no_disponible")
        ahora = max(datetime.now(UTC), _utc(siniestro.actualizado_en))
        transiciones = []
        origen_paso = anterior
        for secuencia, paso in enumerate(pasos, start=ultimo + 1):
            session.add(
                CambioEstadoModel(
                    siniestro_id=siniestro.id,
                    secuencia=secuencia,
                    estado=paso,
                    ocurrido_en=ahora,
                    visible_cliente=True,
                )
            )
            transiciones.append({"de": origen_paso, "a": paso, "secuencia": secuencia})
            origen_paso = paso
        siniestro.estado = dictamen.estado
        siniestro.actualizado_en = ahora
        for activa in session.scalars(
            select(AsignacionSiniestroModel).where(
                AsignacionSiniestroModel.siniestro_id == siniestro.id,
                AsignacionSiniestroModel.estado == "activa",
            )
        ):
            activa.estado = "cerrada"
            activa.cerrada_en = ahora
        decision = DecisionSiniestroModel(
            id=str(uuid4()),
            siniestro_id=siniestro.id,
            operacion=operacion,
            huella=huella,
            origen=origen,
            actor_tipo=actor_tipo,
            actor_id=actor_id,
            resultado=dictamen.resultado,
            estado_anterior=anterior,
            estado_nuevo=dictamen.estado,
            transiciones=transiciones,
            reglas=reglas,
            motivo=dictamen.motivo,
            decidida_en=ahora,
            correlacion=cid,
        )
        session.add(decision)
        session.flush()
        if dictamen.asignacion is not None:
            session.add(
                AsignacionSiniestroModel(
                    id=str(uuid4()),
                    siniestro_id=siniestro.id,
                    decision_id=decision.id,
                    tipo=dictamen.asignacion.tipo,
                    destino=dictamen.asignacion.destino,
                    estado="activa",
                    asignada_en=ahora,
                )
            )
        Repository.audit(
            session,
            actor_id,
            "decidir_siniestro" if origen == "automatica" else "revisar_siniestro",
            dictamen.resultado,
            cid,
        )
        session.flush()
        return decision

    @staticmethod
    def _registro(session: Session, decision: DecisionSiniestroModel) -> dict[str, Any]:
        asignacion = session.scalar(
            select(AsignacionSiniestroModel).where(
                AsignacionSiniestroModel.decision_id == decision.id
            )
        )
        return {
            "id": decision.id,
            "siniestroId": decision.siniestro_id,
            "operacionId": decision.operacion,
            "origen": decision.origen,
            "actor": {"tipo": decision.actor_tipo, "id": decision.actor_id},
            "resultado": decision.resultado,
            "estadoAnterior": decision.estado_anterior,
            "estadoNuevo": decision.estado_nuevo,
            "transiciones": decision.transiciones,
            "reglas": decision.reglas,
            "motivo": decision.motivo,
            "decididaEn": _instante(decision.decidida_en),
            "asignacion": None if asignacion is None else _asignacion(asignacion),
        }


def _asignacion(asignacion: AsignacionSiniestroModel) -> dict[str, Any]:
    return {
        "id": asignacion.id,
        "siniestroId": asignacion.siniestro_id,
        "tipo": asignacion.tipo,
        "destino": asignacion.destino,
        "estado": asignacion.estado,
        "asignadaEn": _instante(asignacion.asignada_en),
        "cerradaEn": _instante(asignacion.cerrada_en),
    }
