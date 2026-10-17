from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from cotizacion.application.ports import ProveedorPerfil, RepositorioReglas, Tarificador
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.decision import construir_instantanea
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.modelo_academico import (
    FACTORES_PLAN,
    VERSION_ACADEMICA,
    entradas_academicas,
    prima_academica,
)
from cotizacion.domain.productos import ProductoSeguro


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

    def calcular(
        self,
        cotizacion: Cotizacion,
        perfil_id: str,
        ahora: datetime,
        producto: ProductoSeguro | None = None,
        datos_producto: dict[str, str] | None = None,
    ) -> None:
        regla = self._reglas.obtener_vigente(cotizacion.producto_id, ahora)
        if regla is None:
            raise ReglaDeNegocioError("No hay reglas de tarificación vigentes para el producto.")
        perfil = self._perfiles.obtener(perfil_id, cotizacion.cliente_id)
        if perfil is None:
            raise ReglaDeNegocioError("No existe el perfil de riesgo indicado.")

        detalle: dict[str, Any] | None = None
        if producto is not None:
            if regla.version != VERSION_ACADEMICA or datos_producto is None:
                raise ReglaDeNegocioError("No hay un modelo académico vigente para la solicitud.")
            if cotizacion.moneda != "COP" or len(cotizacion.coberturas) != 1:
                raise ReglaDeNegocioError("El modelo académico requiere una cobertura en COP.")
            base, duracion, periodicidad = entradas_academicas(producto, datos_producto)
            original = cotizacion.coberturas[0]
            cobertura = CoberturaCotizada(
                cobertura_id=original.cobertura_id,
                suma_asegurada=Dinero(base, "COP"),
                deducible=original.deducible,
                deducible_descuenta_base=False,
            )
            planes = [
                {
                    "codigo": codigo,
                    "nombre": f"{producto.nombre} {nombre}",
                    "factor": str(factor),
                    "prima": str(
                        prima_academica(
                            base, regla.tasa, perfil.score, regla.factor_perfil, factor, duracion
                        )
                    ),
                }
                for codigo, nombre, factor in FACTORES_PLAN
            ]
            detalle = {
                "modelo": VERSION_ACADEMICA,
                "productoId": producto.producto_id,
                "datosProducto": dict(datos_producto),
                "baseAsegurada": str(base),
                "duracion": duracion,
                "periodicidad": periodicidad,
                "redondeo": "COP entero; ROUND_HALF_UP",
                "deducibleDescuentaBase": False,
                "planes": planes,
            }
            cotizacion.coberturas = [cobertura]
            primas = {cobertura.cobertura_id: Dinero(Decimal(planes[0]["prima"]), "COP")}
        else:
            primas = {
                c.cobertura_id: self._tarificador.calcular_prima(regla, perfil, c)
                for c in cotizacion.coberturas
            }
        cotizacion.calcular(primas, ahora, self._vigencia)
        cotizacion.registrar_decision(
            construir_instantanea(cotizacion, regla, perfil, ahora, detalle)
        )
