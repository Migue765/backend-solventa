from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from perfilamiento.domain import Consentimiento, ErrorPerfilamiento, Fuente, Perfil
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
            consentimiento_id is not None
            and consentimiento.consentimiento_id != consentimiento_id
        ) or (
            consentimiento_version is not None
            and consentimiento.version != consentimiento_version
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
    ) -> Perfil:
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["referenciaId"] = str(referencia_id)
        try:
            consentimiento = self._autorizar(
                cliente_id,
                correlation_id,
                auditoria,
                consentimiento_id,
                consentimiento_version,
            )
            existente = self._repositorio.buscar_por_referencia(cliente_id, referencia_id)
            if existente is not None:
                existente.validar(consentimiento, self._reloj())
                auditoria.update(
                    perfilId=str(existente.perfil_id),
                    resultado="disponible",
                    idempotente=True,
                )
                self._repositorio.auditar(auditoria)
                return existente
            fuentes: list[Fuente] = []
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
                fuentes.append(adaptador.consultar(cliente_id, self._reloj()))
            ahora = self._reloj()
            score = max(fuente.indicador_riesgo for fuente in fuentes)
            perfil = Perfil(
                perfil_id=uuid4(),
                cliente_id=cliente_id,
                referencia_id=referencia_id,
                version=self._repositorio.siguiente_version(cliente_id),
                modelo_version="perfil-mock-1",
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
            self._repositorio.guardar(perfil, auditoria)
            return perfil
        except ErrorPerfilamiento as error:
            auditoria["resultado"] = error.codigo
            self._repositorio.auditar(auditoria)
            raise

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
