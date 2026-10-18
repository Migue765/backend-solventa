from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from cotizacion.domain.datos_producto import validar_datos_producto
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.productos import ProductoSeguro

VERSION_ACADEMICA = "academico-v1"
FACTORES_PLAN = (
    ("esencial", "Esencial", Decimal("1.00")),
    ("integral", "Integral", Decimal("1.15")),
    ("premium", "Premium", Decimal("1.30")),
)
MAXIMO_MONTO = Decimal("9999999999999999.99")


def entradas_academicas(
    producto: ProductoSeguro, datos: dict[str, str]
) -> tuple[Decimal, int, str]:
    try:
        datos = validar_datos_producto(producto.producto_id, datos)
    except ValueError as error:
        raise ReglaDeNegocioError(str(error)) from error
    base = producto.suma_asegurada
    duracion = 1
    periodicidad = "mensual"
    if producto.producto_id == "device-protection":
        base = Decimal(datos["purchaseValue"])
    elif producto.producto_id == "payment-protection":
        base = Decimal(datos["monthlyPayment"]) * 6
    elif producto.producto_id == "mortgage-life":
        base = Decimal(datos["mortgageBalance"])
    elif producto.producto_id == "travel":
        duracion = (
            date.fromisoformat(datos["returnDate"]) - date.fromisoformat(datos["departureDate"])
        ).days + 1
        periodicidad = "por_viaje"
    elif producto.producto_id == "parametric":
        periodicidad = "por_vuelo"
    if not base.is_finite() or not 0 < base <= MAXIMO_MONTO:
        raise ReglaDeNegocioError("La base asegurada está fuera del rango monetario admitido.")
    return base, duracion, periodicidad


def prima_academica(
    base: Decimal,
    tasa: Decimal,
    score: Decimal,
    factor_perfil: Decimal,
    factor_plan: Decimal = Decimal("1"),
    duracion: int = 1,
) -> Decimal:
    if not score.is_finite() or not Decimal("0") <= score <= Decimal("1"):
        raise ReglaDeNegocioError("El score del perfil debe estar entre cero y uno.")
    if not tasa.is_finite() or tasa <= 0 or factor_perfil != Decimal("0.20") or duracion < 1:
        raise ReglaDeNegocioError("La regla académica no tiene parámetros válidos.")
    prima = (base * tasa * (1 + factor_perfil * score) * factor_plan * duracion).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if not 0 < prima <= MAXIMO_MONTO:
        raise ReglaDeNegocioError("La prima calculada está fuera del rango monetario admitido.")
    return prima
