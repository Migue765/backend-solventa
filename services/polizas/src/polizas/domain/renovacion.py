import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from polizas.domain.emision import mes_calendario_despues
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

REGLA_RENOVACION = "academica-renovacion-v1"
VENTANA_DIAS = 15
MONEDA = "COP"

MotivoNoRenovable = Literal[
    "producto_no_renovable",
    "poliza_programada",
    "poliza_vencida",
    "fuera_de_ventana",
]


@dataclass(frozen=True)
class ProductoRenovable:
    nombre: str
    prima: int
    coberturas: tuple[tuple[str, str], ...]
    condiciones: tuple[str, ...]


# Tarifa academica fija por producto: Polizas no conserva el perfil de riesgo de la emision,
# asi que la prima de renovacion no se recalcula con Perfilamiento ni con Cotizacion.
RENOVABLES: dict[str, ProductoRenovable] = {
    "automotive": ProductoRenovable(
        "Seguro Automotriz Total",
        85000,
        (
            ("Daños al vehículo", "Daños accidentales sufridos por el vehículo asegurado."),
            ("Responsabilidad civil", "Daños causados a terceros durante la conducción."),
            ("Asistencia en carretera", "Acompañamiento ante inmovilización o emergencia."),
        ),
        (
            "El vehículo debe seguir registrado y cumplir las condiciones de asegurabilidad.",
            "El deducible se aplica por cada evento cubierto.",
        ),
    ),
    "life": ProductoRenovable(
        "Microseguro de Vida",
        25000,
        (
            ("Fallecimiento", "Respaldo económico para los beneficiarios."),
            ("Incapacidad total y permanente", "Protección cuando un evento impide trabajar."),
        ),
        (
            "La edad del asegurado debe seguir dentro del rango aceptado.",
            "La información de salud declarada debe seguir siendo verificable.",
        ),
    ),
    "device-protection": ProductoRenovable(
        "Protección de Dispositivos",
        18000,
        (
            ("Daño accidental", "Reparación o reposición por daños accidentales cubiertos."),
            ("Hurto calificado", "Respaldo por pérdida del dispositivo según lo contratado."),
            ("Falla funcional", "Atención de fallas cubiertas posteriores a la garantía."),
        ),
        (
            "El dispositivo debe seguir identificado y en buen estado.",
            "El valor declarado se mantiene igual al de la emisión.",
        ),
    ),
    "payment-protection": ProductoRenovable(
        "Protección de Pagos",
        15000,
        (
            ("Desempleo involuntario", "Pago temporal de obligaciones ante desempleo cubierto."),
            ("Incapacidad temporal", "Respaldo de cuotas durante una incapacidad cubierta."),
        ),
        (
            "La obligación financiera debe seguir vigente y al día.",
            "La cobertura sigue sujeta al tipo de crédito y al valor de la cuota.",
        ),
    ),
    "mortgage-life": ProductoRenovable(
        "Seguro de Vida Hipotecario",
        120000,
        (
            ("Fallecimiento", "Pago del saldo asegurado del crédito hipotecario."),
            ("Incapacidad total y permanente", "Protección del saldo ante incapacidad cubierta."),
        ),
        (
            "La suma asegurada se ajusta al saldo vigente del crédito.",
            "La continuidad depende de la vigencia de la obligación hipotecaria.",
        ),
    ),
}


def motivo_no_renovable(tipo: str, inicio: date, fin: date, hoy: date) -> MotivoNoRenovable | None:
    if tipo not in RENOVABLES:
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


def oferta(poliza_id: UUID, tipo: str, fin_actual: date) -> dict[str, object]:
    producto = RENOVABLES[tipo]
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
            {"nombre": nombre, "descripcion": descripcion}
            for nombre, descripcion in producto.coberturas
        ],
        "prima": {"monto": str(producto.prima), "moneda": MONEDA, "periodicidad": "mensual"},
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
