import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from perfilamiento.domain import (
    Consentimiento,
    Degradacion,
    ErrorPerfilamiento,
    Fuente,
    Perfil,
    ReglaRiesgo,
    VariableExcluida,
    VariableRiesgo,
)
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento


class ConsultarPerfil:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        consentimientos: Consentimientos,
        fuentes: tuple[FuentePerfilamiento, ...],
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._consentimientos = consentimientos
        self._fuentes = fuentes
        self._reloj = reloj

    def _autorizar(
        self,
        cliente_id: UUID,
        correlation_id: str,
        auditoria: dict[str, object],
        consentimiento_id: UUID | None = None,
        consentimiento_version: int | None = None,
    ) -> Consentimiento:
        consentimiento = self._consentimientos.obtener(cliente_id, correlation_id)
        auditoria["consentimientoId"] = str(consentimiento.consentimiento_id)
        auditoria["consentimientoVersion"] = consentimiento.version
        consentimiento.validar(cliente_id, self._reloj())
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
        operation_id: UUID | None = None,
        credito_id: str | None = None,
    ) -> Perfil:
        operation_id = operation_id or referencia_id
        credito_id = credito_id or str(referencia_id)
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["referenciaId"] = str(referencia_id)
        auditoria["operationId"] = str(operation_id)
        auditoria["creditoId"] = credito_id
        try:
            consentimiento = self._autorizar(
                cliente_id,
                correlation_id,
                auditoria,
                consentimiento_id,
                consentimiento_version,
            )
            existente = self._repositorio.buscar_por_operacion(cliente_id, operation_id)
            if existente is not None:
                if existente.referencia_id != referencia_id or existente.credito_id != credito_id:
                    raise ErrorPerfilamiento("conflicto_idempotencia", 409)
                existente.validar(consentimiento, self._reloj())
                auditoria.update(
                    perfilId=str(existente.perfil_id),
                    resultado="disponible",
                    idempotente=True,
                )
                self._repositorio.auditar(auditoria)
                return existente
            fuentes: list[Fuente] = []
            degradaciones: list[Degradacion] = []
            for adaptador in self._fuentes:
                actual = self._autorizar(
                    cliente_id,
                    correlation_id,
                    auditoria,
                    consentimiento_id,
                    consentimiento_version,
                )
                if actual != consentimiento:
                    raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
                auditoria["fuentes"] = [fuente.nombre for fuente in fuentes] + [adaptador.nombre]
                ahora_fuente = self._reloj()
                try:
                    fuente = adaptador.consultar(cliente_id, ahora_fuente)
                    if not fuente.consultada_en <= ahora_fuente < fuente.vigente_hasta:
                        raise ErrorPerfilamiento("fuente_vencida", 422)
                except ErrorPerfilamiento as error:
                    if error.codigo not in {"fuente_no_disponible", "fuente_vencida"}:
                        raise
                    fuente, politica = self._degradar(
                        cliente_id,
                        adaptador.nombre,
                        consentimiento,
                        ahora_fuente,
                    )
                    degradaciones.append(
                        Degradacion(
                            fuente=adaptador.nombre,
                            motivo=error.codigo,
                            politica=politica,
                            version_politica="degradacion-1",
                        )
                    )
                fuentes.append(fuente)
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
                version=self._repositorio.siguiente_version(cliente_id),
                modelo_version="perfil-riesgo-1",
                score=score,
                nivel="A" if score <= 0.5 else "B" if score <= 0.75 else "C",
                consentimiento_id=consentimiento.consentimiento_id,
                consentimiento_version=consentimiento.version,
                calculado_en=ahora,
                vigente_hasta=min(
                    consentimiento.vigente_hasta,
                    ahora + timedelta(minutes=15),
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
            )
            perfil.validar(
                self._autorizar(
                    cliente_id,
                    correlation_id,
                    auditoria,
                    consentimiento_id,
                    consentimiento_version,
                ),
                self._reloj(),
            )
            auditoria.update(perfilId=str(perfil.perfil_id), resultado="disponible")
            return self._repositorio.guardar(perfil, auditoria)
        except ErrorPerfilamiento as error:
            auditoria["resultado"] = error.codigo
            self._repositorio.auditar(auditoria)
            raise

    def _degradar(
        self,
        cliente_id: UUID,
        nombre: Literal["open_finance", "open_data"],
        consentimiento: Consentimiento,
        ahora: datetime,
    ) -> tuple[Fuente, Literal["cache", "default"]]:
        cache = self._repositorio.fuente_en_cache(
            cliente_id,
            nombre,
            consentimiento.consentimiento_id,
            consentimiento.version,
            ahora,
        )
        if cache is not None:
            return (
                cache.model_copy(
                    update={
                        "version": cache.version + ":cache",
                        "vigente_hasta": ahora + timedelta(minutes=5),
                    }
                ),
                "cache",
            )
        valores_default = {
            "open_finance": Decimal("0.50"),
            "open_data": Decimal("0.25"),
        }
        return (
            Fuente(
                nombre=nombre,
                version="default-1",
                consultada_en=ahora,
                vigente_hasta=ahora + timedelta(minutes=5),
                simulada=True,
                indicador_riesgo=valores_default[nombre],
                variables_disponibles=("indicador_riesgo",),
            ),
            "default",
        )

    def obtener(self, perfil_id: UUID, cliente_id: UUID, correlation_id: str) -> Perfil:
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["perfilId"] = str(perfil_id)
        try:
            perfil = self._repositorio.obtener(perfil_id, cliente_id)
            consentimiento = self._autorizar(cliente_id, correlation_id, auditoria)
            perfil.validar(consentimiento, self._reloj())
            auditoria["fuentes"] = [fuente.nombre for fuente in perfil.fuentes]
            auditoria["resultado"] = "disponible"
            self._repositorio.auditar(auditoria)
            return perfil
        except ErrorPerfilamiento as error:
            auditoria["resultado"] = error.codigo
            self._repositorio.auditar(auditoria)
            raise

    def _auditoria(self, cliente_id: UUID, correlation_id: str) -> dict[str, object]:
        return {
            "clienteId": str(cliente_id),
            "correlationId": correlation_id,
            "fecha": self._reloj().isoformat(),
            "fuentes": [],
        }
