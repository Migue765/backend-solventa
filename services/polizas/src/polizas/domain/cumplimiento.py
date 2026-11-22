import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from polizas.domain.reporte import huella
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

FORMATO = "solventa-regulatorio-academico-v1"
ADAPTADOR = "solventa-reaseguro-academico-v1"
AVISO = "Simulacion academica; sin validez regulatoria ni conexion con reaseguradores."


class ErrorCumplimiento(Exception):
    def __init__(self, codigo: str, status: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status


class GuardarReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formatoVersion: Literal["solventa-regulatorio-academico-v1"]
    corte: AwareDatetime
    contenido: str = Field(min_length=2, max_length=8 * 1024 * 1024)
    contenidoHash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verificar(self) -> "GuardarReporte":
        datos = json.loads(self.contenido)
        if (
            not isinstance(datos, dict)
            or huella(datos) != self.contenidoHash
            or datos.get("formatoVersion") != self.formatoVersion
            or datos.get("simulado") is not True
            or datos.get("sinValidezRegulatoria") is not True
            or datos.get("corte") != self.corte.isoformat().replace("+00:00", "Z")
            or set(datos)
            != {
                "formatoVersion",
                "simulado",
                "sinValidezRegulatoria",
                "aviso",
                "corte",
                "filtros",
                "indicadores",
                "fuentes",
                "trazabilidad",
            }
        ):
            raise ValueError("El contenido no corresponde al reporte academico confirmado.")
        trazas = datos["trazabilidad"]
        campos = {
            "polizas": {"id", "tipo", "pagoId", "emitidaEn", "fechaInicio", "fechaFin", "estado"},
            "primas": {"pagoId", "producto", "monto", "moneda", "confirmadaEn"},
            "siniestros": {"id", "polizaId", "tipo", "estado", "reportadoEn"},
            "pagos": {"id", "siniestroId", "estado", "monto", "moneda", "creadoEn"},
        }
        if not isinstance(trazas, dict) or set(trazas) != set(campos):
            raise ValueError("Se requieren las cuatro fuentes de trazabilidad.")
        for fuente, permitidos in campos.items():
            if not isinstance(trazas[fuente], list):
                raise ValueError("La trazabilidad debe ser una lista.")
            for registro in trazas[fuente]:
                if (
                    not isinstance(registro, dict)
                    or set(registro) != {"versionHash", "datos"}
                    or not isinstance(registro["datos"], dict)
                    or set(registro["datos"]) != permitidos
                    or registro["versionHash"] != huella(registro["datos"])
                ):
                    raise ValueError("La trazabilidad no admite datos personales ni campos extra.")
        return self


class SolicitudIntercambio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporteId: UUID
    aceptaSimulacion: Literal[True]
    escenario: Literal["aceptado", "diferencias"] = "aceptado"

    @field_validator("aceptaSimulacion", mode="before")
    @classmethod
    def confirmar(cls, valor: object) -> object:
        if valor is not True:
            raise ValueError("Se requiere aceptar explicitamente la simulacion.")
        return valor


def intercambio_academico(
    reporte: dict[str, Any], intercambio_id: UUID, escenario: str
) -> dict[str, Any]:
    trazas = reporte["trazabilidad"]
    filtros = reporte["filtros"]
    polizas = {
        p["datos"]["pagoId"]: p["datos"]["id"]
        for p in trazas["polizas"]
        if filtros["producto"] in (None, p["datos"]["tipo"])
        and filtros["estado"] in (None, p["datos"]["estado"])
    }
    siniestros = {
        s["datos"]["id"] for s in trazas["siniestros"] if s["datos"]["polizaId"] in polizas.values()
    }

    def en_periodo(instante: str) -> bool:
        dia = datetime.fromisoformat(instante).date()
        return date.fromisoformat(filtros["desde"]) <= dia <= date.fromisoformat(filtros["hasta"])

    partidas = [
        {
            "id": prima["datos"]["pagoId"],
            "tipo": "cesion",
            "polizaId": polizas[prima["datos"]["pagoId"]],
            "moneda": prima["datos"]["moneda"],
            "monto": _cuota(prima["datos"]["monto"]),
        }
        for prima in trazas["primas"]
        if prima["datos"]["pagoId"] in polizas and en_periodo(prima["datos"]["confirmadaEn"])
    ]
    partidas += [
        {
            "id": pago["datos"]["id"],
            "tipo": "recuperacion",
            "siniestroId": pago["datos"]["siniestroId"],
            "moneda": pago["datos"]["moneda"],
            "monto": _cuota(pago["datos"]["monto"]),
        }
        for pago in trazas["pagos"]
        if pago["datos"]["estado"] == "confirmado"
        and pago["datos"]["siniestroId"] in siniestros
        and en_periodo(pago["datos"]["creadoEn"])
    ]
    recibidas = [dict(partida) for partida in partidas]
    if escenario == "diferencias":
        if not recibidas:
            raise ErrorCumplimiento("sin_partidas_para_conciliar", 422)
        recibidas[0]["monto"] = str(Decimal(recibidas[0]["monto"]) + Decimal("0.01"))
    diferencias = [
        {"partidaId": esperada["id"], "esperado": esperada["monto"], "recibido": recibida["monto"]}
        for esperada, recibida in zip(partidas, recibidas, strict=True)
        if esperada["monto"] != recibida["monto"]
    ]
    return {
        "adaptadorVersion": ADAPTADOR,
        "simulado": True,
        "aviso": AVISO,
        "cuotaAcademica": "0.20",
        "envioId": str(intercambio_id),
        "acuse": {"envioId": str(intercambio_id), "simulado": True, "partidas": recibidas},
        "partidas": partidas,
        "diferencias": diferencias,
        "estado": "revision_pendiente" if diferencias else "conciliado",
    }


def _cuota(monto: str) -> str:
    return str((Decimal(monto) * Decimal("0.20")).quantize(Decimal("0.01")))
