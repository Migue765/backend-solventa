from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from perfilamiento.adapters import FuenteMock
from perfilamiento.application import ConsultarPerfil
from perfilamiento.domain import Consentimiento, ErrorPerfilamiento, Fuente
from perfilamiento.persistence import ConsultaModel, PerfilModel, RepositorioPerfiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

AHORA = datetime(2026, 10, 16, tzinfo=UTC)


class ConsentimientosPrueba:
    def __init__(self, cliente_id: UUID) -> None:
        self.actual: Consentimiento | None = Consentimiento(
            consentimiento_id=uuid4(),
            cliente_id=cliente_id,
            version=1,
            estado="autorizado",
            finalidad="cotizacion",
            alcances=("open_finance", "open_data"),
            efectiva_en=AHORA - timedelta(days=1),
            vigente_hasta=AHORA + timedelta(days=1),
        )

    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento:
        if self.actual is None:
            raise ErrorPerfilamiento("consentimiento_ausente", 403)
        return self.actual


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioPerfiles:
    repo = RepositorioPerfiles(f"sqlite:///{tmp_path / 'perfilamiento.db'}")
    repo.inicializar()
    return repo


def test_perfil_valido_es_versionado_minimo_y_trazable(repo: RepositorioPerfiles) -> None:
    cliente_id, referencia_id = uuid4(), uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    servicio = ConsultarPerfil(
        repo, consentimientos, (FuenteMock("open_finance"), FuenteMock("open_data")), lambda: AHORA
    )
    perfil = servicio.generar(cliente_id, referencia_id, "corr-prueba")
    assert perfil.score == 0.5 and perfil.nivel == "A"
    assert perfil.referencia_id == referencia_id
    assert perfil.vigente_hasta == AHORA + timedelta(minutes=15)
    assert perfil.version == 1
    assert all(fuente.simulada for fuente in perfil.fuentes)
    assert not perfil.fallback_used
    assert servicio.obtener(perfil.perfil_id, cliente_id, "corr-lectura") == perfil
    segundo = servicio.generar(cliente_id, uuid4(), "corr-prueba")
    assert segundo.version == 2
    with Session(repo.engine) as session:
        auditoria = session.scalars(select(ConsultaModel)).first()
        assert auditoria is not None
        assert auditoria.contenido["correlationId"] == "corr-prueba"
        assert auditoria.contenido["consentimientoVersion"] == 1
        assert auditoria.contenido["fuentes"] == ["open_finance", "open_data"]
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 2


@pytest.mark.parametrize("estado", ["ausente", "revocado", "vencido", "ajeno", "alcance"])
def test_sin_consentimiento_aplicable_no_consulta_fuentes(
    repo: RepositorioPerfiles, estado: str
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    assert consentimientos.actual is not None
    if estado == "ausente":
        consentimientos.actual = None
    elif estado == "revocado":
        consentimientos.actual = consentimientos.actual.model_copy(update={"estado": "revocado"})
    elif estado == "vencido":
        consentimientos.actual = consentimientos.actual.model_copy(update={"vigente_hasta": AHORA})
    elif estado == "ajeno":
        consentimientos.actual = consentimientos.actual.model_copy(update={"cliente_id": uuid4()})
    else:
        consentimientos.actual = consentimientos.actual.model_copy(update={"alcances": ()})
    servicio = ConsultarPerfil(repo, consentimientos, (), lambda: AHORA)
    with pytest.raises(ErrorPerfilamiento) as caught:
        servicio.generar(cliente_id, uuid4(), "corr-denegada")
    assert caught.value.status == 403
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 0
        audit = session.scalars(select(ConsultaModel)).one()
        assert audit.contenido["fuentes"] == []
        assert audit.contenido["resultado"] in {
            "consentimiento_ausente",
            "consentimiento_no_vigente",
        }


@pytest.mark.parametrize("escenario", ["no_disponible", "vencido"])
def test_fuentes_fallidas_no_crean_perfil_parcial(
    repo: RepositorioPerfiles, escenario: str
) -> None:
    cliente_id = uuid4()
    fuente = FuenteMock("open_data", "no_disponible" if escenario == "no_disponible" else "vencido")
    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), fuente),
        lambda: AHORA,
    )
    with pytest.raises(ErrorPerfilamiento) as caught:
        servicio.generar(cliente_id, uuid4(), "corr-falla")
    assert caught.value.status == (503 if escenario == "no_disponible" else 422)
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 0
        assert session.scalar(select(func.count()).select_from(ConsultaModel)) == 1


def test_revocacion_en_vuelo_bloquea_entrega_y_reutilizacion(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)

    class FuenteQueRevoca:
        nombre = "open_data"

        def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
            assert consentimientos.actual is not None
            consentimientos.actual = consentimientos.actual.model_copy(
                update={"estado": "revocado", "version": 2}
            )
            return FuenteMock("open_data").consultar(cliente_id, ahora)

    servicio = ConsultarPerfil(
        repo, consentimientos, (FuenteMock("open_finance"), FuenteQueRevoca()), lambda: AHORA
    )
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.generar(cliente_id, uuid4(), "corr-revocacion")
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 0


def test_perfil_no_se_entrega_vencido_revocado_ni_a_otro_cliente(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    servicio = ConsultarPerfil(
        repo, consentimientos, (FuenteMock("open_finance"), FuenteMock("open_data")), lambda: AHORA
    )
    perfil = servicio.generar(cliente_id, uuid4(), "corr-prueba")
    with pytest.raises(ErrorPerfilamiento, match="perfil_no_encontrado"):
        servicio.obtener(perfil.perfil_id, uuid4(), "corr-ajeno")
    vencido = ConsultarPerfil(repo, consentimientos, (), lambda: AHORA + timedelta(minutes=15))
    with pytest.raises(ErrorPerfilamiento, match="perfil_vencido"):
        vencido.obtener(perfil.perfil_id, cliente_id, "corr-vencido")
    assert consentimientos.actual is not None
    consentimientos.actual = consentimientos.actual.model_copy(update={"estado": "revocado"})
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.obtener(perfil.perfil_id, cliente_id, "corr-revocado")


def test_no_entrega_perfil_alterado(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    perfil = servicio.generar(cliente_id, uuid4(), "corr-prueba")
    with Session(repo.engine) as session, session.begin():
        fila = session.get(PerfilModel, perfil.perfil_id)
        assert fila is not None
        fila.contenido = {**fila.contenido, "score": "0.01"}
    with pytest.raises(ErrorPerfilamiento, match="perfil_no_integro"):
        servicio.obtener(perfil.perfil_id, cliente_id, "corr-integridad")
