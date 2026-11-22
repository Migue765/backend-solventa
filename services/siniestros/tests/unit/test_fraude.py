from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from siniestros.domain.fraude import ConfiguracionFraude, VersionRegla, evaluar, vigentes

AHORA = datetime(2026, 6, 1, 12, tzinfo=UTC)


def version(
    regla_id: str,
    numero: int,
    *,
    senal: str = "siniestros_cliente_90_dias",
    umbral: str = "2",
    peso: int = 60,
    desde: datetime = AHORA - timedelta(days=1),
    hasta: datetime | None = None,
) -> VersionRegla:
    return VersionRegla(
        regla_id,
        numero,
        f"Regla {regla_id}",
        f"Motivo de la regla {regla_id}",
        senal,
        "gte",
        Decimal(umbral),
        peso,
        desde,
        hasta,
    )


def test_vigentes_toma_la_mayor_version_dentro_de_su_vigencia() -> None:
    reglas = [
        version("a", 1),
        version("a", 2),
        version("a", 3, desde=AHORA + timedelta(hours=1)),
        version("b", 1, hasta=AHORA),
        version("c", 1, desde=(AHORA - timedelta(days=1)).replace(tzinfo=None)),
    ]

    assert [(regla.regla_id, regla.version) for regla in vigentes(reglas, AHORA)] == [
        ("a", 2),
        ("c", 1),
    ]


def test_evaluar_clasifica_explica_y_prioriza() -> None:
    configuracion = ConfiguracionFraude()
    reglas = [
        version("a", 1, peso=60),
        version("b", 2, senal="horas_reporte_tras_evento", umbral="48", peso=30),
        version("c", 1, senal="senal_ausente", peso=90),
    ]

    media = evaluar({"siniestros_cliente_90_dias": Decimal(2)}, reglas, configuracion)
    alta = evaluar(
        {"siniestros_cliente_90_dias": Decimal(5), "horas_reporte_tras_evento": Decimal(72)},
        reglas,
        configuracion,
    )
    legitimo = evaluar({"siniestros_cliente_90_dias": Decimal(1)}, reglas, configuracion)

    assert (media.resultado, media.puntaje, media.prioridad) == ("sospechoso", 60, "media")
    assert media.requiere_revision is True
    assert "Motivo de la regla a" in media.motivo
    assert (alta.puntaje, alta.prioridad) == (90, "alta")
    assert [item["reglaId"] for item in alta.evidencia] == ["a", "b"]
    assert [item["activada"] for item in alta.reglas] == [True, True, False]
    assert (legitimo.resultado, legitimo.prioridad, legitimo.evidencia) == ("legitimo", None, ())
    assert legitimo.requiere_revision is False


def test_evaluar_limita_el_puntaje_a_cien() -> None:
    reglas = [version(str(numero), 1, peso=60) for numero in range(3)]

    resultado = evaluar({"siniestros_cliente_90_dias": Decimal(3)}, reglas, ConfiguracionFraude())

    assert resultado.puntaje == 100


@pytest.mark.parametrize(
    "parametros",
    [
        {"umbral_sospecha": 0},
        {"umbral_sospecha": 90, "umbral_prioridad_alta": 80},
        {"politica_continuidad": "ignorar"},
        {"presupuesto_ms": 1001},
    ],
)
def test_configuracion_rechaza_valores_invalidos(parametros: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ConfiguracionFraude(**parametros)  # type: ignore[arg-type]


def test_configuracion_desde_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAUDE_UMBRAL_SOSPECHA", "40")
    monkeypatch.setenv("FRAUDE_UMBRAL_PRIORIDAD_ALTA", "70")
    monkeypatch.setenv("FRAUDE_POLITICA_CONTINUIDAD", "continuar")
    monkeypatch.setenv("FRAUDE_PRESUPUESTO_MS", "500")

    assert ConfiguracionFraude.from_environment() == ConfiguracionFraude(40, 70, "continuar", 500)

    monkeypatch.setenv("FRAUDE_POLITICA_CONTINUIDAD", "otra")
    with pytest.raises(ValueError):
        ConfiguracionFraude.from_environment()
