from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict

from cotizacion.application.ports import ProveedorPerfil, RepositorioReglas, Tarificador
from cotizacion.domain.beneficios_plan import PresentacionPlan, beneficios_plan
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.datos_producto import validar_datos_producto
from cotizacion.domain.decision import construir_instantanea
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.modelo_academico import (
    FACTORES_PLAN,
    VERSION_ACADEMICA,
    entradas_academicas,
    prima_academica,
)
from cotizacion.domain.modelo_climatico import (
    VERSION_CLIMATICA,
    duracion_climatica,
    planes_climaticos,
)
from cotizacion.domain.perfil_riesgo import PerfilRiesgo as PerfilRiesgoOferta
from cotizacion.domain.productos import ProductoSeguro


class _PlanAcademico(TypedDict):
    codigo: str
    nombre: str
    factor: str
    prima: str
    presentacion: PresentacionPlan


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
        *,
        perfil_ofertas: PerfilRiesgoOferta | None = None,
    ) -> None:
        regla = self._reglas.obtener_vigente(cotizacion.producto_id, ahora)
        if regla is None:
            raise ReglaDeNegocioError("No hay reglas de tarificación vigentes para el producto.")
        perfil = self._perfiles.obtener(perfil_id, cotizacion.cliente_id)
        if perfil is None:
            raise ReglaDeNegocioError("No existe el perfil de riesgo indicado.")

        detalle: dict[str, Any] | None = None
        if producto is not None and producto.producto_id == "parametric-weather":
            if regla.version != "tarifa-2026.1" or datos_producto is None or perfil_ofertas is None:
                raise ReglaDeNegocioError("No hay un modelo climático vigente para la solicitud.")
            try:
                datos_clima = validar_datos_producto(producto.producto_id, datos_producto)
            except ValueError as error:
                raise ReglaDeNegocioError(str(error)) from error
            detalle = {
                "modelo": VERSION_CLIMATICA,
                "productoId": producto.producto_id,
                "datosProducto": datos_clima,
                "baseAsegurada": datos_clima["insuredAmount"],
                "duracion": duracion_climatica(datos_clima),
                "periodicidad": "por_periodo",
                "redondeo": "COP centavos; ROUND_HALF_UP",
                "deducibleDescuentaBase": True,
                "factorRiesgoOfertas": str(perfil_ofertas.factor_prima),
                "tasaAplicada": perfil_ofertas.tarifa_aplicada,
                "planes": [
                    {
                        **plan,
                        "prima": str(Dinero(Decimal(plan["prima"]), "COP").monto),
                        "presentacion": beneficios_plan(
                            producto,
                            datos_clima,
                            plan["codigo"],
                            Decimal(datos_clima["insuredAmount"]),
                        ),
                    }
                    for plan in planes_climaticos(datos_clima, perfil_ofertas.factor_prima)
                ],
            }
            primas = {
                c.cobertura_id: self._tarificador.calcular_prima(regla, perfil, c)
                for c in cotizacion.coberturas
            }
        elif producto is not None:
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
            planes: list[_PlanAcademico] = [
                {
                    "codigo": codigo,
                    "nombre": f"{producto.nombre} {nombre}",
                    "factor": str(factor),
                    "presentacion": beneficios_plan(producto, datos_producto, codigo, base),
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
