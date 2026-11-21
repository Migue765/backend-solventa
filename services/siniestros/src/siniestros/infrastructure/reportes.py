"""Fuente de siniestros y pagos parametricos para los reportes operativos (HU36).

Reconstruye el estado de cada registro al corte solicitado a partir del historial
persistido y no expone datos personales del cliente ni del beneficiario.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict
from siniestros.domain import ClaimInput
from siniestros.domain.estado import DESCRIPCIONES, TIPOS, SiniestroNoIntegro
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim, ParametricPayment
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

ESTADOS_PAGO = frozenset({"pendiente", "confirmado", "rechazado"})


class ConsultaReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corte: AwareDatetime


class RepositorioReportes:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def consultar(self, corte: datetime, ahora: datetime) -> dict[str, Any]:
        corte = _utc(corte)
        if corte > ahora:
            raise ValueError("El corte no puede estar en el futuro.")
        with self.sesiones() as sesion:
            siniestros = self._siniestros(sesion, corte)
            pagos = self._pagos(sesion, corte)
        return {
            "corte": _instante(corte),
            "fuente": "siniestros",
            "registros": len(siniestros) + len(pagos),
            "huella": huella({"siniestros": siniestros, "pagos": pagos}),
            "siniestros": siniestros,
            "pagos": pagos,
        }

    def _siniestros(self, sesion: Session, corte: datetime) -> list[dict[str, Any]]:
        historial: dict[str, list[CambioEstadoModel]] = {}
        for cambio in sesion.scalars(
            select(CambioEstadoModel).order_by(
                CambioEstadoModel.siniestro_id, CambioEstadoModel.secuencia
            )
        ):
            historial.setdefault(cambio.siniestro_id, []).append(cambio)
        registros = []
        for siniestro in sesion.scalars(select(Claim).order_by(Claim.id)):
            try:
                aviso = ClaimInput.model_validate(siniestro.payload)
                reportado = _utc(datetime.fromisoformat(siniestro.created))
            except (ValueError, TypeError) as error:
                raise SiniestroNoIntegro("El aviso persistido no es valido.") from error
            if reportado > corte:
                continue
            if siniestro.tipo is not None and siniestro.tipo not in TIPOS:
                raise SiniestroNoIntegro("El tipo persistido no es valido.")
            previos = [c for c in historial.get(siniestro.id, []) if _utc(c.ocurrido_en) <= corte]
            if not previos or previos[-1].estado not in DESCRIPCIONES:
                raise SiniestroNoIntegro("El historial no permite fijar el estado al corte.")
            registros.append(
                {
                    "id": siniestro.id,
                    "polizaId": str(aviso.polizaId),
                    "tipo": siniestro.tipo,
                    "estado": previos[-1].estado,
                    "reportadoEn": _instante(reportado),
                }
            )
        return registros

    def _pagos(self, sesion: Session, corte: datetime) -> list[dict[str, Any]]:
        registros = []
        for pago in sesion.scalars(select(ParametricPayment).order_by(ParametricPayment.id)):
            creado = _utc(pago.creado_en)
            if creado > corte:
                continue
            if pago.estado not in ESTADOS_PAGO:
                raise SiniestroNoIntegro("El estado del pago no es valido.")
            if pago.estado != "pendiente" and pago.finalizado_en is None:
                raise SiniestroNoIntegro("El pago finalizado no tiene fecha de cierre.")
            finalizado = pago.finalizado_en is not None and _utc(pago.finalizado_en) <= corte
            registros.append(
                {
                    "id": pago.id,
                    "siniestroId": pago.siniestro_id,
                    "estado": pago.estado if finalizado else "pendiente",
                    "monto": str(Decimal(pago.monto).quantize(Decimal("0.01"))),
                    "moneda": pago.moneda,
                    "creadoEn": _instante(creado),
                }
            )
        return registros


def huella(contenido: dict[str, Sequence[dict[str, Any]]]) -> str:
    canonico = json.dumps(contenido, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _instante(valor: datetime) -> str:
    return _utc(valor).isoformat().replace("+00:00", "Z")
