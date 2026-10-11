from decimal import Decimal
from uuid import uuid4

from cotizacion.infrastructure.perfil_simulado import ProveedorPerfilSimulado


def test_entrega_el_score_configurado():
    perfil = ProveedorPerfilSimulado(Decimal("0.8")).obtener("perfil-1", uuid4())

    assert perfil is not None
    assert perfil.perfil_id == "perfil-1"
    assert perfil.score == Decimal("0.8")


def test_un_perfil_vacio_no_existe():
    assert ProveedorPerfilSimulado(Decimal("0.5")).obtener("  ", uuid4()) is None
