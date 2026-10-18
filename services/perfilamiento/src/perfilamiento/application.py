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
        self, cliente_id: UUID, correlation_id: str, auditoria: dict[str, object]
    ) -> Consentimiento:
        consentimiento = self._consentimientos.obtener(cliente_id, correlation_id)
        auditoria["consentimientoId"] = str(consentimiento.consentimiento_id)
        auditoria["consentimientoVersion"] = consentimiento.version
        consentimiento.validar(cliente_id, self._reloj())
        return consentimiento

    def generar(self, cliente_id: UUID, referencia_id: UUID, correlation_id: str) -> Perfil:
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["referenciaId"] = str(referencia_id)
        try:
            consentimiento = self._autorizar(cliente_id, correlation_id, auditoria)
            fuentes: list[Fuente] = []
            for adaptador in self._fuentes:
                actual = self._autorizar(cliente_id, correlation_id, auditoria)
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
            perfil.validar(self._autorizar(cliente_id, correlation_id, auditoria), self._reloj())
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
