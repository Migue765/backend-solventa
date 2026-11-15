import hashlib
import json
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from polizas.domain.condiciones import (
    MONEDA,
    PRODUCTOS,
    Condiciones,
    detalle_coberturas,
    prima,
)
from polizas.domain.emision import mes_calendario_despues
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

REGLA_RENOVACION = "academica-renovacion-v1"
VENTANA_DIAS = 15

MotivoNoRenovable = Literal[
    "producto_no_renovable",
    "poliza_programada",
    "poliza_vencida",
    "fuera_de_ventana",
]


def motivo_no_renovable(tipo: str, inicio: date, fin: date, hoy: date) -> MotivoNoRenovable | None:
    if tipo not in PRODUCTOS:
        return "producto_no_renovable"
    if hoy < inicio:
        return "poliza_programada"
    if hoy > fin:
        return "poliza_vencida"
    if (fin - hoy).days > VENTANA_DIAS:
        return "fuera_de_ventana"
    return None


def ventana(fin: date) -> tuple[date, date]:
    return fin - timedelta(days=VENTANA_DIAS), fin


def oferta(
    poliza_id: UUID, tipo: str, fin_actual: date, vigentes: Condiciones
) -> dict[str, object]:
    """La renovacion conserva el plan y las coberturas de la version vigente (SOL-319)."""
    producto = PRODUCTOS[tipo]
    inicio = fin_actual + timedelta(days=1)
    condiciones: dict[str, object] = {
        "polizaId": str(poliza_id),
        "producto": producto.nombre,
        "finAnterior": fin_actual.isoformat(),
        "nuevaVigencia": {
            "inicio": inicio.isoformat(),
            "fin": mes_calendario_despues(inicio).isoformat(),
        },
        "coberturas": [
            {"nombre": cobertura["nombre"], "descripcion": cobertura["descripcion"]}
            for cobertura in detalle_coberturas(tipo, vigentes)
            if cobertura["incluida"]
        ],
        "prima": {
            "monto": str(prima(tipo, vigentes)),
            "moneda": MONEDA,
            "periodicidad": "mensual",
        },
        "condiciones": list(producto.condiciones),
        "requierePago": True,
        "reglaVersion": REGLA_RENOVACION,
    }
    return {**condiciones, "condicionesHash": huella(condiciones)}


def huella(contenido: object) -> str:
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class SolicitudRenovacion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acepta_condiciones: StrictBool = Field(alias="aceptaCondiciones")
    condiciones_hash: str = Field(alias="condicionesHash", pattern=r"^[0-9a-f]{64}$")

    @field_validator("acepta_condiciones")
    @classmethod
    def exigir_aceptacion(cls, valor: bool) -> bool:
        if valor is not True:
            raise ValueError("La renovacion requiere aceptacion explicita.")
        return valor
