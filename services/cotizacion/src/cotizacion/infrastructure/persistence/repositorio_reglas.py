import time
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.application.ports import RepositorioReglas
from cotizacion.domain.tarifa import ReglaTarifa
from cotizacion.infrastructure.persistence.models import ReglaTarifaModel


class RepositorioReglasSqlAlchemy:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self._sesiones = sesiones

    def obtener_vigente(self, producto_id: UUID, ahora: datetime) -> ReglaTarifa | None:
        with self._sesiones() as sesion:
            modelo = sesion.scalar(
                select(ReglaTarifaModel)
                .where(
                    ReglaTarifaModel.producto_id == producto_id,
                    ReglaTarifaModel.vigente_desde <= ahora,
                    or_(
                        ReglaTarifaModel.vigente_hasta.is_(None),
                        ReglaTarifaModel.vigente_hasta > ahora,
                    ),
                )
                .order_by(ReglaTarifaModel.vigente_desde.desc())
                .limit(1)
            )
            return _a_dominio(modelo) if modelo else None


class ReglasConCache:
    """Caché local no autoritativa: una regla en caché solo se usa si sigue vigente."""

    def __init__(
        self,
        origen: RepositorioReglas,
        ttl_segundos: float,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._origen = origen
        self._ttl = ttl_segundos
        self._reloj = reloj
        self._entradas: dict[UUID, tuple[float, ReglaTarifa]] = {}

    def obtener_vigente(self, producto_id: UUID, ahora: datetime) -> ReglaTarifa | None:
        entrada = self._entradas.get(producto_id)
        if entrada is not None:
            guardada_en, regla = entrada
            if self._reloj() - guardada_en < self._ttl and regla.vigente_en(ahora):
                return regla
        regla = self._origen.obtener_vigente(producto_id, ahora)
        if regla is None:
            self._entradas.pop(producto_id, None)
        else:
            self._entradas[producto_id] = (self._reloj(), regla)
        return regla


def _a_dominio(modelo: ReglaTarifaModel) -> ReglaTarifa:
    return ReglaTarifa(
        regla_id=modelo.regla_id,
        version=modelo.version,
        producto_id=modelo.producto_id,
        tasa=modelo.tasa,
        factor_perfil=modelo.factor_perfil,
        vigente_desde=_con_zona(modelo.vigente_desde),
        vigente_hasta=_con_zona(modelo.vigente_hasta) if modelo.vigente_hasta else None,
    )


def _con_zona(valor: datetime) -> datetime:
    # Motores sin soporte de zona horaria (ej. SQLite) devuelven fechas naive en UTC.
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
