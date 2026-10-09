from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import RepositorioCotizacionesSqlAlchemy


def cotizacion_calculada():
    c1 = CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), "COP"), Dinero(Decimal("100"), "COP"))
    c2 = CoberturaCotizada(uuid4(), Dinero(Decimal("500"), "COP"), Dinero.cero("COP"))
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[c1, c2])
    cotizacion.calcular(
        {c1.cobertura_id: Dinero(Decimal("13.50"), "COP"), c2.cobertura_id: Dinero(Decimal("7.50"), "COP")},
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        timedelta(days=30),
    )
    return cotizacion


def test_guarda_y_recupera_la_cotizacion(database_url):
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url))
    original = cotizacion_calculada()

    repositorio.guardar(original)
    recuperada = repositorio.obtener(original.cotizacion_id)

    assert recuperada == original


def test_obtener_inexistente_devuelve_none(database_url):
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url))

    assert repositorio.obtener(uuid4()) is None
