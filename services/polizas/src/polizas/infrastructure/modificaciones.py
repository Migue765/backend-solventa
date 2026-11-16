from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from polizas.domain.condiciones import Condiciones, detalle_coberturas, iniciales, prima
from polizas.domain.modificacion import (
    CambioNoPermitido,
    ConfirmacionModificacion,
    MotivoNoModificable,
    SinCambios,
    SolicitudModificacion,
    impacto,
    motivo_no_modificable,
    opciones,
    propuesta,
)
from polizas.domain.renovacion import huella
from polizas.infrastructure.models import (
    AuditoriaPolizaModel,
    EndosoModel,
    PolizaModel,
    VersionPolizaModel,
)
from polizas.infrastructure.renovaciones import IdempotenciaConflictiva, PolizaNoEncontrada
from polizas.infrastructure.versiones import condiciones_vigentes, ultima_version
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class PolizaNoModificable(Exception):
    def __init__(self, motivo: MotivoNoModificable) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class VersionDesactualizada(Exception):
    def __init__(self, version_actual: int) -> None:
        super().__init__(str(version_actual))
        self.version_actual = version_actual


class ImpactoCambio(Exception):
    pass


RECHAZOS = (
    PolizaNoModificable,
    VersionDesactualizada,
    ImpactoCambio,
    SinCambios,
    CambioNoPermitido,
    IdempotenciaConflictiva,
)


