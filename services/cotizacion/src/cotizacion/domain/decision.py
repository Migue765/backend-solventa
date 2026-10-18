from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cotizacion.domain.dinero import Dinero
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa

if TYPE_CHECKING:
    from cotizacion.domain.cotizacion import Cotizacion

ESQUEMA_INSTANTANEA = 1


@dataclass(frozen=True)
class InstantaneaDecision:
    """Evidencia inmutable de cómo se calculó una cotización.

    `hash_decision` cubre entradas, perfil, regla y resultado: mismas entradas y misma versión de
    reglas producen el mismo hash. `hash_contenido` cubre además identificadores y fecha, y sirve
    para verificar la integridad del registro guardado.
    """

    cotizacion_id: UUID
    regla_id: UUID
    regla_version: str
    perfil_id: str
    perfil_modelo_version: str
    hash_decision: str
    hash_contenido: str
    contenido: dict[str, Any]
    creada_en: datetime


def construir_instantanea(
    cotizacion: Cotizacion,
    regla: ReglaTarifa,
    perfil: PerfilRiesgo,
    calculada_en: datetime,
    tarificacion: dict[str, Any] | None = None,
) -> InstantaneaDecision:
    perfil_decision: dict[str, Any] = {
        "perfilId": perfil.perfil_id,
        "score": str(perfil.score),
        "modeloVersion": perfil.modelo_version,
    }
    if perfil.evidencia is not None:
        perfil_decision["evidencia"] = perfil.evidencia
    decision = {
        "entradas": {
            "moneda": cotizacion.moneda,
            "coberturas": [
                {
                    "coberturaId": str(c.cobertura_id),
                    "sumaAsegurada": str(c.suma_asegurada.monto),
                    "deducible": str(c.deducible.monto),
                }
                for c in cotizacion.coberturas
            ],
        },
        "perfil": perfil_decision,
        "regla": {
            "reglaId": str(regla.regla_id),
            "version": regla.version,
            "tasa": str(regla.tasa),
            "factorPerfil": str(regla.factor_perfil),
        },
        "resultado": {
            "primaEstimada": _monto(cotizacion.prima_estimada),
            "coberturas": [
                {"coberturaId": str(c.cobertura_id), "prima": _monto(c.prima)}
                for c in cotizacion.coberturas
            ],
        },
    }
    if tarificacion is not None:
        decision["tarificacion"] = tarificacion
    contenido = {
        "esquema": ESQUEMA_INSTANTANEA,
        "cotizacionId": str(cotizacion.cotizacion_id),
        "clienteId": str(cotizacion.cliente_id),
        "productoId": str(cotizacion.producto_id),
        "calculadaEn": calculada_en.isoformat(),
        **decision,
    }
    return InstantaneaDecision(
        cotizacion_id=cotizacion.cotizacion_id,
        regla_id=regla.regla_id,
        regla_version=regla.version,
        perfil_id=perfil.perfil_id,
        perfil_modelo_version=perfil.modelo_version,
        hash_decision=_hash(decision),
        hash_contenido=_hash(contenido),
        contenido=contenido,
        creada_en=calculada_en,
    )


def _monto(dinero: Dinero | None) -> str:
    assert dinero is not None
    return str(dinero.monto)


def _hash(valor: dict[str, Any]) -> str:
    canonico = json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode()).hexdigest()
