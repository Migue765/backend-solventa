from decimal import Decimal

import pytest
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.modelo_academico import prima_academica


@pytest.mark.parametrize(
    ("base", "score", "factor", "esperada"),
    [
        ("180000000", "0.50", "1.00", "99000"),
        ("90000000", "0.50", "1.00", "49500"),
        ("180000000", "0.50", "1.15", "113850"),
        ("180000000", "0.50", "1.30", "128700"),
        ("180000000", "0", "1.00", "90000"),
        ("180000000", "1", "1.00", "108000"),
        ("1000", "0", "1.00", "1"),
    ],
)
def test_calcula_y_redondea_al_peso_cop(base, score, factor, esperada):
    assert prima_academica(
        Decimal(base), Decimal("0.0005"), Decimal(score), Decimal("0.20"), Decimal(factor)
    ) == Decimal(esperada)


@pytest.mark.parametrize("score", ["-0.01", "1.01", "NaN", "Infinity"])
def test_rechaza_un_score_fuera_del_rango(score):
    with pytest.raises(ReglaDeNegocioError, match="score"):
        prima_academica(Decimal("180000000"), Decimal("0.0005"), Decimal(score), Decimal("0.20"))
