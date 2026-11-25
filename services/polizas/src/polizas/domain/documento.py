import hashlib
import json
from typing import Annotated
from uuid import UUID

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

AVISO = (
    "Documento y aceptacion simulados para fines academicos. "
    "Sin validez legal; no constituyen firma electronica ni digital."
)
FORMATO_VERSION = 1

_PLANTILLA = Environment(autoescape=True, undefined=StrictUndefined).from_string(
    """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Poliza academica simulada</title></head>
<body><h1>Documento de poliza SIMULADO</h1><p><strong>{{ aviso }}</strong></p>
<p>Formato academico {{ formatoVersion }}</p>
<dl><dt>Poliza</dt><dd>{{ polizaId }}</dd><dt>Cliente</dt><dd>{{ clienteId }}</dd>
<dt>Suscripcion</dt><dd>{{ suscripcionId }}</dd>
<dt>Producto</dt><dd>{{ producto.nombre }} ({{ producto.tipo }})</dd>
<dt>Estado registrado</dt><dd>{{ estado }}</dd>
<dt>Vigencia</dt><dd>{{ vigencia.inicio }} a {{ vigencia.fin }}</dd>
<dt>Emitida en UTC</dt><dd>{{ emitidaEn }}</dd>
<dt>Version de condiciones</dt><dd>{{ version if version is not none else 'No aplica' }}</dd>
<dt>Plan academico</dt><dd>{{ plan if plan is not none else 'No aplica' }}</dd>
<dt>Prima del modelo academico</dt>
<dd>{% if prima %}{{ prima.monto }} {{ prima.moneda }} ({{ prima.periodicidad }})
{% else %}No disponible en este modelo; no se estima un importe.{% endif %}</dd>
<dt>Regla de emision</dt><dd>{{ reglaEmision }}</dd></dl>
<h2>Coberturas del modelo</h2><ul>{% for cobertura in coberturas %}
<li><strong>{{ cobertura.nombre }}</strong> ({{ cobertura.codigo }}):
{{ cobertura.descripcion }} ({{ 'opcional' if cobertura.opcional else 'obligatoria' }},
{{ 'incluida' if cobertura.incluida else 'no incluida' }})</li>{% endfor %}</ul>
<h2>Condiciones del modelo</h2><ul>{% for condicion in condiciones %}
<li>{{ condicion }}</li>{% endfor %}</ul>
<p>La aceptacion simulada se registra por separado y no modifica este documento.
No cambia la emision, la cobertura ni el estado de pago de la poliza.</p></body></html>"""
)


def archivo_html(contenido: str) -> str:
    return _PLANTILLA.render(**json.loads(contenido))


def canonico(contenido: object) -> str:
    return json.dumps(contenido, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_contenido(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class ConfirmacionDocumento(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    documento_id: UUID = Field(alias="documentoId")
    version_poliza: Annotated[StrictInt, Field(ge=1)] | None = Field(alias="versionPoliza")
    contenido_hash: str = Field(alias="contenidoHash", pattern=r"^[a-f0-9]{64}$")
    acepta_simulacion: StrictBool = Field(alias="aceptaSimulacion")

    @model_validator(mode="after")
    def explicita(self) -> "ConfirmacionDocumento":
        if self.acepta_simulacion is not True:
            raise ValueError("Se requiere aceptar explicitamente la simulacion.")
        return self


class DocumentoDesactualizado(Exception):
    pass


class DocumentoNoDisponible(Exception):
    pass
