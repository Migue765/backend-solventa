from datetime import datetime, timedelta

from cotizacion.application.ports import ProveedorPerfil, RepositorioReglas, Tarificador
from cotizacion.domain.cotizacion import Cotizacion
from cotizacion.domain.decision import construir_instantanea
from cotizacion.domain.errors import ReglaDeNegocioError


class CalculadoraCotizacion:
    """Aplica la regla de tarificación vigente y el perfil de riesgo, y registra la decisión."""

    def __init__(
        self,
        reglas: RepositorioReglas,
        perfiles: ProveedorPerfil,
        tarificador: Tarificador,
        vigencia: timedelta,
    ) -> None:
        self._reglas = reglas
        self._perfiles = perfiles
        self._tarificador = tarificador
        self._vigencia = vigencia

    def calcular(self, cotizacion: Cotizacion, perfil_id: str, ahora: datetime) -> None:
        regla = self._reglas.obtener_vigente(cotizacion.producto_id, ahora)
        if regla is None:
            raise ReglaDeNegocioError("No hay reglas de tarificación vigentes para el producto.")
        perfil = self._perfiles.obtener(perfil_id, cotizacion.cliente_id)
        if perfil is None:
            raise ReglaDeNegocioError("No existe el perfil de riesgo indicado.")

        primas = {
            c.cobertura_id: self._tarificador.calcular_prima(regla, perfil, c)
            for c in cotizacion.coberturas
        }
        cotizacion.calcular(primas, ahora, self._vigencia)
        cotizacion.registrar_decision(construir_instantanea(cotizacion, regla, perfil, ahora))
