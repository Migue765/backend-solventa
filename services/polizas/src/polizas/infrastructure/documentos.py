import json
from datetime import datetime
from uuid import UUID, uuid4

from polizas.domain.consulta import condiciones_detalle, nombre_producto, tiene_condiciones
from polizas.domain.documento import (
    AVISO,
    FORMATO_VERSION,
    ConfirmacionDocumento,
    DocumentoDesactualizado,
    archivo_html,
    canonico,
    hash_contenido,
)
from polizas.infrastructure.consultas import _utc
from polizas.infrastructure.models import (
    AceptacionDocumentoModel,
    AuditoriaPolizaModel,
    DocumentoPolizaModel,
    PolizaModel,
)
from polizas.infrastructure.renovaciones import IdempotenciaConflictiva, PolizaNoEncontrada
from polizas.infrastructure.versiones import condiciones_vigentes
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class RepositorioDocumentos:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def consultar(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        try:
            return self._consultar(poliza_id, cliente_id, ahora)
        except IntegrityError:
            # Dos primeras consultas pueden intentar insertar el mismo hash.
            return self._consultar(poliza_id, cliente_id, ahora)

    def _consultar(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        with self.sesiones.begin() as sesion:
            poliza = _poliza(sesion, poliza_id, cliente_id)
            version, texto = _contenido(sesion, poliza)
            archivo = archivo_html(texto)
            huella = hash_contenido(archivo)
            documento = sesion.scalar(
                select(DocumentoPolizaModel).where(
                    DocumentoPolizaModel.poliza_id == poliza_id,
                    DocumentoPolizaModel.contenido_hash == huella,
                )
            )
            if documento is None:
                documento = DocumentoPolizaModel(
                    documento_id=uuid4(),
                    poliza_id=poliza_id,
                    version_poliza=version,
                    contenido_hash=huella,
                    contenido=texto,
                    archivo=archivo,
                    creada_en=ahora,
                )
                sesion.add(documento)
                sesion.flush()
            historial = [
                _evidencia(sesion, aceptacion)
                for aceptacion in sesion.scalars(
                    select(AceptacionDocumentoModel)
                    .where(
                        AceptacionDocumentoModel.poliza_id == poliza_id,
                        AceptacionDocumentoModel.cliente_id == cliente_id,
                    )
                    .order_by(
                        AceptacionDocumentoModel.aceptada_en.desc(),
                        AceptacionDocumentoModel.aceptacion_id,
                    )
                )
            ]
            actual = next(
                (a for a in historial if a["documentoId"] == str(documento.documento_id)), None
            )
            return {
                **_metadatos(documento),
                "contenido": json.loads(documento.contenido),
                "aceptacionActual": actual,
                "historialAceptaciones": historial,
            }

    def descargar(self, poliza_id: UUID, cliente_id: UUID, documento_id: UUID) -> str:
        with self.sesiones() as sesion:
            _poliza(sesion, poliza_id, cliente_id)
            documento = sesion.get(DocumentoPolizaModel, documento_id)
            if documento is None or documento.poliza_id != poliza_id:
                raise PolizaNoEncontrada()
            return documento.archivo

    def aceptar(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        confirmacion: ConfirmacionDocumento,
        correlation_id: str,
        ahora: datetime,
    ) -> tuple[dict[str, object], bool]:
        solicitud_hash = hash_contenido(
            canonico(
                {
                    "polizaId": str(poliza_id),
                    **confirmacion.model_dump(mode="json", by_alias=True),
                }
            )
        )
        try:
            with self.sesiones.begin() as sesion:
                poliza = _poliza(sesion, poliza_id, cliente_id)
                existente = _por_clave(sesion, cliente_id, clave, solicitud_hash)
                if existente is not None:
                    return _evidencia(sesion, existente), False
                documento = sesion.get(DocumentoPolizaModel, confirmacion.documento_id)
                if documento is None or documento.poliza_id != poliza_id:
                    raise PolizaNoEncontrada()
                version, texto = _contenido(sesion, poliza)
                if (
                    documento.version_poliza != confirmacion.version_poliza
                    or version != confirmacion.version_poliza
                    or documento.contenido_hash != confirmacion.contenido_hash
                    or hash_contenido(archivo_html(texto)) != confirmacion.contenido_hash
                ):
                    raise DocumentoDesactualizado()
                aceptacion = AceptacionDocumentoModel(
                    aceptacion_id=uuid4(),
                    documento_id=documento.documento_id,
                    poliza_id=poliza_id,
                    cliente_id=cliente_id,
                    idempotency_key=clave,
                    solicitud_hash=solicitud_hash,
                    aceptada_en=ahora,
                )
                sesion.add(aceptacion)
                sesion.add(
                    AuditoriaPolizaModel(
                        auditoria_id=uuid4(),
                        poliza_id=poliza_id,
                        cliente_id=cliente_id,
                        accion="aceptacion_simulada",
                        resultado="confirmada",
                        motivo=None,
                        detalle={
                            "aceptacionId": str(aceptacion.aceptacion_id),
                            "documentoId": str(documento.documento_id),
                            "versionPoliza": version,
                            "contenidoHash": documento.contenido_hash,
                        },
                        correlation_id=correlation_id,
                        ocurrido_en=ahora,
                    )
                )
                sesion.flush()
                return _evidencia(sesion, aceptacion), True
        except IntegrityError:
            with self.sesiones() as sesion:
                _poliza(sesion, poliza_id, cliente_id)
                existente = _por_clave(sesion, cliente_id, clave, solicitud_hash)
                if existente is None:
                    raise
                return _evidencia(sesion, existente), False


def _poliza(sesion: Session, poliza_id: UUID, cliente_id: UUID) -> PolizaModel:
    poliza = sesion.scalar(
        select(PolizaModel)
        .where(PolizaModel.poliza_id == poliza_id, PolizaModel.cliente_id == cliente_id)
        .with_for_update()
    )
    if poliza is None:
        raise PolizaNoEncontrada()
    return poliza


def _contenido(sesion: Session, poliza: PolizaModel) -> tuple[int | None, str]:
    vigentes = condiciones_vigentes(sesion, poliza) if tiene_condiciones(poliza.tipo) else None
    return (vigentes[0] if vigentes else None), canonico(
        {
            "formatoVersion": FORMATO_VERSION,
            "simulada": True,
            "aviso": AVISO,
            "polizaId": str(poliza.poliza_id),
            "clienteId": str(poliza.cliente_id),
            "suscripcionId": str(poliza.suscripcion_id),
            "producto": {"tipo": poliza.tipo, "nombre": nombre_producto(poliza.tipo)},
            "vigencia": {
                "inicio": poliza.fecha_inicio.isoformat(),
                "fin": poliza.fecha_fin.isoformat(),
            },
            "estado": poliza.estado,
            "emitidaEn": _utc(poliza.emitida_en).isoformat(),
            "reglaEmision": poliza.regla_version,
            **condiciones_detalle(poliza.tipo, vigentes),
        }
    )


def _por_clave(
    sesion: Session,
    cliente_id: UUID,
    clave: UUID,
    solicitud_hash: str,
) -> AceptacionDocumentoModel | None:
    existente = sesion.scalar(
        select(AceptacionDocumentoModel).where(
            AceptacionDocumentoModel.cliente_id == cliente_id,
            AceptacionDocumentoModel.idempotency_key == clave,
        )
    )
    if existente is not None and existente.solicitud_hash != solicitud_hash:
        raise IdempotenciaConflictiva()
    return existente


def _metadatos(documento: DocumentoPolizaModel) -> dict[str, object]:
    return {
        "documentoId": str(documento.documento_id),
        "polizaId": str(documento.poliza_id),
        "versionPoliza": documento.version_poliza,
        "contenidoHash": documento.contenido_hash,
        "formato": "html",
        "simulada": True,
        "aviso": json.loads(documento.contenido)["aviso"],
    }


def _evidencia(sesion: Session, aceptacion: AceptacionDocumentoModel) -> dict[str, object]:
    documento = sesion.get(DocumentoPolizaModel, aceptacion.documento_id)
    if documento is None:
        raise RuntimeError("La evidencia de aceptacion perdio su documento.")
    return {
        **_metadatos(documento),
        "id": str(aceptacion.aceptacion_id),
        "actorId": str(aceptacion.cliente_id),
        "aceptadaEn": _utc(aceptacion.aceptada_en).isoformat(),
    }
