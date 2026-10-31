from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from perfilamiento.domain import Consentimiento, ErrorPerfilamiento
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos


class AbrirExcepcionOpenFinance(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation_id: UUID = Field(alias="operationId")
    socio_id: str = Field(alias="socioId", min_length=1, max_length=50)
    cliente_id: UUID = Field(alias="clienteId")
    consentimiento_id: UUID = Field(alias="consentimientoId")
    consentimiento_version: int = Field(alias="consentimientoVersion", ge=1)
    codigo: Literal["fuente_no_disponible", "fuente_vencida", "regla_no_aplicable"]
    motivo_bloqueo: str = Field(alias="motivoBloqueo", min_length=3, max_length=500)
    bloquea_procesamiento: Literal[True] = Field(alias="bloqueaProcesamiento")

    @field_validator("motivo_bloqueo")
    @classmethod
    def motivo_no_vacio(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("El motivo debe incluir al menos tres caracteres.")
        return normalized


class ResolverExcepcionOpenFinance(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    decision: Literal["aprobar", "rechazar"]
    motivo: str = Field(min_length=3, max_length=500)

    @field_validator("motivo")
    @classmethod
    def motivo_no_vacio(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("El motivo debe incluir al menos tres caracteres.")
        return normalized


class ConsentimientoExcepcion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    version: int = Field(ge=1)
    estado: Literal["autorizado", "rechazado", "revocado", "vencido"]
    fuentes: tuple[str, ...]
    datos: tuple[str, ...]
    finalidad: str
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")


class ExcepcionOpenFinance(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    excepcion_id: UUID = Field(alias="excepcionId")
    operation_id: UUID = Field(alias="operationId")
    socio_id: str = Field(alias="socioId")
    cliente_id: UUID = Field(alias="clienteId")
    codigo: Literal["fuente_no_disponible", "fuente_vencida", "regla_no_aplicable"]
    motivo_bloqueo: str = Field(alias="motivoBloqueo")
    alcance: Literal["open_finance"] = "open_finance"
    finalidad: Literal["cotizacion"] = "cotizacion"
    datos: tuple[Literal["indicador_riesgo"], ...] = ("indicador_riesgo",)
    consentimiento: ConsentimientoExcepcion
    estado: Literal["pendiente", "aprobada", "rechazada"]
    estado_anterior: str = Field(alias="estadoAnterior")
    creado_en: AwareDatetime = Field(alias="creadoEn")
    correlation_id: str = Field(alias="correlationId")
    trazabilidad: dict[str, str] = Field(default_factory=dict)
    actor_id: UUID | None = Field(default=None, alias="actorId")
    resuelta_en: AwareDatetime | None = Field(default=None, alias="resueltaEn")
    decision: Literal["aprobar", "rechazar"] | None = None
    motivo: str | None = None
    idempotente: bool = False


class GestionarExcepcionesOpenFinance:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        consentimientos: Consentimientos,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._consentimientos = consentimientos
        self._reloj = reloj

    def abrir(
        self, comando: AbrirExcepcionOpenFinance, correlation_id: str
    ) -> ExcepcionOpenFinance:
        existente = self._repositorio.obtener_excepcion_open_finance_por_operacion(
            comando.socio_id, comando.operation_id
        )
        if existente is not None:
            actual = ExcepcionOpenFinance.model_validate(existente)
            if (
                actual.cliente_id != comando.cliente_id
                or actual.consentimiento.id != comando.consentimiento_id
                or actual.consentimiento.version != comando.consentimiento_version
                or actual.codigo != comando.codigo
                or actual.motivo_bloqueo != comando.motivo_bloqueo
            ):
                raise ErrorPerfilamiento("conflicto_idempotencia", 409)
            return actual.model_copy(update={"idempotente": True})

        consentimiento = self._obtener_consentimiento(
            comando.cliente_id,
            correlation_id,
            comando.consentimiento_id,
            comando.consentimiento_version,
        )
        ahora = self._reloj()
        excepcion = ExcepcionOpenFinance(
            excepcionId=uuid4(),
            operationId=comando.operation_id,
            socioId=comando.socio_id,
            clienteId=comando.cliente_id,
            codigo=comando.codigo,
            motivoBloqueo=comando.motivo_bloqueo,
            consentimiento=self._consentimiento_json(consentimiento),
            estado="pendiente",
            estadoAnterior="procesamiento_automatico_bloqueado",
            creadoEn=ahora,
            correlationId=correlation_id,
            trazabilidad={
                "estadoAnterior": "procesamiento_automatico_bloqueado",
                "estadoActual": "pendiente",
                "alcance": "open_finance",
                "creadoEn": ahora.isoformat(),
                "correlationId": correlation_id,
            },
        )
        contenido = excepcion.model_dump(mode="json", by_alias=True, exclude={"idempotente"})
        evento = {
            "eventoId": str(uuid4()),
            "excepcionId": str(excepcion.excepcion_id),
            "operationId": str(excepcion.operation_id),
            "actorId": None,
            "fecha": ahora.isoformat(),
            "motivo": comando.motivo_bloqueo,
            "alcance": "open_finance",
            "estadoAnterior": "procesamiento_automatico_bloqueado",
            "estadoActual": "pendiente",
            "correlationId": correlation_id,
            "tipo": "ExcepcionOpenFinanceCreada",
        }
        persisted = self._repositorio.registrar_excepcion_open_finance(contenido, evento)
        return ExcepcionOpenFinance.model_validate(persisted)

    def listar_pendientes(self, socio_id: str) -> tuple[ExcepcionOpenFinance, ...]:
        items = self._repositorio.listar_excepciones_open_finance(socio_id, "pendiente")
        return tuple(ExcepcionOpenFinance.model_validate(item) for item in items)

    def obtener(self, excepcion_id: UUID, socio_id: str) -> ExcepcionOpenFinance:
        contenido = self._repositorio.obtener_excepcion_open_finance(excepcion_id, socio_id)
        return ExcepcionOpenFinance.model_validate(contenido)

    def obtener_por_operacion(self, operation_id: UUID, socio_id: str) -> ExcepcionOpenFinance:
        contenido = self._repositorio.obtener_excepcion_open_finance_por_operacion(
            socio_id, operation_id
        )
        if contenido is None:
            raise ErrorPerfilamiento("excepcion_no_encontrada", 404)
        return ExcepcionOpenFinance.model_validate(contenido)

    def resolver(
        self,
        excepcion_id: UUID,
        socio_id: str,
        actor_id: UUID,
        decision: ResolverExcepcionOpenFinance,
        correlation_id: str,
    ) -> ExcepcionOpenFinance:
        actual = self.obtener(excepcion_id, socio_id)
        if actual.estado != "pendiente":
            raise ErrorPerfilamiento("excepcion_ya_resuelta", 409)
        if decision.decision == "aprobar":
            self._obtener_consentimiento(
                actual.cliente_id,
                correlation_id,
                actual.consentimiento.id,
                actual.consentimiento.version,
            )
        ahora = self._reloj()
        contenido = actual.model_copy(
            update={
                "estado": "aprobada" if decision.decision == "aprobar" else "rechazada",
                "estado_anterior": actual.estado,
                "actor_id": actor_id,
                "resuelta_en": ahora,
                "decision": decision.decision,
                "motivo": decision.motivo,
                "correlation_id": correlation_id,
                "trazabilidad": {
                    "estadoAnterior": actual.estado,
                    "estadoActual": ("aprobada" if decision.decision == "aprobar" else "rechazada"),
                    "alcance": "open_finance",
                    "creadoEn": actual.creado_en.isoformat(),
                    "correlationId": correlation_id,
                },
                "idempotente": False,
            }
        ).model_dump(mode="json", by_alias=True, exclude={"idempotente"})
        evento = {
            "eventoId": str(uuid4()),
            "excepcionId": str(excepcion_id),
            "operationId": str(actual.operation_id),
            "actorId": str(actor_id),
            "fecha": ahora.isoformat(),
            "motivo": decision.motivo,
            "alcance": "open_finance",
            "estadoAnterior": actual.estado,
            "estadoActual": contenido["estado"],
            "correlationId": correlation_id,
            "tipo": "ExcepcionOpenFinanceResuelta",
            "decision": decision.decision,
        }
        persistido = self._repositorio.resolver_excepcion_open_finance(
            excepcion_id, socio_id, contenido, evento
        )
        return ExcepcionOpenFinance.model_validate(persistido)

    def _obtener_consentimiento(
        self,
        cliente_id: UUID,
        correlation_id: str,
        consentimiento_id: UUID,
        consentimiento_version: int,
    ) -> Consentimiento:
        consentimiento = self._consentimientos.obtener(cliente_id, correlation_id)
        consentimiento.validar(cliente_id, self._reloj())
        self._repositorio.validar_version_consentimiento(consentimiento)
        if (
            consentimiento.consentimiento_id != consentimiento_id
            or consentimiento.version != consentimiento_version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        return consentimiento

    @staticmethod
    def _consentimiento_json(consentimiento: Consentimiento) -> dict[str, object]:
        return {
            "id": str(consentimiento.consentimiento_id),
            "version": consentimiento.version,
            "estado": consentimiento.estado,
            "fuentes": list(consentimiento.fuentes),
            "datos": list(consentimiento.datos),
            "finalidad": consentimiento.finalidad,
            "vigenteHasta": consentimiento.vigente_hasta.isoformat(),
        }
