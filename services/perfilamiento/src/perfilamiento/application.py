from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from perfilamiento.domain import Consentimiento, ErrorPerfilamiento, EstadoFuente, Fuente, Perfil
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento
from perfilamiento.resilience import (
    CircuitBreaker,
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
        nombres = [fuente.nombre for fuente in fuentes]
        if len(nombres) != len(set(nombres)):
            raise ValueError("No se permiten adaptadores duplicados.")
        self._breakers = {nombre: CircuitBreaker(self._politica) for nombre in nombres}

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
    ) -> Perfil:
        auditoria = self._auditoria(cliente_id, correlation_id)
        auditoria["referenciaId"] = str(referencia_id)
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        estados: list[dict[str, object]] = []
        auditoria["estadoFuentes"] = estados
        consentimiento: Consentimiento | None = None
        fuentes: list[Fuente] = []
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
            existente = self._repositorio.buscar_por_referencia(cliente_id, referencia_id)
            if existente is not None:
                if set(nombres) != {fuente.nombre for fuente in existente.fuentes}:
                    raise ErrorPerfilamiento("conflicto_referencia", 409)
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
                fuente = self._consultar_fuente(
                    adaptador, consentimiento, autorizar, presupuesto, estado
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
            perfil = Perfil(
                perfil_id=uuid4(),
                cliente_id=cliente_id,
                referencia_id=referencia_id,
                version=1,
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
                fallback_used=any(estado["estado"] == "cache" for estado in estados),
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

    def _consultar_fuente(
        self,
        adaptador: FuentePerfilamiento,
        consentimiento: Consentimiento,
        autorizar: Callable[[], Consentimiento],
        presupuesto: Presupuesto,
        estado: dict[str, object],
    ) -> Fuente | None:
        def verificar() -> None:
            if autorizar() != consentimiento:
                raise ErrorPerfilamiento("consentimiento_no_vigente", 403)

        def consultar() -> Fuente:
            consentimiento.validar(consentimiento.cliente_id, self._reloj(), (adaptador.nombre,))
            self._repositorio.validar_version_consentimiento(consentimiento)
            try:
                return adaptador.consultar(consentimiento.cliente_id, self._reloj())
            except TimeoutError as error:
                raise ErrorPerfilamiento("dependencia_timeout", 503) from error
            except ErrorPerfilamiento as error:
                codigo = (
                    "dependencia_timeout"
                    if error.codigo == "dependencia_timeout"
                    else "fuente_no_disponible"
                )
                raise ErrorPerfilamiento(codigo, 503) from error
            except (OSError, ValueError) as error:
                raise ErrorPerfilamiento("fuente_no_disponible", 503) from error

        verificar()
        breaker = self._breakers[adaptador.nombre]
        codigo = "circuito_abierto"
        if breaker.permitir():
            try:
                for intento in range(self._politica.intentos):
                    if intento:
                        verificar()
                    estado["intentos"] = intento + 1
                    try:
                        fuente = presupuesto.ejecutar(
                            adaptador.nombre,
                            consultar,
                            reserva=self._politica.objetivo_segundos,
                        )
                        if fuente.nombre != adaptador.nombre:
                            raise ErrorPerfilamiento("respuesta_fuente_invalida", 503)
                        if not fuente.consultada_en <= self._reloj() < fuente.vigente_hasta:
                            raise ErrorPerfilamiento("perfil_vencido", 422)
                    except (OSError, ErrorPerfilamiento) as error:
                        if isinstance(error, ErrorPerfilamiento) and error.status == 403:
                            raise
                        codigo = (
                            error.codigo
                            if isinstance(error, ErrorPerfilamiento)
                            and error.codigo
                            in {
                                "dependencia_timeout",
                                "dependencia_saturada",
                                "presupuesto_agotado",
                                "respuesta_fuente_invalida",
                                "perfil_vencido",
                            }
                            else "fuente_no_disponible"
                        )
                        if codigo != "fuente_no_disponible" or (
                            presupuesto.restante < 2 * self._politica.objetivo_segundos
                        ):
                            break
                    else:
                        breaker.registrar(True)
                        estado["estado"] = "disponible"
                        return fuente
            except ErrorPerfilamiento:
                breaker.registrar(False)
                raise
            breaker.registrar(False)
        estado["motivo"] = codigo
        verificar()
        cache = self._repositorio.buscar_fuente(consentimiento, adaptador.nombre)
        if cache is not None and cache.consultada_en <= self._reloj() < min(
            cache.vigente_hasta, cache.consultada_en + timedelta(minutes=15)
        ):
            estado["estado"] = "cache"
            return cache
        return None

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
