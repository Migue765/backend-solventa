import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from uuid import UUID

from perfilamiento.domain import (
    Consentimiento,
    ConsultaInformacionCliente,
    Degradacion,
    ErrorPerfilamiento,
    EvidenciaFuente,
    Fuente,
)
from perfilamiento.fuentes import ConsultarFuentes
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento
from perfilamiento.resilience import EjecutorAcotado, PoliticaConsultas, Presupuesto

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


class ConsultarInformacionCliente:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        consentimientos: Consentimientos,
        fuentes: tuple[FuentePerfilamiento, ...],
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        politica: PoliticaConsultas | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._consentimientos = consentimientos
        self._reloj = reloj
        self._politica = politica or PoliticaConsultas()
        self._consultas_fuentes = ConsultarFuentes(
            repositorio, fuentes, lambda: self._reloj(), self._politica
        )
        self._fuentes = {fuente.nombre: fuente for fuente in fuentes}
        self._ejecutor = EjecutorAcotado()

    def cerrar(self) -> None:
        self._ejecutor.cerrar()

    def _autorizar(
        self,
        cliente_id: UUID,
        finalidad: str,
        correlation_id: str,
        presupuesto: Presupuesto,
        esperado: Consentimiento | None = None,
    ) -> Consentimiento:
        consentimiento = presupuesto.ejecutar(
            "identidad", lambda: self._consentimientos.obtener(cliente_id, correlation_id)
        )
        consentimiento.validar_consulta(cliente_id, finalidad, self._reloj())
        self._repositorio.validar_version_consentimiento(consentimiento)
        if esperado is not None and consentimiento != esperado:
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        return consentimiento

    def ejecutar(
        self, comando: ConsultarInformacionComando, correlation_id: str
    ) -> ConsultaInformacionCliente:
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        estados: list[dict[str, object]] = []
        degradaciones: list[Degradacion] = []
        nombre = "identidad"
        consentimiento: Consentimiento | None = None
        try:
            fingerprint = _fingerprint(comando)
            if comando.producto_id not in PRODUCTOS_AUTORIZADOS:
                raise ErrorPerfilamiento("producto_no_autorizado", 403)
            if comando.finalidad != "cotizacion":
                raise ErrorPerfilamiento("finalidad_no_autorizada", 403)

            def autorizar() -> Consentimiento:
                return self._autorizar(
                    comando.cliente_id,
                    comando.finalidad,
                    correlation_id,
                    presupuesto,
                    consentimiento,
                )

            consentimiento = autorizar()
            existente = self._repositorio.buscar_consulta_informacion(
                comando.socio_id, comando.operation_id
            )
            if existente is not None:
                consulta, fingerprint_existente = existente
                if fingerprint_existente != fingerprint:
                    raise ErrorPerfilamiento("conflicto_idempotencia", 409)
                self._validar_consentimiento_consulta(consulta, autorizar())
                return consulta
            permitidos = set(comando.datos_solicitados) & set(consentimiento.datos)
            fuentes: list[Fuente] = []
            frescas: list[Fuente] = []
            if "indicador_riesgo" in permitidos:
                for nombre in consentimiento.fuentes:
                    estado: dict[str, object] = {
                        "fuente": nombre,
                        "estado": "fallida",
                        "intentos": 0,
                    }
                    estados.append(estado)
                    adaptador = self._fuentes.get(nombre)
                    if adaptador is None:
                        estado["motivo"] = "fuente_no_disponible"
                        raise ErrorPerfilamiento("fuente_no_disponible", 503)
                    fuente = self._consultas_fuentes.consultar(
                        adaptador, consentimiento, autorizar, presupuesto, estado, degradaciones
                    )
                    if fuente is None:
                        raise ErrorPerfilamiento(str(estado["motivo"]), 503)
                    fuentes.append(fuente)
                    if estado["estado"] == "disponible":
                        frescas.append(fuente)

            autorizar()
            ahora = self._reloj()
            for fuente in fuentes:
                if not fuente.consultada_en <= ahora < fuente.vigente_hasta:
                    nombre = fuente.nombre
                    raise ErrorPerfilamiento("perfil_vencido", 503)
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
            auditorias = tuple(
                self._auditoria(
                    comando,
                    str(estado["fuente"]),
                    "consultada" if estado["estado"] == "disponible" else "cache",
                    correlation_id,
                    presupuesto,
                    estados,
                    degradaciones,
                )
                for estado in estados
            )
            self._repositorio.guardar_consulta_informacion(
                consulta, fingerprint, tuple(frescas), auditorias
            )
            return consulta
        except ErrorPerfilamiento as error:
            for estado in estados or [{"fuente": nombre, "estado": "fallida"}]:
                resultado = (
                    error.codigo
                    if estado["fuente"] == nombre
                    else "cache"
                    if estado["estado"] == "cache"
                    else "consultada"
                )
                auditoria = self._auditoria(
                    comando,
                    str(estado["fuente"]),
                    resultado,
                    correlation_id,
                    presupuesto,
                    estados,
                    degradaciones,
                )
                auditoria["resultadoConsulta"] = error.codigo
                self._repositorio.auditar(auditoria)
            raise

    def recuperar(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> ConsultaInformacionCliente:
        existente = self._repositorio.buscar_consulta_informacion(socio_id, operation_id)
        if existente is None:
            raise ErrorPerfilamiento("consulta_no_encontrada", 404)
        consulta, _ = existente
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        consentimiento = self._autorizar(
            consulta.cliente_id, consulta.finalidad, correlation_id, presupuesto
        )
        self._validar_consentimiento_consulta(consulta, consentimiento)
        return consulta

    @staticmethod
    def _validar_consentimiento_consulta(
        consulta: ConsultaInformacionCliente, consentimiento: Consentimiento
    ) -> None:
        if (
            consulta.consentimiento_id != consentimiento.consentimiento_id
            or consulta.consentimiento_version != consentimiento.version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)

    def _auditoria(
        self,
        comando: ConsultarInformacionComando,
        fuente: str,
        resultado: str,
        correlation_id: str,
        presupuesto: Presupuesto,
        estados: list[dict[str, object]],
        degradaciones: list[Degradacion],
    ) -> dict[str, object]:
        return {
            "operationId": str(comando.operation_id),
            "socioId": comando.socio_id,
            "actorId": str(comando.actor_id),
            "clienteId": str(comando.cliente_id),
            "productoId": comando.producto_id,
            "finalidad": comando.finalidad,
            "fuente": fuente,
            "fecha": self._reloj().isoformat(),
            "resultado": resultado,
            "correlationId": correlation_id,
            "estadoFuentes": estados,
            "degradaciones": [
                item.model_dump(mode="json", by_alias=True) for item in degradaciones
            ],
            "dependencias": presupuesto.dependencias,
            "duracionMs": round((monotonic() - presupuesto.inicio) * 1000, 2),
            "presupuestoMs": presupuesto.politica.journey_segundos * 1000,
        }


def _fingerprint(comando: ConsultarInformacionComando) -> str:
    serialized = json.dumps(asdict(comando), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
