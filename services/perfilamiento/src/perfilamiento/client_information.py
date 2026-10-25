import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from perfilamiento.domain import (
    ConsultaInformacionCliente,
    ErrorPerfilamiento,
    EvidenciaFuente,
    Fuente,
)
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento

PRODUCTOS_AUTORIZADOS = frozenset(
    {
        "automotive",
        "travel",
        "life",
        "device-protection",
        "parametric",
        "parametric-weather",
        "payment-protection",
        "mortgage-life",
    }
)


@dataclass(frozen=True)
class ConsultarInformacionComando:
    operation_id: UUID
    socio_id: str
    actor_id: UUID
    cliente_id: UUID
    producto_id: str
    finalidad: str
    datos_solicitados: tuple[str, ...]


class CircuitBreakerFuente:
    def __init__(
        self,
        fuente: FuentePerfilamiento,
        reloj: Callable[[], datetime],
        limite_fallos: int = 2,
        recuperacion: timedelta = timedelta(seconds=30),
    ) -> None:
        self.nombre = fuente.nombre
        self._fuente = fuente
        self._reloj = reloj
        self._limite_fallos = limite_fallos
        self._recuperacion = recuperacion
        self._fallos = 0
        self._abierto_hasta: datetime | None = None

    def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
        if self._abierto_hasta is not None and self._reloj() < self._abierto_hasta:
            raise ErrorPerfilamiento("circuito_abierto", 503)
        try:
            resultado = self._fuente.consultar(cliente_id, ahora)
        except ErrorPerfilamiento:
            self._fallos += 1
            if self._fallos >= self._limite_fallos:
                self._abierto_hasta = self._reloj() + self._recuperacion
            raise
        self._fallos = 0
        self._abierto_hasta = None
        return resultado


class ConsultarInformacionCliente:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        consentimientos: Consentimientos,
        fuentes: tuple[FuentePerfilamiento, ...],
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._consentimientos = consentimientos
        self._reloj = reloj
        self._fuentes = {fuente.nombre: CircuitBreakerFuente(fuente, reloj) for fuente in fuentes}

    def ejecutar(
        self, comando: ConsultarInformacionComando, correlation_id: str
    ) -> ConsultaInformacionCliente:
        fingerprint = _fingerprint(comando)
        existente = self._repositorio.buscar_consulta_informacion(
            comando.socio_id, comando.operation_id
        )
        if existente is not None:
            consulta, fingerprint_existente = existente
            if fingerprint_existente != fingerprint:
                raise ErrorPerfilamiento("conflicto_idempotencia", 409)
            return consulta
        if comando.producto_id not in PRODUCTOS_AUTORIZADOS:
            raise ErrorPerfilamiento("producto_no_autorizado", 403)
        if comando.finalidad != "cotizacion":
            raise ErrorPerfilamiento("finalidad_no_autorizada", 403)

        consentimiento = self._consentimientos.obtener(comando.cliente_id, correlation_id)
        consentimiento.validar_consulta(comando.cliente_id, comando.finalidad, self._reloj())
        self._repositorio.validar_version_consentimiento(consentimiento)
        permitidos = set(comando.datos_solicitados) & set(consentimiento.datos)
        fuentes = []
        if "indicador_riesgo" in permitidos:
            for nombre in consentimiento.fuentes:
                fuente = self._fuentes.get(nombre)
                if fuente is None:
                    self._auditar(comando, nombre, "fuente_no_disponible")
                    raise ErrorPerfilamiento("fuente_no_disponible", 503)
                try:
                    resultado = fuente.consultar(comando.cliente_id, self._reloj())
                except ErrorPerfilamiento as error:
                    self._auditar(comando, nombre, error.codigo)
                    raise
                fuentes.append(resultado)
                self._auditar(comando, nombre, "consultada")

        ahora = self._reloj()
        datos: dict[str, str] = {}
        if fuentes:
            datos["indicadorRiesgo"] = str(
                max((fuente.indicador_riesgo for fuente in fuentes), default=Decimal("0"))
            )
        consulta = ConsultaInformacionCliente(
            operation_id=comando.operation_id,
            socio_id=comando.socio_id,
            actor_id=comando.actor_id,
            cliente_id=comando.cliente_id,
            producto_id=comando.producto_id,
            finalidad="cotizacion",
            consentimiento_id=consentimiento.consentimiento_id,
            consentimiento_version=consentimiento.version,
            datos=datos,
            evidencia_fuentes=tuple(
                EvidenciaFuente(
                    fuente=fuente.nombre,
                    version=fuente.version,
                    consultada_en=fuente.consultada_en,
                    simulada=fuente.simulada,
                )
                for fuente in fuentes
            ),
            consultada_en=ahora,
        )
        self._repositorio.guardar_consulta_informacion(consulta, fingerprint)
        return consulta

    def _auditar(self, comando: ConsultarInformacionComando, fuente: str, resultado: str) -> None:
        self._repositorio.auditar(
            {
                "operationId": str(comando.operation_id),
                "socioId": comando.socio_id,
                "actorId": str(comando.actor_id),
                "clienteId": str(comando.cliente_id),
                "productoId": comando.producto_id,
                "finalidad": comando.finalidad,
                "fuente": fuente,
                "fecha": self._reloj().isoformat(),
                "resultado": resultado,
            }
        )


def _fingerprint(comando: ConsultarInformacionComando) -> str:
    serialized = json.dumps(asdict(comando), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
