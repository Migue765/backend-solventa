from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from polizas.domain.condiciones import MONEDA, PRODUCTOS, Condiciones, detalle_coberturas, prima
from polizas.domain.renovacion import huella
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

REGLA_CANCELACION = "academica-cancelacion-v1"

MotivoNoCancelable = Literal[
    "producto_no_cancelable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
    "renovacion_en_curso",
]
MotivoCancelacion = Literal[
    "ya_no_la_necesito",
    "costo",
    "cambio_de_aseguradora",
    "insatisfaccion",
    "otro",
]


@dataclass(frozen=True)
class PeriodoPagado:
    inicio: date
    fin: date
    prima: int


class ConfirmacionCancelacion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirma: StrictBool
    motivo_cancelacion: MotivoCancelacion = Field(alias="motivoCancelacion")
    liquidacion_hash: str = Field(alias="liquidacionHash", pattern=r"^[0-9a-f]{64}$")

    @field_validator("confirma")
    @classmethod
    def exigir_confirmacion(cls, valor: bool) -> bool:
        if valor is not True:
            raise ValueError("La cancelacion requiere confirmacion explicita.")
        return valor


def motivo_no_cancelable(
    tipo: str,
    inicio: date,
    fin: date,
    hoy: date,
    *,
    cancelada: bool,
    renovacion_pendiente: bool,
) -> MotivoNoCancelable | None:
    """Solo los productos mensuales con prima conocida se cancelan y liquidan."""
    if cancelada:
        return "poliza_cancelada"
    if tipo not in PRODUCTOS:
        return "producto_no_cancelable"
    if hoy < inicio:
        return "poliza_programada"
    if hoy > fin:
        return "poliza_vencida"
    if hoy == fin:
        return "fin_de_vigencia"
    if renovacion_pendiente:
        return "renovacion_en_curso"
    return None


def liquidacion(
    tipo: str,
    vigentes: Condiciones,
    periodo_actual: tuple[date, date],
    futuros: list[PeriodoPagado],
    hoy: date,
) -> dict[str, object]:
    """La cobertura termina al confirmar: el dia de la cancelacion se considera usado.

    Se devuelve la prima vigente de los dias no usados del periodo en curso y la prima
    completa de los periodos renovados que aun no inician. Los periodos se pagan por
    anticipado, asi que no quedan valores pendientes por cobrar.
    """
    inicio, fin = periodo_actual
    prima_mensual = prima(tipo, vigentes)
    dias_periodo = (fin - inicio).days + 1
    dias_no_usados = (fin - hoy).days
    proporcional = int(
        (Decimal(prima_mensual) * dias_no_usados / dias_periodo).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    anulados = sum(periodo.prima for periodo in futuros)
    contenido: dict[str, object] = {
        "fechaEfectiva": hoy.isoformat(),
        "finCobertura": hoy.isoformat(),
        "coberturasFinalizadas": [
            {"codigo": cobertura["codigo"], "nombre": cobertura["nombre"]}
            for cobertura in detalle_coberturas(tipo, vigentes)
            if cobertura["incluida"]
        ],
        "periodoActual": {"inicio": inicio.isoformat(), "fin": fin.isoformat()},
        "periodosAnulados": [
            {
                "inicio": periodo.inicio.isoformat(),
                "fin": periodo.fin.isoformat(),
                "prima": _dinero(periodo.prima),
            }
            for periodo in futuros
        ],
        "primaMensual": _dinero(prima_mensual),
        "diasPeriodo": dias_periodo,
        "diasNoUsados": dias_no_usados,
        "devolucionProporcional": _dinero(proporcional),
        "devolucionPeriodosAnulados": _dinero(anulados),
        "devolucion": _dinero(proporcional + anulados),
        "valorPendiente": _dinero(0),
        "reglaVersion": REGLA_CANCELACION,
    }
    return {**contenido, "liquidacionHash": huella(contenido)}


def _dinero(monto: int) -> dict[str, str]:
    return {"monto": str(monto), "moneda": MONEDA}
