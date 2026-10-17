from decimal import Decimal
from typing import NotRequired, TypedDict

from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.modelo_climatico import duracion_climatica
from cotizacion.domain.productos import CoberturaProducto, ProductoSeguro

VERSION_BENEFICIOS = "beneficios-v1"
NIVELES = ("esencial", "integral", "premium")


class PresentacionPlan(TypedDict):
    version: str
    descripcion: str
    coberturaPrincipal: str
    coberturas: list[str]
    condiciones: list[str]
    beneficiosAdicionales: list[str]
    responsabilidadCivil: str
    deducible: NotRequired[str]
    vigencia: NotRequired[str]


def beneficios_plan(
    producto: ProductoSeguro, datos: dict[str, str], codigo: str, base: Decimal
) -> PresentacionPlan:
    clima = producto.producto_id == "parametric-weather"
    niveles = ("esencial", "plus", "integral") if clima else NIVELES
    if codigo not in niveles:
        raise ReglaDeNegocioError("El nivel no admite beneficios.")
    nivel = niveles.index(codigo)
    coberturas = producto.coberturas
    condiciones = producto.condiciones
    minimo_coberturas = 1
    if clima:
        evento = datos["weatherEvent"]
        if evento not in {"heavy-rain", "drought"}:
            raise ReglaDeNegocioError("El evento climático no admite ofertas.")
        coberturas = (coberturas[0 if evento == "heavy-rain" else 1],)
        condiciones = (
            f"Municipio y departamento declarados: {datos['location']}.",
            f"Monto asegurado declarado: {datos['insuredAmount']} COP.",
            "Disparador, umbral y compensación sujetos a las condiciones del plan.",
        )
    if producto.producto_id == "parametric":
        modalidad = datos.get("coverageType", "delay-cancellation")
        if modalidad not in {"delay-cancellation", "missed-connection", "comprehensive"}:
            raise ReglaDeNegocioError("La cobertura de vuelo no admite ofertas.")
        if modalidad != "delay-cancellation":
            conexion = CoberturaProducto(
                "Pérdida de conexión",
                "Pérdida de conexión por afectación del vuelo previo.",
            )
            if modalidad == "comprehensive":
                coberturas = (conexion, coberturas[1], coberturas[0])
                minimo_coberturas = 2
            else:
                coberturas = (conexion,)
            condiciones = (
                *condiciones,
                "Requiere conexión en un mismo itinerario por afectación de la aerolínea.",
                "No cubre llegar tarde al aeropuerto ni pérdidas por culpa del pasajero.",
            )
    incluidas = coberturas[: nivel + minimo_coberturas]
    nombres = ", ".join(c.nombre for c in incluidas)
    orientaciones = (
        "Orientación sobre las condiciones del plan",
        f"Orientación sobre {coberturas[0].nombre.lower()}",
        (
            f"Orientación sobre {coberturas[-1].nombre.lower()}"
            if len(coberturas) > 1
            else (
                "Orientación sobre el disparador y el umbral del plan"
                if clima
                else "Orientación sobre las exclusiones de conexión"
            )
        ),
    )
    base_formateada = f"${base:,.0f} COP".replace(",", ".")
    presentacion: PresentacionPlan = {
        "version": VERSION_BENEFICIOS,
        "descripcion": (f"{nombres}. Incluye {nivel + 1} temas de orientación sobre el plan."),
        "coberturaPrincipal": f"{nombres}. Base asegurada: {base_formateada}",
        "coberturas": [f"{c.nombre}: {c.descripcion}" for c in incluidas],
        "condiciones": [
            "El deducible es una condición y no descuenta la base de cálculo.",
            *condiciones,
        ],
        "beneficiosAdicionales": list(orientaciones[: nivel + 1]),
        "responsabilidadCivil": (
            "Incluida, sujeta a las condiciones del producto."
            if any(c.nombre == "Responsabilidad civil" for c in incluidas)
            else "No aplica"
        ),
    }
    if clima:
        evento_nombre = coberturas[0].nombre.lower()
        presentacion.update(
            {
                "descripcion": (
                    f"Protección ante {evento_nombre} en {datos['location']}. "
                    f"Incluye {nivel + 1} temas de orientación sobre el plan."
                ),
                "coberturaPrincipal": f"Hasta {base_formateada} ante {evento_nombre}",
                "condiciones": list(condiciones),
                "deducible": "Sin deducible",
                "vigencia": (
                    f"Del {datos['startDate']} al {datos['endDate']} "
                    f"({duracion_climatica(datos)} días inclusive)"
                ),
            }
        )
    return presentacion
