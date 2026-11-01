import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import EntradaOfertaInvalidaError, ReglaDeNegocioError
from cotizacion.domain.modelo_academico import VERSION_ACADEMICA, prima_academica
from cotizacion.domain.modelo_climatico import VERSION_CLIMATICA, planes_climaticos
from cotizacion.domain.perfil_riesgo import perfil_desde_score

ESQUEMA_OFERTA_SOCIO = 1


@dataclass(frozen=True)
class OfertaSocio:
    """Oferta persistida de una operación de socio: evidencia histórica que no se recalcula."""

    oferta_socio_id: UUID
    socio_id: str
    operation_id: UUID
    cliente_id: UUID
    producto_id: str
    solicitud_id: str
    cotizacion_id: UUID
    hash_decision: str
    hash_contenido: str
    contenido: dict[str, Any]
    calculada_en: datetime
    vigente_hasta: datetime


def factores_planes(contenido_decision: dict[str, Any]) -> list[list[dict[str, str]]]:
    """Explica cada plan desde la instantánea y verifica que su prima sea reproducible.

    El rating ya incorpora el score del perfil; aquí solo se desglosa, nunca se vuelve a aplicar.
    Una prima que no se reproduce con sus propias entradas se trata como inconsistente.
    """
    detalle = contenido_decision.get("tarificacion")
    if not detalle or not detalle.get("planes"):
        raise EntradaOfertaInvalidaError("decision_sin_tarificacion")
    score = Decimal(contenido_decision["perfil"]["score"])
    nivel = perfil_desde_score(score).nivel.value
    if detalle["modelo"] == VERSION_ACADEMICA:
        return [
            _factores_academicos(contenido_decision, detalle, plan, score, nivel)
            for plan in detalle["planes"]
        ]
    if detalle["modelo"] == VERSION_CLIMATICA:
        return _factores_climaticos(detalle, score, nivel)
    raise EntradaOfertaInvalidaError("modelo_no_soportado")


def hash_oferta(contenido: dict[str, Any]) -> str:
    canonico = json.dumps(contenido, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode()).hexdigest()


def _factores_academicos(
    decision: dict[str, Any],
    detalle: dict[str, Any],
    plan: dict[str, Any],
    score: Decimal,
    nivel: str,
) -> list[dict[str, str]]:
    base = Decimal(detalle["baseAsegurada"])
    tasa = Decimal(decision["regla"]["tasa"])
    factor_perfil = Decimal(decision["regla"]["factorPerfil"])
    factor_plan = Decimal(plan["factor"])
    duracion = int(detalle["duracion"])
    try:
        esperada = prima_academica(base, tasa, score, factor_perfil, factor_plan, duracion)
    except ReglaDeNegocioError as error:
        raise EntradaOfertaInvalidaError("decision_inconsistente") from error
    if esperada != Decimal(plan["prima"]):
        raise EntradaOfertaInvalidaError("decision_inconsistente")
    return [
        _factor("base_asegurada", "Base asegurada del producto", str(base)),
        _factor("tasa_producto", "Tasa vigente del producto", str(tasa)),
        _factor(
            "ajuste_riesgo",
            f"Ajuste por riesgo individualizado (nivel {nivel}, score {score})",
            str((1 + factor_perfil * score).normalize()),
        ),
        _factor("factor_plan", f"Factor del plan {plan['nombre']}", str(factor_plan)),
        _factor("duracion", f"Duración ({detalle['periodicidad']})", str(duracion)),
        _factor("redondeo", "Redondeo de la prima", detalle["redondeo"]),
    ]


def _factores_climaticos(
    detalle: dict[str, Any], score: Decimal, nivel: str
) -> list[list[dict[str, str]]]:
    factor_riesgo = Decimal(detalle["factorRiesgoOfertas"])
    if perfil_desde_score(score).factor_prima != factor_riesgo:
        raise EntradaOfertaInvalidaError("decision_inconsistente")
    datos = detalle["datosProducto"]
    try:
        esperados = planes_climaticos(datos, factor_riesgo)
    except (ReglaDeNegocioError, KeyError, ValueError) as error:
        raise EntradaOfertaInvalidaError("decision_inconsistente") from error
    guardados = detalle["planes"]
    if len(esperados) != len(guardados) or any(
        esperado["codigo"] != plan["codigo"]
        or Dinero(Decimal(esperado["prima"]), "COP").monto != Decimal(plan["prima"])
        for esperado, plan in zip(esperados, guardados, strict=True)
    ):
        raise EntradaOfertaInvalidaError("decision_inconsistente")
    tasa_diaria = "0.0001" if datos["weatherEvent"] == "heavy-rain" else "0.00012"
    return [
        [
            _factor("monto_asegurado", "Monto asegurado declarado", detalle["baseAsegurada"]),
            _factor("tasa_diaria", "Tasa diaria del evento declarado", tasa_diaria),
            _factor("duracion", "Días del periodo asegurado", str(detalle["duracion"])),
            _factor("factor_plan", f"Factor del plan {plan['nombre']}", plan["factor"]),
            _factor(
                "ajuste_riesgo",
                f"Ajuste por riesgo individualizado (nivel {nivel}, score {score})",
                str(factor_riesgo),
            ),
            _factor("prima_minima", "Prima mínima por periodo", "1000"),
        ]
        for plan in guardados
    ]


def _factor(codigo: str, descripcion: str, valor: str) -> dict[str, str]:
    return {"codigo": codigo, "descripcion": descripcion, "valor": valor}
