import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from perfilamiento.domain import (
    Consentimiento,
    Degradacion,
    ErrorPerfilamiento,
    EstadoFuente,
    Fuente,
    Perfil,
    ReglaRiesgo,
    VariableExcluida,
    VariableRiesgo,
)
from perfilamiento.fuentes import ConsultarFuentes
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento
from perfilamiento.resilience import (
    EjecutorAcotado,
    PoliticaConsultas,
    Presupuesto,
)


class ConsultarPerfil:
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
        self._fuentes = fuentes
        self._reloj = reloj
        self._politica = politica or PoliticaConsultas()
        self._ejecutor = EjecutorAcotado()
        self._consultas_fuentes = ConsultarFuentes(
            repositorio, fuentes, lambda: self._reloj(), self._politica
        )

    def cerrar(self) -> None:
        self._ejecutor.cerrar()

    def _autorizar(
        self,
        cliente_id: UUID,
        correlation_id: str,
        auditoria: dict[str, object],
        presupuesto: Presupuesto,
        consentimiento_id: UUID | None = None,
        consentimiento_version: int | None = None,
        fuentes: tuple[str, ...] = ("open_finance", "open_data"),
    ) -> Consentimiento:
        consentimiento = presupuesto.ejecutar(
            "identidad", lambda: self._consentimientos.obtener(cliente_id, correlation_id)
        )
        auditoria["consentimientoId"] = str(consentimiento.consentimiento_id)
        auditoria["consentimientoVersion"] = consentimiento.version
        consentimiento.validar(cliente_id, self._reloj())
        consentimiento.validar(cliente_id, self._reloj(), fuentes)
        self._repositorio.validar_version_consentimiento(consentimiento)
        if (
            consentimiento_id is not None and consentimiento.consentimiento_id != consentimiento_id
        ) or (
            consentimiento_version is not None and consentimiento.version != consentimiento_version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        return consentimiento

    def generar(
        self,
        cliente_id: UUID,
        referencia_id: UUID,
        correlation_id: str,
        consentimiento_id: UUID | None = None,
        consentimiento_version: int | None = None,
        fuentes_solicitadas: tuple[str, ...] | None = None,
        finalidad: str = "cotizacion",
        datos: tuple[str, ...] = ("indicador_riesgo",),
        operation_id: UUID | None = None,
        credito_id: str | None = None,
    ) -> Perfil:
        operation_id = operation_id or referencia_id
        credito_id = credito_id or str(referencia_id)
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["referenciaId"] = str(referencia_id)
        auditoria["operationId"] = str(operation_id)
        auditoria["creditoId"] = credito_id
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        estados: list[dict[str, object]] = []
        auditoria["estadoFuentes"] = estados
        consentimiento: Consentimiento | None = None
        fuentes: list[Fuente] = []
        degradaciones: list[Degradacion] = []
        conservar_parcial = False
        try:
            nombres = fuentes_solicitadas or tuple(fuente.nombre for fuente in self._fuentes)
            if finalidad != "cotizacion" or datos != ("indicador_riesgo",):
                raise ErrorPerfilamiento("consulta_no_autorizada", 403)
            if (
                fuentes_solicitadas is not None
                and (
                    not {"open_finance", "open_data"}.issubset(nombres)
                    or len(nombres) != len(set(nombres))
                )
            ) or not set(nombres).issubset(fuente.nombre for fuente in self._fuentes):
                raise ErrorPerfilamiento("fuentes_invalidas", 400)

            def autorizar() -> Consentimiento:
                return self._autorizar(
                    cliente_id,
                    correlation_id,
                    auditoria,
                    presupuesto,
                    consentimiento_id,
                    consentimiento_version,
                    nombres,
                )

            consentimiento = autorizar()
            existente = self._repositorio.buscar_por_operacion(cliente_id, operation_id)
            if existente is not None:
                if (
                    existente.referencia_id != referencia_id
                    or existente.credito_id != credito_id
                    or set(nombres) != {fuente.nombre for fuente in existente.fuentes}
                ):
                    raise ErrorPerfilamiento("conflicto_idempotencia", 409)
                existente.validar(consentimiento, self._reloj())
                auditoria.update(
                    perfilId=str(existente.perfil_id),
                    resultado="disponible",
                    idempotente=True,
                )
                self._registrar_tiempos(auditoria, presupuesto)
                self._repositorio.auditar(auditoria)
                return existente
            for adaptador in self._fuentes:
                if adaptador.nombre not in nombres:
                    continue
                estado: dict[str, object] = {
                    "fuente": adaptador.nombre,
                    "estado": "fallida",
                    "intentos": 0,
                }
                estados.append(estado)
                auditoria["fuentes"] = [estado["fuente"] for estado in estados]
                fuente = self._consultas_fuentes.consultar(
                    adaptador,
                    consentimiento,
                    autorizar,
                    presupuesto,
                    estado,
                    degradaciones,
                    permitir_default=True,
                )
                if fuente is not None:
                    fuentes.append(fuente)
            if len(fuentes) != len(nombres) or not fuentes:
                if autorizar() != consentimiento:
                    raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
                conservar_parcial = True
                if any(estado.get("motivo") == "perfil_vencido" for estado in estados):
                    raise ErrorPerfilamiento("perfil_vencido", 422, estados)
                raise ErrorPerfilamiento("fuentes_no_disponibles", 503, estados)
            ahora = self._reloj()
            score = max(fuente.indicador_riesgo for fuente in fuentes)
            variables = tuple(
                VariableRiesgo(
                    nombre="indicador_riesgo",
                    fuente=fuente.nombre,
                    valor_normalizado=fuente.indicador_riesgo,
                    autorizada_por=consentimiento.consentimiento_id,
                )
                for fuente in fuentes
                if "indicador_riesgo" in consentimiento.datos
            )
            variables_excluidas = tuple(
                VariableExcluida(
                    nombre=nombre,
                    fuente=fuente.nombre,
                    motivo="sin_consentimiento",
                )
                for fuente in fuentes
                for nombre in fuente.variables_disponibles
                if nombre not in consentimiento.datos
            )
            regla = ReglaRiesgo(
                regla_id="riesgo-maximo-fuentes",
                version="1.0",
                modelo_version="perfil-riesgo-1",
                expresion="max(indicador_riesgo_por_fuente)",
            )
            evidencia = {
                "clienteId": str(cliente_id),
                "creditoId": credito_id,
                "operationId": str(operation_id),
                "consentimientoId": str(consentimiento.consentimiento_id),
                "consentimientoVersion": consentimiento.version,
                "fuentes": [fuente.model_dump(mode="json", by_alias=True) for fuente in fuentes],
                "variables": [
                    variable.model_dump(mode="json", by_alias=True) for variable in variables
                ],
                "variablesExcluidas": [
                    variable.model_dump(mode="json", by_alias=True)
                    for variable in variables_excluidas
                ],
                "regla": regla.model_dump(mode="json", by_alias=True),
                "degradaciones": [
                    degradacion.model_dump(mode="json", by_alias=True)
                    for degradacion in degradaciones
                ],
                "score": str(score),
            }
            perfil = Perfil(
                perfil_id=uuid4(),
                cliente_id=cliente_id,
                referencia_id=referencia_id,
                operation_id=operation_id,
                credito_id=credito_id,
                version=1,
                modelo_version="perfil-riesgo-1",
                score=score,
                nivel="A" if score <= 0.5 else "B" if score <= 0.75 else "C",
                consentimiento_id=consentimiento.consentimiento_id,
                consentimiento_version=consentimiento.version,
                calculado_en=ahora,
                vigente_hasta=min(
                    consentimiento.vigente_hasta,
                    ahora + timedelta(minutes=5 if degradaciones else 15),
                    *(fuente.vigente_hasta for fuente in fuentes),
                ),
                fuentes=tuple(fuentes),
                variables=variables,
                variables_excluidas=variables_excluidas,
                regla=regla,
                degradaciones=tuple(degradaciones),
                hash_decision=hashlib.sha256(
                    json.dumps(evidencia, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                fallback_used=bool(degradaciones),
                estado_fuentes=tuple(EstadoFuente.model_validate(estado) for estado in estados),
            )
            perfil.validar(
                autorizar(),
                self._reloj(),
            )
            auditoria.update(perfilId=str(perfil.perfil_id), resultado="disponible")
            self._registrar_tiempos(auditoria, presupuesto)
            return self._repositorio.guardar(perfil, auditoria)
        except ErrorPerfilamiento as error:
            auditoria["resultado"] = error.codigo
            self._registrar_tiempos(auditoria, presupuesto)
            self._repositorio.auditar(
                auditoria,
                consentimiento if conservar_parcial else None,
                tuple(fuentes) if conservar_parcial else (),
            )
            raise

    @staticmethod
    def _registrar_tiempos(auditoria: dict[str, object], presupuesto: Presupuesto) -> None:
        auditoria["dependencias"] = presupuesto.dependencias
        auditoria["duracionMs"] = round((monotonic() - presupuesto.inicio) * 1000, 2)
        auditoria["presupuestoMs"] = presupuesto.politica.journey_segundos * 1000

    def obtener(self, perfil_id: UUID, cliente_id: UUID, correlation_id: str) -> Perfil:
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["perfilId"] = str(perfil_id)
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        try:
            perfil = self._repositorio.obtener(perfil_id, cliente_id)
            consentimiento = self._autorizar(cliente_id, correlation_id, auditoria, presupuesto)
            perfil.validar(consentimiento, self._reloj())
            auditoria["fuentes"] = [fuente.nombre for fuente in perfil.fuentes]
            auditoria["resultado"] = "disponible"
            self._registrar_tiempos(auditoria, presupuesto)
            self._repositorio.auditar(auditoria)
            return perfil
        except ErrorPerfilamiento as error:
            auditoria["resultado"] = error.codigo
            self._registrar_tiempos(auditoria, presupuesto)
            self._repositorio.auditar(auditoria)
            raise

    def _auditoria(self, cliente_id: UUID, correlation_id: str) -> dict[str, object]:
        return {
            "clienteId": str(cliente_id),
            "correlationId": correlation_id,
            "fecha": self._reloj().isoformat(),
            "fuentes": [],
        }