class RepositorioModificaciones:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def consultar(self, poliza_id: UUID, cliente_id: UUID, ahora: datetime) -> dict[str, object]:
        with self.sesiones() as sesion:
            poliza = _poliza(sesion, poliza_id, cliente_id)
            motivo = motivo_no_modificable(
                poliza.tipo,
                poliza.fecha_inicio,
                poliza.fecha_fin,
                _hoy(ahora),
                cancelada=poliza.estado == "cancelada",
            )
            respuesta: dict[str, object] = {
                "polizaId": str(poliza.poliza_id),
                "tipo": poliza.tipo,
                "vigencia": {
                    "inicio": poliza.fecha_inicio.isoformat(),
                    "fin": poliza.fecha_fin.isoformat(),
                },
                "modificable": motivo is None,
                "motivo": motivo,
            }
            if motivo == "producto_no_modificable":
                return {**respuesta, "versionActual": None, "opciones": None, "historial": []}
            historial = self._historial(sesion, poliza)
            return {
                **respuesta,
                "versionActual": historial[0],
                "opciones": opciones(poliza.tipo) if motivo is None else None,
                "historial": historial,
            }

    def simular(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        solicitud: SolicitudModificacion,
        ahora: datetime,
    ) -> dict[str, object]:
        with self.sesiones() as sesion:
            return self._impacto(sesion, poliza_id, cliente_id, solicitud, ahora)[0]

    def confirmar(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        confirmacion: ConfirmacionModificacion,
        correlation_id: str,
        ahora: datetime,
    ) -> tuple[dict[str, object], bool]:
        solicitud_hash = huella(
            {"polizaId": str(poliza_id), **confirmacion.model_dump(mode="json", by_alias=True)}
        )
        try:
            try:
                with self.sesiones.begin() as sesion:
                    existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                    if existente is not None:
                        return self._resultado(sesion, existente), False
                    endoso = self._aplicar(
                        sesion,
                        poliza_id,
                        cliente_id,
                        clave,
                        confirmacion,
                        solicitud_hash,
                        correlation_id,
                        ahora,
                    )
                    return self._resultado(sesion, endoso), True
            except IntegrityError:
                with self.sesiones() as sesion:
                    existente = _por_clave(sesion, cliente_id, clave, poliza_id, solicitud_hash)
                    if existente is not None:
                        return self._resultado(sesion, existente), False
                    poliza = _poliza(sesion, poliza_id, cliente_id)
                    raise VersionDesactualizada(condiciones_vigentes(sesion, poliza)[0]) from None
        except RECHAZOS as rechazo:
            self._auditar_rechazo(poliza_id, cliente_id, confirmacion, rechazo, correlation_id)
            raise

    def _aplicar(
        self,
        sesion: Session,
        poliza_id: UUID,
        cliente_id: UUID,
        clave: UUID,
        confirmacion: ConfirmacionModificacion,
        solicitud_hash: str,
        correlation_id: str,
        ahora: datetime,
    ) -> EndosoModel:
        resultado, poliza, nuevas = self._impacto(
            sesion, poliza_id, cliente_id, confirmacion, ahora
        )
        if resultado["impactoHash"] != confirmacion.impacto_hash:
            raise ImpactoCambio()
        version_base = confirmacion.version_base
        if ultima_version(sesion, poliza_id) is None:
            sesion.add(
                _version(
                    poliza, 1, iniciales(poliza.tipo), poliza.fecha_inicio, "emision", None, ahora
                )
            )
        endoso = EndosoModel(
            endoso_id=uuid4(),
            poliza_id=poliza_id,
            cliente_id=cliente_id,
            idempotency_key=clave,
            solicitud_hash=solicitud_hash,
            version_anterior=version_base,
            version_nueva=version_base + 1,
            impacto=resultado,
            impacto_hash=confirmacion.impacto_hash,
            creado_en=ahora,
        )
        efectivo = date.fromisoformat(str(resultado["efectivoDesde"]))
        sesion.add(endoso)
        sesion.add(
            _version(
                poliza, version_base + 1, nuevas, efectivo, "modificacion", endoso.endoso_id, ahora
            )
        )
        sesion.add(
            _auditoria(
                poliza_id,
                cliente_id,
                "confirmada",
                None,
                {"endosoId": str(endoso.endoso_id), "versionNueva": version_base + 1},
                correlation_id,
            )
        )
        sesion.flush()
        return endoso

    def _impacto(
        self,
        sesion: Session,
        poliza_id: UUID,
        cliente_id: UUID,
        solicitud: SolicitudModificacion,
        ahora: datetime,
    ) -> tuple[dict[str, object], PolizaModel, Condiciones]:
        poliza = _poliza(sesion, poliza_id, cliente_id)
        hoy = _hoy(ahora)
        motivo = motivo_no_modificable(
            poliza.tipo,
            poliza.fecha_inicio,
            poliza.fecha_fin,
            hoy,
            cancelada=poliza.estado == "cancelada",
        )
        if motivo is not None:
            raise PolizaNoModificable(motivo)
        version, actuales = condiciones_vigentes(sesion, poliza)
        if solicitud.version_base != version:
            raise VersionDesactualizada(version)
        nuevas = propuesta(poliza.tipo, solicitud)
        resultado = impacto(
            poliza.tipo,
            version,
            actuales,
            nuevas,
            (poliza.fecha_inicio, poliza.fecha_fin),
            hoy,
        )
        return resultado, poliza, nuevas

    def _historial(self, sesion: Session, poliza: PolizaModel) -> list[dict[str, object]]:
        versiones = sesion.scalars(
            select(VersionPolizaModel)
            .where(VersionPolizaModel.poliza_id == poliza.poliza_id)
            .order_by(VersionPolizaModel.version.desc())
        ).all()
        if not versiones:
            implicita = _version(
                poliza,
                1,
                iniciales(poliza.tipo),
                poliza.fecha_inicio,
                "emision",
                None,
                poliza.emitida_en,
            )
            return [_serializar_version(poliza.tipo, implicita)]
        return [_serializar_version(poliza.tipo, version) for version in versiones]

    def _resultado(self, sesion: Session, endoso: EndosoModel) -> dict[str, object]:
        poliza = sesion.get(PolizaModel, endoso.poliza_id)
        version = sesion.get(VersionPolizaModel, (endoso.poliza_id, endoso.version_nueva))
        assert poliza is not None and version is not None
        return {
            "endosoId": str(endoso.endoso_id),
            "polizaId": str(endoso.poliza_id),
            "versionAnterior": endoso.version_anterior,
            "versionNueva": endoso.version_nueva,
            "version": _serializar_version(poliza.tipo, version),
            "impacto": endoso.impacto,
            "creadoEn": _utc(endoso.creado_en).isoformat(),
        }

    def _auditar_rechazo(
        self,
        poliza_id: UUID,
        cliente_id: UUID,
        confirmacion: ConfirmacionModificacion,
        rechazo: Exception,
        correlation_id: str,
    ) -> None:
        motivo = getattr(rechazo, "motivo", None) or getattr(rechazo, "campo", None)
        detalle: dict[str, object] = {
            "error": type(rechazo).__name__,
            "solicitud": confirmacion.model_dump(mode="json", by_alias=True),
        }
        with self.sesiones.begin() as sesion:
            sesion.add(
                _auditoria(
                    poliza_id,
                    cliente_id,
                    "rechazada",
                    str(motivo) if motivo is not None else None,
                    detalle,
                    correlation_id,
                )
            )


