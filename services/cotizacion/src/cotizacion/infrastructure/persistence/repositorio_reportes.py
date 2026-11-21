from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.productos import PRODUCTOS
from cotizacion.domain.reporte import ReporteInconsistenteError, instante
from cotizacion.infrastructure.persistence.models import CotizacionModel, PagoSimuladoModel

_CODIGOS = {producto.producto_interno_id: producto.producto_id for producto in PRODUCTOS}


class RepositorioReportePrimas:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def primas(self, corte: datetime) -> list[dict[str, str]]:
        consulta = (
            select(PagoSimuladoModel, CotizacionModel.producto_id)
            .outerjoin(
                CotizacionModel, CotizacionModel.solicitud_id == PagoSimuladoModel.solicitud_id
            )
            .where(PagoSimuladoModel.confirmada_en <= corte)
            .order_by(PagoSimuladoModel.pago_id)
        )
        with self._sesiones() as sesion:
            filas = sesion.execute(consulta).all()
        registros = []
        for pago, producto_interno in filas:
            confirmada = _utc(pago.confirmada_en)
            if confirmada > corte:
                continue
            producto = _CODIGOS.get(producto_interno)
            if producto is None:
                raise ReporteInconsistenteError("Prima sin producto identificable.")
            registros.append(
                {
                    "pagoId": str(pago.pago_id),
                    "producto": producto,
                    "monto": str(Decimal(pago.monto).quantize(Decimal("0.01"))),
                    "moneda": pago.moneda,
                    "confirmadaEn": instante(confirmada),
                }
            )
        return registros


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
