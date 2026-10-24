import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from cotizacion.application.registrar_solicitud import (
    RegistrarSolicitud,
    RegistrarSolicitudComando,
)
from cotizacion.domain.errors import ConflictoIdempotenciaError, ErrorConsultaPerfil
from cotizacion.domain.operacion_hipotecaria import OperacionHipotecaria


class RepositorioOperacionHipotecaria(Protocol):
    def buscar(self, socio_id: str, operation_id: UUID) -> OperacionHipotecaria | None: ...

    def guardar(self, operacion: OperacionHipotecaria, token: UUID) -> None: ...


class AdmisionOperaciones(Protocol):
    def reservar(
        self, socio: str, operation_id: UUID, fingerprint: str, *, global_id: bool = False
    ) -> AbstractContextManager[UUID]: ...


@dataclass(frozen=True)
class IniciarOperacionHipotecariaComando:
    operation_id: UUID
    socio_id: str
    asesor_id: UUID
    cliente_id: UUID
    tipo_documento: str
    numero_documento: str
    correo: str
    telefono: str
    fecha_nacimiento: str
    referencia_credito: str
    monto_credito: Decimal
    plazo_meses: int
    consentimiento_id: UUID
    consentimiento_version: int


class IniciarOperacionHipotecaria:
    def __init__(
        self,
        repositorio: RepositorioOperacionHipotecaria,
        registrar_solicitud: RegistrarSolicitud,
        perfilamiento_habilitado: bool,
        admision: AdmisionOperaciones,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._registrar_solicitud = registrar_solicitud
        self._perfilamiento_habilitado = perfilamiento_habilitado
        self._admision = admision
        self._reloj = reloj

    def ejecutar(self, comando: IniciarOperacionHipotecariaComando) -> OperacionHipotecaria:
        fingerprint = _fingerprint(comando)
        existente = self._repositorio.buscar(comando.socio_id, comando.operation_id)
        if existente is not None:
            if existente.request_fingerprint != fingerprint:
                raise ConflictoIdempotenciaError()
            return existente
        if not self._perfilamiento_habilitado:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)

        with self._admision.reservar(comando.socio_id, comando.operation_id, fingerprint) as token:
            # Otro worker pudo completar la operacion entre la lectura y la reserva.
            existente = self._repositorio.buscar(comando.socio_id, comando.operation_id)
            if existente is not None:
                if existente.request_fingerprint != fingerprint:
                    raise ConflictoIdempotenciaError()
                return existente
            return self._crear(comando, fingerprint, token)

    def _crear(
        self, comando: IniciarOperacionHipotecariaComando, fingerprint: str, token: UUID
    ) -> OperacionHipotecaria:
        solicitud = self._registrar_solicitud.ejecutar(
            RegistrarSolicitudComando(
                cliente_id=comando.cliente_id,
                producto_id="mortgage-life",
                tipo_cliente="persona_natural",
                tipo_documento=comando.tipo_documento,
                identificacion=comando.numero_documento,
                datos_producto={
                    "birthDate": comando.fecha_nacimiento,
                    "mortgageBalance": str(comando.monto_credito.quantize(Decimal("1"))),
                },
                correo=comando.correo,
                telefono=comando.telefono,
                idempotency_key=uuid5(
                    NAMESPACE_URL,
                    f"solventa:{comando.socio_id}:{comando.operation_id}",
                ),
                consentimiento_id=comando.consentimiento_id,
                consentimiento_version=comando.consentimiento_version,
                cotizacion_id=uuid5(
                    NAMESPACE_URL,
                    f"solventa:cotizacion:{comando.socio_id}:{comando.operation_id}",
                ),
            )
        )
        instantanea = solicitud.cotizacion.instantanea
        if instantanea is None:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        operacion = OperacionHipotecaria(
            operation_id=comando.operation_id,
            socio_id=comando.socio_id,
            asesor_id=comando.asesor_id,
            cliente_id=comando.cliente_id,
            consentimiento_id=comando.consentimiento_id,
            consentimiento_version=comando.consentimiento_version,
            referencia_credito=comando.referencia_credito,
            monto_credito=comando.monto_credito,
            plazo_meses=comando.plazo_meses,
            solicitud_id=solicitud.solicitud_id,
            cotizacion_id=solicitud.cotizacion.cotizacion_id,
            perfilamiento_referencia=instantanea.perfil_id,
            estado="completada",
            request_fingerprint=fingerprint,
            creada_en=self._reloj(),
        )
        self._repositorio.guardar(operacion, token)
        return operacion


def _fingerprint(comando: IniciarOperacionHipotecariaComando) -> str:
    payload = asdict(comando)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
