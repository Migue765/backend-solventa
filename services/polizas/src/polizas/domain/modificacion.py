from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from polizas.domain.condiciones import (
    MONEDA,
    PLANES,
    PRODUCTOS,
    Condiciones,
    detalle_coberturas,
    opcionales,
    prima,
)
from polizas.domain.renovacion import huella
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

REGLA_MODIFICACION = "academica-modificacion-v1"

MotivoNoModificable = Literal[
    "producto_no_modificable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
]
Plan = Literal["esencial", "integral", "premium"]


class CambioNoPermitido(Exception):
    def __init__(self, campo: str) -> None:
        super().__init__(campo)
        self.campo = campo


class SinCambios(Exception):
    pass


class SolicitudModificacion(BaseModel):
    """Unicos campos modificables: plan y coberturas opcionales del producto."""

    model_config = ConfigDict(extra="forbid")
    version_base: int = Field(alias="versionBase", ge=1, strict=True)
    plan: Plan
    coberturas_opcionales: list[str] = Field(alias="coberturasOpcionales", max_length=10)

    @field_validator("coberturas_opcionales")
    @classmethod
    def sin_duplicados(cls, valor: list[str]) -> list[str]:
        if len(set(valor)) != len(valor):
            raise ValueError("Las coberturas no pueden repetirse.")
        return valor


class ConfirmacionModificacion(SolicitudModificacion):
    confirma: StrictBool
    impacto_hash: str = Field(alias="impactoHash", pattern=r"^[0-9a-f]{64}$")

    @field_validator("confirma")
    @classmethod
    def exigir_confirmacion(cls, valor: bool) -> bool:
        if valor is not True:
            raise ValueError("La modificacion requiere confirmacion explicita.")
        return valor


def motivo_no_modificable(
    tipo: str, inicio: date, fin: date, hoy: date, *, cancelada: bool = False
) -> MotivoNoModificable | None:
    if tipo not in PRODUCTOS:
        return "producto_no_modificable"
    if cancelada:
        return "poliza_cancelada"
    if hoy < inicio:
        return "poliza_programada"
    if hoy > fin:
        return "poliza_vencida"
    if hoy == fin:
        return "fin_de_vigencia"
    return None


def propuesta(tipo: str, solicitud: SolicitudModificacion) -> Condiciones:
    permitidas = {c.codigo for c in opcionales(tipo)}
    for codigo in solicitud.coberturas_opcionales:
        if codigo not in permitidas:
            raise CambioNoPermitido(f"coberturasOpcionales.{codigo}")
    obligatorias = {c.codigo for c in PRODUCTOS[tipo].coberturas if c.recargo is None}
    return Condiciones(
        solicitud.plan, frozenset(obligatorias | set(solicitud.coberturas_opcionales))
    )


def opciones(tipo: str) -> dict[str, object]:
    return {
        "planes": [
            {"codigo": codigo, "nombre": nombre, "factor": str(factor)}
            for codigo, (nombre, factor) in PLANES.items()
        ],
        "coberturasOpcionales": [
            {
                "codigo": c.codigo,
                "nombre": c.nombre,
                "recargo": {"monto": str(c.recargo), "moneda": MONEDA},
            }
            for c in opcionales(tipo)
        ],
    }


def impacto(
    tipo: str,
    version_base: int,
    actuales: Condiciones,
    nuevas: Condiciones,
    vigencia: tuple[date, date],
    hoy: date,
) -> dict[str, object]:
    if nuevas.plan == actuales.plan and nuevas.coberturas == actuales.coberturas:
        raise SinCambios()
    inicio, fin = vigencia
    efectivo = hoy + timedelta(days=1)
    prima_anterior, prima_nueva = prima(tipo, actuales), prima(tipo, nuevas)
    diferencia = prima_nueva - prima_anterior
    dias_restantes = (fin - efectivo).days + 1
    dias_periodo = (fin - inicio).days + 1
    ajuste = (Decimal(diferencia) * dias_restantes / dias_periodo).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    contenido: dict[str, object] = {
        "versionBase": version_base,
        "plan": {"anterior": actuales.plan, "nuevo": nuevas.plan},
        "coberturas": detalle_coberturas(tipo, nuevas),
        "coberturasAgregadas": sorted(nuevas.coberturas - actuales.coberturas),
        "coberturasRetiradas": sorted(actuales.coberturas - nuevas.coberturas),
        "primaAnterior": _dinero(prima_anterior),
        "primaNueva": _dinero(prima_nueva),
        "diferenciaMensual": _dinero(diferencia),
        "ajusteProrrateado": _dinero(int(ajuste)),
        "diasRestantes": dias_restantes,
        "efectivoDesde": efectivo.isoformat(),
        "vigencia": {"inicio": inicio.isoformat(), "fin": fin.isoformat(), "cambia": False},
        "reglaVersion": REGLA_MODIFICACION,
    }
    return {**contenido, "impactoHash": huella(contenido)}


def _dinero(monto: int) -> dict[str, str]:
    return {"monto": str(monto), "moneda": MONEDA}
