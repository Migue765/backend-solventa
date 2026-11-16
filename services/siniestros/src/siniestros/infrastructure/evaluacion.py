"""Persistencia e integracion de la evaluacion automatica de siniestros (HU26)."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from siniestros.domain import ClaimInput, Problem
from siniestros.domain.estado import SiniestroNoIntegro
from siniestros.domain.evaluacion import (
    FALLO_EVALUADOR,
    FRAUDE_NIVEL,
    SIN_REGLAS_VIGENTES,
    SINIESTRO_NO_INTEGRO,
    ConjuntoReglasEvaluacion,
    EntradasEvaluacion,
    ResultadoEvaluacion,
    SenalFraude,
    evaluar,
    nivel_fraude,
    pendiente,
)
from siniestros.infrastructure.modelos_evaluacion import (
    EvaluacionSiniestroModel,
    ReglasEvaluacionModel,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.infrastructure.repositorio import validar
from siniestros.models import Claim, Upload
from siniestros.repository import Repository, fingerprint
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
NIVELES_TEXTO = {0: "bajo", 1: "bajo", 2: "medio", 3: "alto"}


class ProveedorSenalesFraude(Protocol):
    """Punto de integracion con la defensa antifraude (ASR-FRD-01, SOL-181).

    Devuelve None cuando no hay senales disponibles para el siniestro.
    """

    def senales(self, sesion: Session, siniestro_id: str) -> Sequence[SenalFraude] | None: ...


class SinSenalesFraude:
    def senales(self, sesion: Session, siniestro_id: str) -> Sequence[SenalFraude] | None:
        return None


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _instante(valor: datetime) -> str:
    return _utc(valor).isoformat().replace("+00:00", "Z")


class RepositorioEvaluaciones:
    def __init__(
        self, repository: Repository, proveedor_fraude: ProveedorSenalesFraude | None = None
    ) -> None:
        self.repository = repository
        self.proveedor_fraude = proveedor_fraude or SinSenalesFraude()

    def registrar_reglas(self, conjunto: ConjuntoReglasEvaluacion) -> tuple[dict[str, Any], int]:
        if conjunto.vigenteHasta is not None and conjunto.vigenteHasta <= conjunto.vigenteDesde:
            raise Problem(422, "vigencia_invalida")
        definicion = conjunto.model_dump(mode="json")
        huella = fingerprint(definicion)
        with self.repository.write() as sesion:
            existente = sesion.scalar(
                select(ReglasEvaluacionModel).where(
                    ReglasEvaluacionModel.version == conjunto.version
                )
            )
            if existente is not None:
                if existente.huella != huella:
                    raise Problem(409, "version_regla_conflictiva")
                return _reglas(existente), 200
            modelo = ReglasEvaluacionModel(
                id=str(uuid4()),
                version=conjunto.version,
                huella=huella,
                definicion=definicion,
                vigente_desde=conjunto.vigenteDesde,
                vigente_hasta=conjunto.vigenteHasta,
                creada_en=datetime.now(UTC),
            )
            sesion.add(modelo)
            sesion.flush()
            return _reglas(modelo), 201

    def evaluar(
        self,
        siniestro_id: UUID,
        senales: list[SenalFraude] | None,
        correlacion: str,
    ) -> tuple[dict[str, Any], int]:
        with self.repository.write() as sesion:
            siniestro = sesion.scalar(
                select(Claim).where(Claim.id == str(siniestro_id)).with_for_update()
            )
            if siniestro is None:
                raise Problem(404, "siniestro_no_encontrado")
            if siniestro.tipo == "parametrico":
                raise Problem(409, "siniestro_no_evaluable")
            ahora = datetime.now(UTC)
            evaluacion, creada = self._evaluar(
                sesion, siniestro, self._vigentes(sesion, ahora), senales, correlacion, ahora
            )
            return serializar(evaluacion), 201 if creada else 200

    def procesar(self, limite: int) -> dict[str, int]:
        """Evalua siniestros reportados sin evaluacion vigente para su evidencia actual."""
        correlacion = str(uuid4())
        conteo = {"evaluados": 0, "decididos": 0, "informacionInsuficiente": 0}
        conteo["pendientesRevision"] = 0
        with self.repository.write() as sesion:
            ahora = datetime.now(UTC)
            reglas = self._vigentes(sesion, ahora)
            evidencias = (
                select(func.count())
                .select_from(Upload)
                .where(Upload.claim_id == Claim.id, Upload.status == "confirmada")
                .correlate(Claim)
                .scalar_subquery()
            )
            version = (
                EvaluacionSiniestroModel.reglas_version.is_(None)
                if reglas is None
                else EvaluacionSiniestroModel.reglas_version == reglas.version
            )
            evaluado = (
                exists()
                .where(
                    EvaluacionSiniestroModel.siniestro_id == Claim.id,
                    EvaluacionSiniestroModel.cantidad_evidencias == evidencias,
                    version,
                )
                .correlate(Claim)
            )
            pendientes = sesion.scalars(
                select(Claim)
                .where(
                    Claim.estado == "reportado",
                    or_(Claim.tipo.is_(None), Claim.tipo == "asistido"),
                    ~evaluado,
                )
                .order_by(Claim.created, Claim.id)
                .limit(limite)
            ).all()
            for siniestro in pendientes:
                evaluacion, creada = self._evaluar(
                    sesion, siniestro, reglas, None, correlacion, ahora
                )
                if not creada:
                    continue
                conteo["evaluados"] += 1
                clave = {
                    "decidido": "decididos",
                    "informacion_insuficiente": "informacionInsuficiente",
                    "pendiente_revision": "pendientesRevision",
                }[evaluacion.resultado]
                conteo[clave] += 1
        return conteo

    def historial(self, siniestro_id: UUID) -> dict[str, Any]:
        with self.repository.sessions() as sesion:
            if sesion.get(Claim, str(siniestro_id)) is None:
                raise Problem(404, "siniestro_no_encontrado")
            evaluaciones = sesion.scalars(
                select(EvaluacionSiniestroModel)
                .where(EvaluacionSiniestroModel.siniestro_id == str(siniestro_id))
                .order_by(EvaluacionSiniestroModel.evaluada_en, EvaluacionSiniestroModel.id)
            ).all()
            return {"evaluaciones": [serializar(evaluacion) for evaluacion in evaluaciones]}

    @staticmethod
    def _vigentes(sesion: Session, ahora: datetime) -> ReglasEvaluacionModel | None:
        return sesion.scalar(
            select(ReglasEvaluacionModel)
            .where(
                ReglasEvaluacionModel.vigente_desde <= ahora,
                or_(
                    ReglasEvaluacionModel.vigente_hasta.is_(None),
                    ReglasEvaluacionModel.vigente_hasta > ahora,
                ),
            )
            .order_by(ReglasEvaluacionModel.version.desc())
            .limit(1)
        )

    def _evaluar(
        self,
        sesion: Session,
        siniestro: Claim,
        reglas: ReglasEvaluacionModel | None,
        senales: list[SenalFraude] | None,
        correlacion: str,
        ahora: datetime,
    ) -> tuple[EvaluacionSiniestroModel, bool]:
        version = reglas.version if reglas is not None else None
        cargas = sesion.scalars(
            select(Upload)
            .where(Upload.claim_id == siniestro.id, Upload.status == "confirmada")
            .order_by(Upload.id)
        ).all()
        try:
            validar(sesion, siniestro)
            aviso = ClaimInput.model_validate(siniestro.payload)
        except SiniestroNoIntegro:
            logger.error(
                "Siniestro no integro durante la evaluacion; correlationId=%s", correlacion
            )
            entradas_dict: dict[str, Any] = {
                "siniestroId": siniestro.id,
                "cantidadEvidencias": len(cargas),
            }
            return self._guardar(
                sesion,
                siniestro,
                version,
                entradas_dict,
                len(cargas),
                pendiente(SINIESTRO_NO_INTEGRO),
                siniestro.estado,
                correlacion,
                ahora,
            )
        if senales is None:
            disponibles = self.proveedor_fraude.senales(sesion, siniestro.id)
            senales = list(disponibles) if disponibles is not None else None
        reportado = _utc(datetime.fromisoformat(siniestro.created))
        entradas = EntradasEvaluacion(
            siniestroId=siniestro.id,
            polizaId=str(aviso.polizaId),
            tipoEvento=aviso.tipoEvento,
            fechaEvento=_instante(aviso.fechaEvento),
            reportadoEn=_instante(reportado),
            diasHastaReporte=max(0, int((reportado - aviso.fechaEvento).total_seconds() // 86400)),
            longitudDescripcion=len(aviso.descripcion),
            cantidadEvidencias=len(cargas),
            cantidadEvidenciasConUbicacion=sum(carga.location is not None for carga in cargas),
            evidencias=tuple(
                {"id": carga.id, "mimeType": carga.mime, "sha256": carga.digest} for carga in cargas
            ),
            senalesFraudeDisponibles=senales is not None,
            senalesFraude=tuple(
                sorted(
                    (senal.model_dump() for senal in senales or []),
                    key=lambda senal: (senal["fuente"], senal["codigo"], senal["nivel"]),
                )
            ),
            nivelFraude=nivel_fraude(senales),
            cantidadSenalesFraude=len(senales or []),
        )
        if reglas is None:
            resultado = pendiente(SIN_REGLAS_VIGENTES)
        else:
            try:
                resultado = evaluar(
                    ConjuntoReglasEvaluacion.model_validate(reglas.definicion), entradas
                )
            except Exception:
                # Cualquier fallo del evaluador deja el caso pendiente; nunca lo aprueba.
                logger.exception("Fallo del evaluador; correlationId=%s", correlacion)
                resultado = pendiente(FALLO_EVALUADOR)
        estado = (
            "en_evaluacion"
            if siniestro.estado == "reportado" and resultado.resultado != "informacion_insuficiente"
            else siniestro.estado
        )
        return self._guardar(
            sesion,
            siniestro,
            version,
            entradas.como_dict(),
            len(cargas),
            resultado,
            estado,
            correlacion,
            ahora,
        )

    @staticmethod
    def _guardar(
        sesion: Session,
        siniestro: Claim,
        version: int | None,
        entradas: dict[str, Any],
        cantidad_evidencias: int,
        resultado: ResultadoEvaluacion,
        estado_resultante: str,
        correlacion: str,
        ahora: datetime,
    ) -> tuple[EvaluacionSiniestroModel, bool]:
        huella = fingerprint({"reglasVersion": version, "entradas": entradas})
        existente = sesion.scalar(
            select(EvaluacionSiniestroModel).where(
                EvaluacionSiniestroModel.siniestro_id == siniestro.id,
                EvaluacionSiniestroModel.huella == huella,
            )
        )
        if existente is not None:
            return existente, False
        if siniestro.estado != "reportado":
            raise Problem(409, "siniestro_no_evaluable")
        evaluacion = EvaluacionSiniestroModel(
            id=str(uuid4()),
            siniestro_id=siniestro.id,
            huella=huella,
            reglas_version=version,
            resultado=resultado.resultado,
            tratamiento=resultado.tratamiento,
            requiere_revision_humana=resultado.requiere_revision_humana,
            entradas=entradas,
            reglas_aplicadas=[
                {"codigo": regla.codigo, "tratamiento": regla.tratamiento, "motivo": regla.motivo}
                for regla in resultado.reglas_aplicadas
            ],
            motivo=resultado.motivo,
            estado_anterior=siniestro.estado,
            estado_resultante=estado_resultante,
            cantidad_evidencias=cantidad_evidencias,
            evaluada_en=ahora,
            correlacion=correlacion,
        )
        sesion.add(evaluacion)
        if estado_resultante != siniestro.estado:
            secuencia = sesion.scalar(
                select(func.max(CambioEstadoModel.secuencia)).where(
                    CambioEstadoModel.siniestro_id == siniestro.id
                )
            )
            sesion.add(
                CambioEstadoModel(
                    siniestro_id=siniestro.id,
                    secuencia=(secuencia or 0) + 1,
                    estado=estado_resultante,
                    ocurrido_en=ahora,
                    visible_cliente=True,
                )
            )
            siniestro.estado = estado_resultante
            siniestro.actualizado_en = ahora
        sesion.flush()
        return evaluacion, True


def decision_automatica(evaluacion: EvaluacionSiniestroModel) -> dict[str, Any] | None:
    """Cuerpo que consume HU27 (SOL-179) para aprobar o enrutar el siniestro."""
    if evaluacion.resultado == "informacion_insuficiente":
        return None
    nivel = int(evaluacion.entradas.get("nivelFraude", 0))
    return {
        "evaluacionId": evaluacion.id,
        "evaluadoEn": _instante(evaluacion.evaluada_en),
        "elegible": evaluacion.tratamiento != "rechazo",
        "requiereInspeccion": evaluacion.tratamiento == "inspeccion",
        "requiereRevisionHumana": evaluacion.requiere_revision_humana,
        "fraude": {
            "nivel": NIVELES_TEXTO[nivel],
            "bloqueante": any(
                regla["codigo"] == FRAUDE_NIVEL for regla in evaluacion.reglas_aplicadas
            ),
        },
        "reglas": (
            []
            if evaluacion.reglas_version is None
            else [
                {
                    "reglaId": regla["codigo"],
                    "version": evaluacion.reglas_version,
                    "resultado": regla["tratamiento"],
                }
                for regla in evaluacion.reglas_aplicadas
            ]
        ),
        "motivo": evaluacion.motivo,
    }


def serializar(evaluacion: EvaluacionSiniestroModel) -> dict[str, Any]:
    return {
        "evaluacionId": evaluacion.id,
        "siniestroId": evaluacion.siniestro_id,
        "reglasVersion": evaluacion.reglas_version,
        "resultado": evaluacion.resultado,
        "tratamiento": evaluacion.tratamiento,
        "requiereRevisionHumana": evaluacion.requiere_revision_humana,
        "motivo": evaluacion.motivo,
        "reglasAplicadas": evaluacion.reglas_aplicadas,
        "entradas": evaluacion.entradas,
        "estadoAnterior": evaluacion.estado_anterior,
        "estadoResultante": evaluacion.estado_resultante,
        "evaluadaEn": _instante(evaluacion.evaluada_en),
        "decisionAutomatica": decision_automatica(evaluacion),
    }


def _reglas(modelo: ReglasEvaluacionModel) -> dict[str, Any]:
    return {
        "version": modelo.version,
        "vigenteDesde": _instante(modelo.vigente_desde),
        "vigenteHasta": None if modelo.vigente_hasta is None else _instante(modelo.vigente_hasta),
        "creadaEn": _instante(modelo.creada_en),
    }