def _poliza(sesion: Session, poliza_id: UUID, cliente_id: UUID) -> PolizaModel:
    poliza = sesion.get(PolizaModel, poliza_id)
    if poliza is None or poliza.cliente_id != cliente_id:
        raise PolizaNoEncontrada()
    return poliza


def _por_clave(
    sesion: Session, cliente_id: UUID, clave: UUID, poliza_id: UUID, solicitud_hash: str
) -> EndosoModel | None:
    existente = sesion.scalars(
        select(EndosoModel).where(
            EndosoModel.cliente_id == cliente_id, EndosoModel.idempotency_key == clave
        )
    ).first()
    if existente is not None and (
        existente.poliza_id != poliza_id or existente.solicitud_hash != solicitud_hash
    ):
        raise IdempotenciaConflictiva()
    return existente


def _version(
    poliza: PolizaModel,
    numero: int,
    condiciones: Condiciones,
    efectivo: date,
    origen: str,
    endoso_id: UUID | None,
    ahora: datetime,
) -> VersionPolizaModel:
    return VersionPolizaModel(
        poliza_id=poliza.poliza_id,
        version=numero,
        plan=condiciones.plan,
        coberturas=sorted(condiciones.coberturas),
        prima=str(prima(poliza.tipo, condiciones)),
        efectivo_desde=efectivo,
        origen=origen,
        endoso_id=endoso_id,
        creada_en=ahora,
    )


def _serializar_version(tipo: str, version: VersionPolizaModel) -> dict[str, object]:
    condiciones = Condiciones(version.plan, frozenset(version.coberturas))
    return {
        "version": version.version,
        "plan": version.plan,
        "coberturas": detalle_coberturas(tipo, condiciones),
        "prima": {"monto": version.prima, "moneda": "COP", "periodicidad": "mensual"},
        "efectivoDesde": version.efectivo_desde.isoformat(),
        "origen": version.origen,
        "endosoId": str(version.endoso_id) if version.endoso_id else None,
        "creadaEn": _utc(version.creada_en).isoformat(),
    }


def _auditoria(
    poliza_id: UUID,
    cliente_id: UUID,
    resultado: str,
    motivo: str | None,
    detalle: dict[str, object],
    correlation_id: str,
) -> AuditoriaPolizaModel:
    return AuditoriaPolizaModel(
        auditoria_id=uuid4(),
        poliza_id=poliza_id,
        cliente_id=cliente_id,
        accion="modificacion",
        resultado=resultado,
        motivo=motivo,
        detalle=detalle,
        correlation_id=correlation_id[:64],
        ocurrido_en=datetime.now(UTC),
    )


def _hoy(ahora: datetime) -> date:
    return ahora.astimezone(UTC).date()


def _utc(instante: datetime) -> datetime:
    return instante.replace(tzinfo=UTC) if instante.tzinfo is None else instante.astimezone(UTC)
