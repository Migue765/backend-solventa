from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from cotizacion.domain.dinero import Dinero


class EstadoOferta(str, Enum):
    DISPONIBLE = "disponible"
    ACEPTADA = "aceptada"
    VENCIDA = "vencida"
    RETIRADA = "retirada"


@dataclass(frozen=True)
class Oferta:
    oferta_id: UUID
    version: int
    nombre: str
    descripcion: str
    prima_mensual: Dinero
    cobertura_principal: str
    deducible: str
    vigencia: str
    responsabilidad_civil: str
    coberturas: tuple[str, ...]
    condiciones: tuple[str, ...]
    beneficios_adicionales: tuple[str, ...]
    recomendada: bool
    estado: EstadoOferta
    vigente_hasta: datetime
    periodicidad: str = "mensual"


@dataclass(frozen=True)
class ResultadoOfertas:
    solicitud_id: str
    calculada_en: datetime
    vigente_hasta: datetime
    tasa_aplicada: str
    ofertas: tuple[Oferta, ...]


@dataclass(frozen=True)
class SeleccionOferta:
    solicitud_id: str
    cotizacion_id: UUID
    oferta_id: UUID
    version: int
    nombre_oferta: str
    prima_mensual: Dinero
    seleccionada_en: datetime
    idempotency_key: UUID
    request_fingerprint: str
    periodicidad: str = "mensual"
