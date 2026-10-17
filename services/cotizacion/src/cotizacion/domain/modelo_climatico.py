from datetime import date
from decimal import Decimal

from cotizacion.domain.errors import ReglaDeNegocioError

VERSION_CLIMATICA = "clima-v1"


def duracion_climatica(datos: dict[str, str]) -> int:
    return (date.fromisoformat(datos["endDate"]) - date.fromisoformat(datos["startDate"])).days + 1


def planes_climaticos(
    datos: dict[str, str], factor_riesgo: Decimal = Decimal("1")
) -> list[dict[str, str]]:
    # Trabajo en proceso ... :) Se conserva la fórmula ilustrativa previa, no actuarial.
    evento = datos["weatherEvent"]
    if evento not in {"heavy-rain", "drought"}:
        raise ReglaDeNegocioError("El evento climático no admite ofertas.")
    nombre = "Lluvia Intensa" if evento == "heavy-rain" else "Sequía"
    monto = Decimal(datos["insuredAmount"])
    tasa_diaria = Decimal("0.0001") if evento == "heavy-rain" else Decimal("0.00012")
    return [
        {
            "codigo": codigo,
            "nombre": f"Clima {nombre} {nivel}",
            "factor": str(factor),
            "prima": str(
                max(Decimal("1000"), monto * tasa_diaria * duracion_climatica(datos) * factor)
                * factor_riesgo
            ),
        }
        for codigo, nivel, factor in (
            ("integral", "Integral", Decimal("1")),
            ("plus", "Plus", Decimal("0.85")),
            ("esencial", "Esencial", Decimal("0.70")),
        )
    ]
