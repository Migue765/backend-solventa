"""Persistencia, enrutamiento y continuidad del motor de fraude (HU29).

Expone ``MotorFraude`` como interfaz desacoplada: otros flujos solo invocan
``detectar_en_flujo`` o consultan ``consultar``/``bloqueado`` sin conocer las reglas.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from siniestros.domain import ClaimInput, Problem
from siniestros.domain.decision import ruta
from siniestros.domain.fraude import (
    RANGO_PRIORIDAD,
    SENALES_CALCULADAS,
    VERSION_MOTOR,
    ConfiguracionFraude,
    ReglaFraude,
    VersionRegla,
    evaluar,
    vigentes,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Base, Claim, Upload
from siniestros.repository import Repository, fingerprint
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

logger = logging.getLogger(__name__)

ESTADOS_ENRUTABLES = frozenset({"reportado", "en_evaluacion"})


class FraudRule(Base):
    __tablename__ = "reglas_fraude"
    __table_args__ = (
        CheckConstraint("version > 0"),
        CheckConstraint("peso >= 1 AND peso <= 100"),
        CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        UniqueConstraint("regla_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    regla_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    nombre: Mapped[str] = mapped_column(String(100))
    motivo: Mapped[str] = mapped_column(String(300))
    senal: Mapped[str] = mapped_column(String(60))
    operador: Mapped[str] = mapped_column(String(8))
    umbral: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    peso: Mapped[int] = mapped_column(Integer)
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    vigente_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FraudEvaluation(Base):
    __tablename__ = "evaluaciones_fraude"
    __table_args__ = (
        CheckConstraint("resultado IN ('sospechoso', 'legitimo', 'indeterminado')"),
        CheckConstraint("accion IN ('revision_humana', 'continuar')"),
        Index("ix_evaluaciones_fraude_siniestro", "siniestro_id", "evaluada_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"))
    huella: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resultado: Mapped[str] = mapped_column(String(20))
    accion: Mapped[str] = mapped_column(String(20))
    puntaje: Mapped[int] = mapped_column(Integer)
    prioridad: Mapped[str | None] = mapped_column(String(10), nullable=True)
    motivo: Mapped[str] = mapped_column(String(2000))
    senales: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidencia: Mapped[list[Any]] = mapped_column(JSON)
    reglas: Mapped[list[Any]] = mapped_column(JSON)
    version_motor: Mapped[str] = mapped_column(String(20))
    politica: Mapped[str | None] = mapped_column(String(20), nullable=True)
    incidente: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duracion_ms: Mapped[int] = mapped_column(Integer)
    origen: Mapped[str] = mapped_column(String(20))
    correlation: Mapped[str] = mapped_column(String(36))
    evaluada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FraudReviewCase(Base):
    __tablename__ = "casos_revision_fraude"
    __table_args__ = (
        CheckConstraint("prioridad IN ('alta', 'media', 'baja')"),
        CheckConstraint("estado IN ('pendiente', 'resuelto')"),
        Index("ix_casos_revision_fraude_cola", "estado", "rango_prioridad", "creado_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"), unique=True)
    evaluacion_id: Mapped[str] = mapped_column(ForeignKey("evaluaciones_fraude.id"))
    prioridad: Mapped[str] = mapped_column(String(10))
    rango_prioridad: Mapped[int] = mapped_column(Integer)
    puntaje: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MotorFraude:
    def __init__(self, repository: Repository, configuracion: ConfiguracionFraude) -> None:
        self.repository = repository
        self.configuracion = configuracion

    def registrar_regla(self, regla: ReglaFraude) -> tuple[dict[str, object], int]:
        if regla.vigenteHasta is not None and regla.vigenteHasta <= regla.vigenteDesde:
            raise Problem(422, "vigencia_invalida")
        digest = fingerprint(regla.model_dump(mode="json"))
        with self.repository.write() as session:
            existente = session.scalar(
                select(FraudRule).where(
                    FraudRule.regla_id == str(regla.reglaId),
                    FraudRule.version == regla.version,
                )
            )
            if existente is not None:
                if existente.fingerprint != digest:
                    raise Problem(409, "version_regla_conflictiva")
                return _regla(existente), 200
            modelo = FraudRule(
                id=str(uuid4()),
                regla_id=str(regla.reglaId),
                version=regla.version,
                fingerprint=digest,
                nombre=regla.nombre,
                motivo=regla.motivo,
                senal=regla.senal,
                operador=regla.operador,
                umbral=regla.umbral,
                peso=regla.peso,
                vigente_desde=regla.vigenteDesde,
                vigente_hasta=regla.vigenteHasta,
                creada_en=datetime.now(UTC),
            )
            session.add(modelo)
            session.flush()
            return _regla(modelo), 201

    def detectar(
        self,
        siniestro_id: UUID,
        origen: str,
        cid: str,
        externas: Mapping[str, Decimal] | None = None,
    ) -> dict[str, object]:
        externas = dict(externas or {})
        if SENALES_CALCULADAS & externas.keys():
            raise Problem(422, "senal_reservada")
        inicio = perf_counter()
        try:
            return self._detectar(siniestro_id, origen, cid, externas, inicio)
        except Problem:
            raise
        except Exception as error:
            return self._continuidad(siniestro_id, origen, cid, error, inicio)

    def detectar_en_flujo(self, siniestro_id: UUID, origen: str, cid: str) -> None:
        """Nunca interrumpe el flujo principal: los fallos ya quedan auditados."""
        try:
            self.detectar(siniestro_id, origen, cid)
        except Exception:
            logger.exception(
                "Deteccion de fraude sin resultado; siniestroId=%s correlationId=%s",
                siniestro_id,
                cid,
            )

    def consultar(self, siniestro_id: UUID) -> dict[str, object]:
        with self.repository.sessions() as session:
            if session.get(Claim, str(siniestro_id)) is None:
                raise Problem(404, "siniestro_no_encontrado")
            ultima = _ultima(session, str(siniestro_id))
            if ultima is None:
                raise Problem(404, "evaluacion_no_encontrada")
            return _evaluacion(ultima, self.configuracion)

    def bloqueado(self, siniestro_id: UUID) -> bool:
        with self.repository.sessions() as session:
            return (
                session.scalar(
                    select(FraudReviewCase.id).where(
                        FraudReviewCase.siniestro_id == str(siniestro_id),
                        FraudReviewCase.estado == "pendiente",
                    )
                )
                is not None
            )

    def pendientes(self, limite: int) -> dict[str, object]:
        with self.repository.sessions() as session:
            filas = session.execute(
                select(FraudReviewCase, FraudEvaluation, Claim)
                .join(FraudEvaluation, FraudEvaluation.id == FraudReviewCase.evaluacion_id)
                .join(Claim, Claim.id == FraudReviewCase.siniestro_id)
                .where(FraudReviewCase.estado == "pendiente")
                .order_by(
                    FraudReviewCase.rango_prioridad.desc(),
                    FraudReviewCase.puntaje.desc(),
                    FraudReviewCase.creado_en,
                    FraudReviewCase.id,
                )
                .limit(limite)
            ).all()
            evidencias = dict(
                session.execute(
                    select(Upload.claim_id, func.count())
                    .where(
                        Upload.claim_id.in_([fila[2].id for fila in filas]),
                        Upload.status == "confirmada",
                    )
                    .group_by(Upload.claim_id)
                ).all()
            )
            return {
                "casos": [
                    {
                        "casoId": caso.id,
                        "prioridad": caso.prioridad,
                        "puntaje": caso.puntaje,
                        "estado": caso.estado,
                        "creadoEn": _instante(caso.creado_en),
                        "siniestro": _contexto(claim, int(evidencias.get(claim.id, 0))),
                        "evaluacion": _evaluacion(evaluacion, self.configuracion),
                    }
                    for caso, evaluacion, claim in filas
                ]
            }

    def _detectar(
        self,
        siniestro_id: UUID,
        origen: str,
        cid: str,
        externas: dict[str, Decimal],
        inicio: float,
    ) -> dict[str, object]:
        with self.repository.write() as session:
            claim = session.get(Claim, str(siniestro_id), with_for_update=True)
            if claim is None:
                raise Problem(404, "siniestro_no_encontrado")
            ahora = datetime.now(UTC)
            senales = {**externas, **_senales(session, claim)}
            reglas = vigentes(
                [_version(regla) for regla in session.scalars(select(FraudRule))], ahora
            )
            huella = fingerprint(
                {
                    "motor": VERSION_MOTOR,
                    "senales": {nombre: str(valor) for nombre, valor in senales.items()},
                    "reglas": [[regla.regla_id, regla.version] for regla in reglas],
                    "configuracion": [
                        self.configuracion.umbral_sospecha,
                        self.configuracion.umbral_prioridad_alta,
                    ],
                }
            )
            ultima = _ultima(session, claim.id)
            if ultima is not None and ultima.huella == huella:
                return _evaluacion(ultima, self.configuracion)
            resultado = evaluar(senales, reglas, self.configuracion)
            duracion = _duracion(inicio)
            evaluacion = FraudEvaluation(
                id=str(uuid4()),
                siniestro_id=claim.id,
                huella=huella,
                resultado=resultado.resultado,
                accion="revision_humana" if resultado.requiere_revision else "continuar",
                puntaje=resultado.puntaje,
                prioridad=resultado.prioridad,
                motivo=resultado.motivo[:2000],
                senales={nombre: str(valor) for nombre, valor in sorted(senales.items())},
                evidencia=list(resultado.evidencia),
                reglas=list(resultado.reglas),
                version_motor=VERSION_MOTOR,
                politica=None,
                incidente=None,
                duracion_ms=duracion,
                origen=origen,
                correlation=cid,
                evaluada_en=ahora,
            )
            session.add(evaluacion)
            session.flush()
            if resultado.requiere_revision and resultado.prioridad is not None:
                _enrutar(session, claim, evaluacion, resultado.prioridad, ahora)
            Repository.audit(session, claim.owner, "deteccion_fraude", resultado.resultado, cid)
            if duracion > self.configuracion.presupuesto_ms:
                logger.warning(
                    "Deteccion de fraude fuera de presupuesto; duracionMs=%s correlationId=%s",
                    duracion,
                    cid,
                )
                Repository.audit(
                    session, claim.owner, "deteccion_fraude", "fuera_de_presupuesto", cid
                )
            return _evaluacion(evaluacion, self.configuracion)

    def _continuidad(
        self,
        siniestro_id: UUID,
        origen: str,
        cid: str,
        error: Exception,
        inicio: float,
    ) -> dict[str, object]:
        politica = self.configuracion.politica_continuidad
        incidente = type(error).__name__[:100]
        logger.error(
            "Motor de fraude no disponible; politica=%s clase=%s siniestroId=%s correlationId=%s",
            politica,
            incidente,
            siniestro_id,
            cid,
        )
        try:
            with self.repository.write() as session:
                claim = session.get(Claim, str(siniestro_id), with_for_update=True)
                if claim is None:
                    raise Problem(404, "siniestro_no_encontrado")
                ahora = datetime.now(UTC)
                prioridad = "media" if politica == "revision_humana" else None
                evaluacion = FraudEvaluation(
                    id=str(uuid4()),
                    siniestro_id=claim.id,
                    huella=None,
                    resultado="indeterminado",
                    accion=politica,
                    puntaje=0,
                    prioridad=prioridad,
                    motivo=(
                        f"Motor de fraude no disponible ({incidente}); se aplico la "
                        f"politica de continuidad {politica}."
                    ),
                    senales={},
                    evidencia=[],
                    reglas=[],
                    version_motor=VERSION_MOTOR,
                    politica=politica,
                    incidente=incidente,
                    duracion_ms=_duracion(inicio),
                    origen=origen,
                    correlation=cid,
                    evaluada_en=ahora,
                )
                session.add(evaluacion)
                session.flush()
                if prioridad is not None:
                    _enrutar(session, claim, evaluacion, prioridad, ahora)
                Repository.audit(
                    session, claim.owner, "deteccion_fraude_incidente", "motor_no_disponible", cid
                )
                return _evaluacion(evaluacion, self.configuracion)
        except Problem:
            raise
        except Exception as fallo:
            logger.error(
                "No se pudo aplicar la politica de continuidad; clase=%s correlationId=%s",
                type(fallo).__name__,
                cid,
            )
            raise Problem(503, "motor_fraude_no_disponible") from fallo


def _senales(session: Session, claim: Claim) -> dict[str, Decimal]:
    aviso = ClaimInput.model_validate(claim.payload)
    reportado = _utc(datetime.fromisoformat(claim.created))
    horas = Decimal(max(0, int((reportado - aviso.fechaEvento).total_seconds()))) / 3600
    cliente_90 = poliza_30 = 0
    otros = session.execute(
        select(Claim.created, Claim.payload).where(Claim.owner == claim.owner, Claim.id != claim.id)
    )
    for otro in otros:
        creado: str = otro.created
        payload: dict[str, Any] = otro.payload
        instante = _utc(datetime.fromisoformat(creado))
        if reportado - timedelta(days=90) <= instante <= reportado:
            cliente_90 += 1
            if (
                payload.get("polizaId") == str(aviso.polizaId)
                and reportado - timedelta(days=30) <= instante
            ):
                poliza_30 += 1
    cargas = session.scalars(
        select(Upload).where(Upload.claim_id == claim.id, Upload.status == "confirmada")
    ).all()
    huellas = {carga.digest for carga in cargas}
    repetidas = (
        set(
            session.scalars(
                select(Upload.digest).where(
                    Upload.digest.in_(huellas),
                    Upload.claim_id != claim.id,
                    Upload.status == "confirmada",
                )
            )
        )
        if huellas
        else set()
    )
    return {
        "horas_reporte_tras_evento": horas.quantize(Decimal("0.01")),
        "siniestros_cliente_90_dias": Decimal(cliente_90),
        "siniestros_poliza_30_dias": Decimal(poliza_30),
        "evidencias_confirmadas": Decimal(len(cargas)),
        "evidencias_duplicadas": Decimal(sum(carga.digest in repetidas for carga in cargas)),
        "evidencias_sin_ubicacion": Decimal(sum(carga.location is None for carga in cargas)),
    }


def _enrutar(
    session: Session,
    claim: Claim,
    evaluacion: FraudEvaluation,
    prioridad: str,
    ahora: datetime,
) -> None:
    caso = session.scalar(select(FraudReviewCase).where(FraudReviewCase.siniestro_id == claim.id))
    if caso is None:
        session.add(
            FraudReviewCase(
                id=str(uuid4()),
                siniestro_id=claim.id,
                evaluacion_id=evaluacion.id,
                prioridad=prioridad,
                rango_prioridad=RANGO_PRIORIDAD[prioridad],
                puntaje=evaluacion.puntaje,
                estado="pendiente",
                creado_en=ahora,
                actualizado_en=ahora,
            )
        )
    else:
        if caso.estado != "pendiente" or RANGO_PRIORIDAD[prioridad] >= caso.rango_prioridad:
            caso.prioridad = prioridad
            caso.rango_prioridad = RANGO_PRIORIDAD[prioridad]
        caso.evaluacion_id = evaluacion.id
        caso.puntaje = max(caso.puntaje, evaluacion.puntaje)
        caso.estado = "pendiente"
        caso.actualizado_en = ahora
    if claim.estado not in ESTADOS_ENRUTABLES:
        return
    ultimo = session.scalar(
        select(CambioEstadoModel)
        .where(CambioEstadoModel.siniestro_id == claim.id)
        .order_by(CambioEstadoModel.secuencia.desc())
    )
    instante = max(ahora, _utc(ultimo.ocurrido_en)) if ultimo is not None else ahora
    obligatorios = ("en_evaluacion",) if claim.estado == "reportado" else ()
    for desplazamiento, paso in enumerate(ruta(claim.estado, "en_revision", obligatorios), 1):
        session.add(
            CambioEstadoModel(
                siniestro_id=claim.id,
                secuencia=(ultimo.secuencia if ultimo is not None else 0) + desplazamiento,
                estado=paso,
                ocurrido_en=instante,
                visible_cliente=True,
            )
        )
    claim.estado = "en_revision"
    claim.actualizado_en = instante


def _ultima(session: Session, siniestro_id: str) -> FraudEvaluation | None:
    return session.scalar(
        select(FraudEvaluation)
        .where(FraudEvaluation.siniestro_id == siniestro_id)
        .order_by(FraudEvaluation.evaluada_en.desc(), FraudEvaluation.id.desc())
        .limit(1)
    )


def _version(regla: FraudRule) -> VersionRegla:
    return VersionRegla(
        regla.regla_id,
        regla.version,
        regla.nombre,
        regla.motivo,
        regla.senal,
        regla.operador,
        Decimal(regla.umbral),
        regla.peso,
        regla.vigente_desde,
        regla.vigente_hasta,
    )


def _evaluacion(evaluacion: FraudEvaluation, config: ConfiguracionFraude) -> dict[str, object]:
    return {
        "evaluacionId": evaluacion.id,
        "siniestroId": evaluacion.siniestro_id,
        "resultado": evaluacion.resultado,
        "accion": evaluacion.accion,
        "bloqueado": evaluacion.accion == "revision_humana",
        "puntaje": evaluacion.puntaje,
        "prioridad": evaluacion.prioridad,
        "motivo": evaluacion.motivo,
        "senales": evaluacion.senales,
        "evidencia": evaluacion.evidencia,
        "reglas": evaluacion.reglas,
        "versionMotor": evaluacion.version_motor,
        "politicaContinuidad": evaluacion.politica,
        "incidente": evaluacion.incidente,
        "duracionMs": evaluacion.duracion_ms,
        "dentroDePresupuesto": evaluacion.duracion_ms <= config.presupuesto_ms,
        "origen": evaluacion.origen,
        "correlationId": evaluacion.correlation,
        "evaluadaEn": _instante(evaluacion.evaluada_en),
    }


def _contexto(claim: Claim, evidencias: int) -> dict[str, object]:
    payload = claim.payload
    return {
        "id": claim.id,
        "clienteId": claim.owner,
        "polizaId": payload.get("polizaId"),
        "tipo": claim.tipo,
        "estado": claim.estado,
        "tipoEvento": payload.get("tipoEvento"),
        "fechaEvento": payload.get("fechaEvento"),
        "lugar": payload.get("lugar"),
        "descripcion": payload.get("descripcion"),
        "reportadoEn": _instante(datetime.fromisoformat(claim.created)),
        "evidenciasConfirmadas": evidencias,
    }


def _regla(regla: FraudRule) -> dict[str, object]:
    return {
        "reglaId": regla.regla_id,
        "version": regla.version,
        "nombre": regla.nombre,
        "creadaEn": _instante(regla.creada_en),
    }


def _duracion(inicio: float) -> int:
    return int((perf_counter() - inicio) * 1000)


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _instante(valor: datetime) -> str:
    return _utc(valor).isoformat().replace("+00:00", "Z")
