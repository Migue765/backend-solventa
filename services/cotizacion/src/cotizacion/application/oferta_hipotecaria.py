import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import UUID

from cotizacion.application.ofertas import ofertas_desde_decision
from cotizacion.domain.errors import OfertaNoDisponibleError
from cotizacion.domain.oferta_hipotecaria import OfertaHipotecariaPersistida
from cotizacion.domain.ofertas import Oferta
from cotizacion.domain.perfil_riesgo import perfil_desde_score
from cotizacion.domain.solicitud import SolicitudCotizacion


def construir_oferta_hipotecaria(
    operation_id: UUID,
    socio_id: str,
    solicitud: SolicitudCotizacion,
) -> OfertaHipotecariaPersistida:
    instantanea = solicitud.cotizacion.instantanea
    if instantanea is None:
        raise OfertaNoDisponibleError()
    detalle = instantanea.contenido.get("tarificacion")
    perfil = instantanea.contenido.get("perfil")
    regla = instantanea.contenido.get("regla")
    if (
        not isinstance(detalle, dict)
        or detalle.get("productoId") != "mortgage-life"
        or not isinstance(perfil, dict)
        or not isinstance(regla, dict)
    ):
        raise OfertaNoDisponibleError()
    evidencia = perfil.get("evidencia")
    if not isinstance(evidencia, dict):
        raise OfertaNoDisponibleError()
    fuentes = evidencia.get("fuentes")
    if not isinstance(fuentes, list) or not fuentes:
        raise OfertaNoDisponibleError()

    resultado = ofertas_desde_decision(solicitud, solicitud.creada_en)
    oferta = next((item for item in resultado.ofertas if item.recomendada), None)
    if oferta is None:
        raise OfertaNoDisponibleError()
    contenido = {
        **_oferta_a_dict(oferta),
        "explicacion": _explicacion(oferta, detalle, perfil, regla),
        "trazabilidad": {
            "esquemaDecision": instantanea.contenido["esquema"],
            "hashDecision": instantanea.hash_decision,
            "hashContenido": instantanea.hash_contenido,
            "perfilId": instantanea.perfil_id,
            "perfilVersion": evidencia["version"],
            "modeloPerfilVersion": instantanea.perfil_modelo_version,
            "reglaId": str(instantanea.regla_id),
            "reglaVersion": instantanea.regla_version,
            "fuentes": [
                {"nombre": fuente["nombre"], "version": fuente["version"]} for fuente in fuentes
            ],
        },
    }
    return OfertaHipotecariaPersistida(
        operation_id=operation_id,
        socio_id=socio_id,
        oferta_id=oferta.oferta_id,
        version=oferta.version,
        contenido=contenido,
        evidencia_decision=deepcopy(instantanea.contenido),
        hash_oferta=_hash(contenido),
        hash_decision=instantanea.hash_decision,
        creada_en=instantanea.creada_en,
    )


def _oferta_a_dict(oferta: Oferta) -> dict[str, Any]:
    prima = {
        "monto": str(oferta.prima_mensual.monto),
        "moneda": oferta.prima_mensual.moneda,
    }
    return {
        "id": str(oferta.oferta_id),
        "version": oferta.version,
        "nombre": oferta.nombre,
        "descripcion": oferta.descripcion,
        "prima": prima,
        "periodicidad": oferta.periodicidad,
        "primaMensual": prima if oferta.periodicidad == "mensual" else None,
        "coberturaPrincipal": oferta.cobertura_principal,
        "deducible": oferta.deducible,
        "vigencia": oferta.vigencia,
        "responsabilidadCivil": oferta.responsabilidad_civil,
        "coberturas": list(oferta.coberturas),
        "condiciones": list(oferta.condiciones),
        "beneficiosAdicionales": list(oferta.beneficios_adicionales),
        "recomendada": oferta.recomendada,
        "estado": oferta.estado.value,
        "vigenteHasta": oferta.vigente_hasta.isoformat().replace("+00:00", "Z"),
    }


def _explicacion(
    oferta: Oferta,
    detalle: dict[str, Any],
    perfil: dict[str, Any],
    regla: dict[str, Any],
) -> dict[str, Any]:
    saldo = Decimal(str(detalle["baseAsegurada"]))
    score = Decimal(str(perfil["score"]))
    nivel = perfil_desde_score(score).nivel.value
    plan = detalle["planes"][0]
    tasa = Decimal(str(regla["tasa"]))
    factor_perfil = Decimal(str(regla["factorPerfil"]))
    prima = oferta.prima_mensual.monto
    return {
        "resumen": (
            f"La prima mensual de ${prima:,.0f} COP protege un saldo hipotecario de "
            f"${saldo:,.0f} COP. Se calculó con el perfil autorizado de riesgo {nivel} "
            f"y la regla {regla['version']}."
        ).replace(",", "."),
        "factoresDeterminantes": [
            {
                "nombre": "Saldo hipotecario",
                "valor": f"${saldo:,.0f} COP".replace(",", "."),
                "impacto": "Define la suma asegurada y la base del cálculo.",
            },
            {
                "nombre": "Perfil de riesgo autorizado",
                "valor": f"Nivel {nivel}, score {score}",
                "impacto": (
                    f"Ajusta la prima con el factor de perfil {factor_perfil} de la regla vigente."
                ),
            },
            {
                "nombre": "Regla y plan",
                "valor": f"{regla['version']} / {plan['codigo']}",
                "impacto": (
                    f"Aplica la tasa {tasa} y el factor de plan {plan['factor']} "
                    "con redondeo a pesos colombianos."
                ),
            },
        ],
    }


def _hash(contenido: dict[str, Any]) -> str:
    canonico = json.dumps(contenido, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode()).hexdigest()
