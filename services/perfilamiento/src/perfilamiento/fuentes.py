from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from perfilamiento.domain import Consentimiento, Degradacion, ErrorPerfilamiento, Fuente
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import FuentePerfilamiento
from perfilamiento.resilience import CircuitBreaker, PoliticaConsultas, Presupuesto


class ConsultarFuentes:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        fuentes: tuple[FuentePerfilamiento, ...],
        reloj: Callable[[], datetime],
        politica: PoliticaConsultas,
    ) -> None:
        self._repositorio = repositorio
        self._reloj = reloj
        self._politica = politica
        nombres = [fuente.nombre for fuente in fuentes]
        if len(nombres) != len(set(nombres)):
            raise ValueError("No se permiten adaptadores duplicados.")
        self._breakers = {nombre: CircuitBreaker(politica) for nombre in nombres}

    def consultar(
        self,
        adaptador: FuentePerfilamiento,
        consentimiento: Consentimiento,
        autorizar: Callable[[], Consentimiento],
        presupuesto: Presupuesto,
        estado: dict[str, object],
        degradaciones: list[Degradacion],
        *,
        permitir_default: bool = False,
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
                if error.status == 403:
                    raise
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
            degradaciones.append(
                Degradacion(
                    fuente=adaptador.nombre,
                    motivo=(
                        "fuente_vencida" if codigo == "perfil_vencido" else "fuente_no_disponible"
                    ),
                    politica="cache",
                    version_politica="degradacion-1",
                )
            )
            return cache.model_copy(
                update={
                    "version": cache.version + ":cache",
                    "vigente_hasta": min(cache.vigente_hasta, self._reloj() + timedelta(minutes=5)),
                }
            )
        if (
            permitir_default
            and codigo
            in {
                "fuente_no_disponible",
                "dependencia_timeout",
                "dependencia_saturada",
                "presupuesto_agotado",
                "circuito_abierto",
                "perfil_vencido",
            }
            and adaptador.nombre in {"open_finance", "open_data"}
        ):
            nombre: Literal["open_finance", "open_data"] = (
                "open_finance" if adaptador.nombre == "open_finance" else "open_data"
            )
            fuente, politica = self._degradar(nombre, consentimiento)
            estado["estado"] = politica
            degradaciones.append(
                Degradacion(
                    fuente=adaptador.nombre,
                    motivo=(
                        "fuente_vencida" if codigo == "perfil_vencido" else "fuente_no_disponible"
                    ),
                    politica=politica,
                    version_politica="degradacion-1",
                )
            )
            return fuente
        return None

    def _degradar(
        self,
        nombre: Literal["open_finance", "open_data"],
        consentimiento: Consentimiento,
    ) -> tuple[Fuente, Literal["cache", "default"]]:
        ahora = self._reloj()
        cache = self._repositorio.fuente_en_cache(
            consentimiento.cliente_id,
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
        valores_default = {"open_finance": Decimal("0.50"), "open_data": Decimal("0.25")}
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
