from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from polizas.domain.condiciones import (
    MONEDA,
    PRODUCTOS,
    Condiciones,
    detalle_coberturas,
    prima,
)
from polizas.domain.emision import estado_vigencia


@dataclass(frozen=True)
class ProductoSinCondiciones:
    nombre: str
    cobertura_codigo: str
    cobertura_nombre: str
    cobertura_descripcion: str
    condiciones: tuple[str, ...]


# Productos de vigencia contratada: sin plan ni endosos, conservan la presentacion de Cotizacion.
SIN_CONDICIONES: dict[str, ProductoSinCondiciones] = {
    "travel": ProductoSinCondiciones(
        "Seguro de Viaje",
        "asistencia_medica",
        "Asistencia médica",
        "Atención por accidente o enfermedad durante el viaje.",
        ("La cobertura aplica durante las fechas y destinos informados.",),
    ),
    "parametric": ProductoSinCondiciones(
        "Seguro de Vuelos",
        "retraso_vuelo",
        "Retraso de vuelo",
        "Compensación cuando el retraso supera el umbral contratado.",
        ("La confirmación se basa en la información oficial de la aerolínea.",),
    ),
    "parametric-weather": ProductoSinCondiciones(
        "Seguro Paramétrico Climático",
        "evento_climatico",
        "Evento climático",
        "Protección ante el evento climático durante el periodo y la ubicación declarados.",
        ("La cobertura corresponde al evento climático y la ubicación declarados.",),
    ),
}


def numero_poliza(poliza_id: UUID, emitida_en: datetime) -> str:
    """Numero visible y estable derivado de la emision; el identificador sigue siendo el UUID."""
    return f"POL-{emitida_en:%Y%m%d}-{poliza_id.hex[:8].upper()}"


def nombre_producto(tipo: str) -> str:
    if tipo in PRODUCTOS:
        return PRODUCTOS[tipo].nombre
    return SIN_CONDICIONES[tipo].nombre


def cobertura_principal(tipo: str) -> dict[str, str]:
    if tipo in PRODUCTOS:
        principal = next(c for c in PRODUCTOS[tipo].coberturas if c.recargo is None)
        return {"codigo": principal.codigo, "nombre": principal.nombre}
    producto = SIN_CONDICIONES[tipo]
    return {"codigo": producto.cobertura_codigo, "nombre": producto.cobertura_nombre}


def resumen(
    poliza_id: UUID,
    tipo: str,
    inicio: date,
    fin: date,
    emitida_en: datetime,
    ahora: datetime,
) -> dict[str, object]:
    return {
        "id": str(poliza_id),
        "numero": numero_poliza(poliza_id, emitida_en),
        "producto": {"tipo": tipo, "nombre": nombre_producto(tipo)},
        "estado": estado_vigencia(inicio, fin, ahora),
        "vigencia": {"inicio": inicio.isoformat(), "fin": fin.isoformat()},
        "coberturaPrincipal": cobertura_principal(tipo),
        "simulada": True,
    }


def tiene_condiciones(tipo: str) -> bool:
    return tipo in PRODUCTOS


def condiciones_detalle(tipo: str, vigentes: tuple[int, Condiciones] | None) -> dict[str, object]:
    if vigentes is not None:
        version, condiciones = vigentes
        return {
            "version": version,
            "plan": condiciones.plan,
            "coberturas": detalle_coberturas(tipo, condiciones),
            "prima": {
                "monto": str(prima(tipo, condiciones)),
                "moneda": MONEDA,
                "periodicidad": "mensual",
            },
            "condiciones": list(PRODUCTOS[tipo].condiciones),
        }
    producto = SIN_CONDICIONES[tipo]
    return {
        "version": None,
        "plan": None,
        "coberturas": [
            {
                "codigo": producto.cobertura_codigo,
                "nombre": producto.cobertura_nombre,
                "descripcion": producto.cobertura_descripcion,
                "opcional": False,
                "incluida": True,
            }
        ],
        "prima": None,
        "condiciones": list(producto.condiciones),
    }
