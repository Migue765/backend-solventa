from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MONEDA = "COP"

# Factores de plan del modelo academico de Cotizacion (esencial, integral, premium).
PLANES: dict[str, tuple[str, Decimal]] = {
    "esencial": ("Esencial", Decimal("1.00")),
    "integral": ("Integral", Decimal("1.15")),
    "premium": ("Premium", Decimal("1.30")),
}


@dataclass(frozen=True)
class Cobertura:
    codigo: str
    nombre: str
    descripcion: str
    recargo: int | None = None  # None: obligatoria; entero: opcional con recargo mensual.


@dataclass(frozen=True)
class Producto:
    nombre: str
    prima_base: int
    coberturas: tuple[Cobertura, ...]
    condiciones: tuple[str, ...]


@dataclass(frozen=True)
class Condiciones:
    plan: str
    coberturas: frozenset[str]


# Tarifa academica fija: Polizas no conserva el perfil de riesgo de la emision, asi que no
# recalcula la prima con Perfilamiento ni con Cotizacion. Con plan esencial y todas las
# coberturas la prima coincide con la tarifa de renovacion publicada en SOL-317.
PRODUCTOS: dict[str, Producto] = {
    "automotive": Producto(
        "Seguro Automotriz Total",
        77000,
        (
            Cobertura(
                "danos_vehiculo",
                "Daños al vehículo",
                "Daños accidentales sufridos por el vehículo asegurado.",
            ),
            Cobertura(
                "responsabilidad_civil",
                "Responsabilidad civil",
                "Daños causados a terceros durante la conducción.",
            ),
            Cobertura(
                "asistencia_carretera",
                "Asistencia en carretera",
                "Acompañamiento ante inmovilización o emergencia.",
                8000,
            ),
        ),
        (
            "El vehículo debe seguir registrado y cumplir las condiciones de asegurabilidad.",
            "El deducible se aplica por cada evento cubierto.",
        ),
    ),
    "life": Producto(
        "Microseguro de Vida",
        19000,
        (
            Cobertura(
                "fallecimiento", "Fallecimiento", "Respaldo económico para los beneficiarios."
            ),
            Cobertura(
                "incapacidad_total",
                "Incapacidad total y permanente",
                "Protección cuando un evento impide trabajar.",
                6000,
            ),
        ),
        (
            "La edad del asegurado debe seguir dentro del rango aceptado.",
            "La información de salud declarada debe seguir siendo verificable.",
        ),
    ),
    "device-protection": Producto(
        "Protección de Dispositivos",
        14000,
        (
            Cobertura(
                "dano_accidental",
                "Daño accidental",
                "Reparación o reposición por daños accidentales cubiertos.",
            ),
            Cobertura(
                "hurto_calificado",
                "Hurto calificado",
                "Respaldo por pérdida del dispositivo según lo contratado.",
            ),
            Cobertura(
                "falla_funcional",
                "Falla funcional",
                "Atención de fallas cubiertas posteriores a la garantía.",
                4000,
            ),
        ),
        (
            "El dispositivo debe seguir identificado y en buen estado.",
            "El valor declarado se mantiene igual al de la emisión.",
        ),
    ),
    "payment-protection": Producto(
        "Protección de Pagos",
        12000,
        (
            Cobertura(
                "desempleo_involuntario",
                "Desempleo involuntario",
                "Pago temporal de obligaciones ante desempleo cubierto.",
            ),
            Cobertura(
                "incapacidad_temporal",
                "Incapacidad temporal",
                "Respaldo de cuotas durante una incapacidad cubierta.",
                3000,
            ),
        ),
        (
            "La obligación financiera debe seguir vigente y al día.",
            "La cobertura sigue sujeta al tipo de crédito y al valor de la cuota.",
        ),
    ),
    "mortgage-life": Producto(
        "Seguro de Vida Hipotecario",
        100000,
        (
            Cobertura(
                "fallecimiento",
                "Fallecimiento",
                "Pago del saldo asegurado del crédito hipotecario.",
            ),
            Cobertura(
                "incapacidad_total",
                "Incapacidad total y permanente",
                "Protección del saldo ante incapacidad cubierta.",
                20000,
            ),
        ),
        (
            "La suma asegurada se ajusta al saldo vigente del crédito.",
            "La continuidad depende de la vigencia de la obligación hipotecaria.",
        ),
    ),
}


def iniciales(tipo: str) -> Condiciones:
    """Condiciones de emision: plan esencial con todas las coberturas del producto."""
    return Condiciones("esencial", frozenset(c.codigo for c in PRODUCTOS[tipo].coberturas))


def opcionales(tipo: str) -> tuple[Cobertura, ...]:
    return tuple(c for c in PRODUCTOS[tipo].coberturas if c.recargo is not None)


def prima(tipo: str, condiciones: Condiciones) -> int:
    producto = PRODUCTOS[tipo]
    recargos = sum(
        c.recargo or 0 for c in producto.coberturas if c.codigo in condiciones.coberturas
    )
    total = (producto.prima_base + recargos) * PLANES[condiciones.plan][1]
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def detalle_coberturas(tipo: str, condiciones: Condiciones) -> list[dict[str, object]]:
    return [
        {
            "codigo": c.codigo,
            "nombre": c.nombre,
            "descripcion": c.descripcion,
            "opcional": c.recargo is not None,
            "incluida": c.codigo in condiciones.coberturas,
        }
        for c in PRODUCTOS[tipo].coberturas
    ]
