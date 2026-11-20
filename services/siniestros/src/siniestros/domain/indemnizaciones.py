"""Indemnizacion de siniestros aprobados (HU28).

Reutiliza las garantias de HU31: clave idempotente, intentos durables, consulta del
efecto antes de ejecutar y conciliacion de respuestas ambiguas.
"""

import hashlib
import json
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import StringConstraints, field_validator

from siniestros.domain.pagos_parametricos import (
    DineroIndemnizacion,
    EntradaPagoParametrico,
    ResultadoProveedorPago,
)

UUID_NULO = UUID(int=0)


class SolicitudIndemnizacion(EntradaPagoParametrico):
    beneficiarioId: UUID
    indemnizacion: DineroIndemnizacion

    @field_validator("beneficiarioId")
    @classmethod
    def beneficiario_identificado(cls, valor: UUID) -> UUID:
        if valor == UUID_NULO:
            raise ValueError("El beneficiario debe estar identificado.")
        return valor


class OrdenIndemnizacion(EntradaPagoParametrico):
    indemnizacionId: UUID
    siniestroId: UUID
    clienteId: UUID
    polizaId: UUID
    beneficiarioId: UUID
    indemnizacion: DineroIndemnizacion


class EfectoProveedor(EntradaPagoParametrico):
    resultado: ResultadoProveedorPago
    referencia: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class ProveedorIndemnizacion(Protocol):
    def consultar(self, indemnizacion_id: UUID, huella: str) -> EfectoProveedor | None: ...

    def ejecutar(self, orden: OrdenIndemnizacion, huella: str) -> EfectoProveedor: ...


def _huella(contenido: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _dinero(dinero: DineroIndemnizacion) -> dict[str, object]:
    # 100 y 100.00 son el mismo monto y deben producir la misma huella.
    return {"monto": f"{dinero.monto:.2f}", "moneda": dinero.moneda}


def huella_solicitud(siniestro_id: UUID, solicitud: SolicitudIndemnizacion) -> str:
    return _huella(
        {
            "siniestroId": str(siniestro_id),
            "beneficiarioId": str(solicitud.beneficiarioId),
            "indemnizacion": _dinero(solicitud.indemnizacion),
        }
    )


def huella_orden(orden: OrdenIndemnizacion) -> str:
    return _huella(
        {
            "indemnizacionId": str(orden.indemnizacionId),
            "siniestroId": str(orden.siniestroId),
            "clienteId": str(orden.clienteId),
            "polizaId": str(orden.polizaId),
            "beneficiarioId": str(orden.beneficiarioId),
            "indemnizacion": _dinero(orden.indemnizacion),
        }
    )
