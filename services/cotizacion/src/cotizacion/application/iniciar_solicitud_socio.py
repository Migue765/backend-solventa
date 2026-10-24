import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.iniciar_operacion_hipotecaria import AdmisionOperaciones
from cotizacion.application.registrar_solicitud import RegistrarSolicitud, RegistrarSolicitudComando
from cotizacion.domain.errors import ConflictoIdempotenciaError, ErrorConsultaPerfil


@dataclass(frozen=True)
class SolicitudSocioComando:
    operation_id: UUID
    socio_id: str
    usuario_id: UUID
    solicitud: RegistrarSolicitudComando


@dataclass(frozen=True)
class OperacionSocio:
    operation_id: UUID
    socio_id: str
    usuario_id: UUID
    cliente_id: UUID
    producto_id: str
    solicitud_id: str
    cotizacion_id: UUID
    perfilamiento_referencia: str
    fingerprint: str
    creada_en: datetime


class RepositorioOperacionesSocios(Protocol):
    def buscar(self, socio: str, operation_id: UUID) -> OperacionSocio | None: ...

    def guardar(self, operacion: OperacionSocio, token: UUID) -> None: ...


class IniciarSolicitudSocio:
    def __init__(
        self,
        repositorio: RepositorioOperacionesSocios,
        registrar: RegistrarSolicitud,
        admision: AdmisionOperaciones,
        habilitado: bool,
    ) -> None:
        self._repositorio = repositorio
        self._registrar = registrar
        self._admision = admision
        self._habilitado = habilitado

    def ejecutar(self, comando: SolicitudSocioComando) -> OperacionSocio:
        fingerprint = hashlib.sha256(
            json.dumps(asdict(comando), sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        existente = self._existente(comando, fingerprint)
        if existente is not None:
            return existente
        if not self._habilitado:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        with self._admision.reservar(
            comando.socio_id, comando.operation_id, fingerprint, global_id=True
        ) as token:
            existente = self._existente(comando, fingerprint)
            if existente is not None:
                return existente
            namespace = f"solventa:socio:{comando.socio_id}:{comando.operation_id}"
            solicitud = self._registrar.ejecutar(
                replace(
                    comando.solicitud,
                    idempotency_key=uuid5(NAMESPACE_URL, namespace),
                    cotizacion_id=uuid5(NAMESPACE_URL, namespace + ":cotizacion"),
                )
            )
            instantanea = solicitud.cotizacion.instantanea
            if instantanea is None:
                raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
            operacion = OperacionSocio(
                operation_id=comando.operation_id,
                socio_id=comando.socio_id,
                usuario_id=comando.usuario_id,
                cliente_id=comando.solicitud.cliente_id,
                producto_id=comando.solicitud.producto_id,
                solicitud_id=solicitud.solicitud_id,
                cotizacion_id=solicitud.cotizacion.cotizacion_id,
                perfilamiento_referencia=instantanea.perfil_id,
                fingerprint=fingerprint,
                creada_en=solicitud.creada_en,
            )
            self._repositorio.guardar(operacion, token)
            return operacion

    def _existente(self, comando: SolicitudSocioComando, fingerprint: str) -> OperacionSocio | None:
        existente = self._repositorio.buscar(comando.socio_id, comando.operation_id)
        if existente is not None and existente.fingerprint != fingerprint:
            raise ConflictoIdempotenciaError()
        return existente
