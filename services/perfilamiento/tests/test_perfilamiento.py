import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

import pytest
from perfilamiento.adapters import FuenteMock
from perfilamiento.app import Config, create_app
from perfilamiento.application import ConsultarPerfil
from perfilamiento.client_information import (
    ConsultarInformacionCliente,
    ConsultarInformacionComando,
)
from perfilamiento.domain import (
    Consentimiento,
    ErrorPerfilamiento,
    EventoConsentimiento,
    Fuente,
    Perfil,
)
from perfilamiento.persistence import (
    ConsultaInformacionModel,
    ConsultaModel,
    EventoConsentimientoModel,
    PerfilModel,
    RepositorioPerfiles,
)
from sqlalchemy import event, func, select
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
            fuentes=("open_finance", "open_data"),
            datos=("indicador_riesgo",),
            version_terminos="1.0",
            actor_id=cliente_id,
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
    assert perfil.operation_id == referencia_id
    assert perfil.credito_id == str(referencia_id)
    assert perfil.vigente_hasta == AHORA + timedelta(minutes=15)
    assert perfil.version == 1
    assert all(fuente.simulada for fuente in perfil.fuentes)
    assert not perfil.fallback_used
    assert {variable.fuente for variable in perfil.variables} == {
        "open_finance",
        "open_data",
    }
    assert {variable.nombre for variable in perfil.variables_excluidas} == {
        "saldo_promedio",
        "zona_geografica",
    }
    assert perfil.regla.version == "1.0"
    assert servicio.obtener(perfil.perfil_id, cliente_id, "corr-lectura") == perfil
    assert servicio.generar(cliente_id, referencia_id, "corr-reintento") == perfil
    segundo = servicio.generar(cliente_id, uuid4(), "corr-prueba")
    assert segundo.version == 2
    with Session(repo.engine) as session:
        auditoria = session.scalars(select(ConsultaModel)).first()
        assert auditoria is not None
        assert auditoria.contenido["correlationId"] == "corr-prueba"
        assert auditoria.contenido["consentimientoVersion"] == 1
        assert auditoria.contenido["fuentes"] == ["open_finance", "open_data"]
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 2


def test_consentimiento_solicitado_debe_coincidir(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    assert consentimientos.actual is not None
    servicio = ConsultarPerfil(
        repo,
        consentimientos,
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )

    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.generar(
            cliente_id,
            uuid4(),
            "corr-consentimiento",
            uuid4(),
            consentimientos.actual.version,
        )

    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 0


def test_consulta_informacion_minimiza_y_es_idempotente(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    servicio = ConsultarInformacionCliente(
        repo,
        consentimientos,
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    comando = ConsultarInformacionComando(
        operation_id=uuid4(),
        socio_id="BANCO-001",
        actor_id=uuid4(),
        cliente_id=cliente_id,
        producto_id="mortgage-life",
        finalidad="cotizacion",
        datos_solicitados=("indicador_riesgo", "movimientos", "saldo"),
    )

    consulta = servicio.ejecutar(comando, "corr-hu44")
    reintento = servicio.ejecutar(comando, "corr-reintento")

    assert reintento == consulta
    assert consulta.datos == {"indicadorRiesgo": "0.50"}
    assert {evidencia.fuente for evidencia in consulta.evidencia_fuentes} == {
        "open_finance",
        "open_data",
    }
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConsultaInformacionModel)) == 1
        auditorias = list(
            session.scalars(
                select(ConsultaModel).where(
                    ConsultaModel.contenido["operationId"].as_string() == str(comando.operation_id)
                )
            )
        )
        assert {auditoria.contenido["fuente"] for auditoria in auditorias} == {
            "open_finance",
            "open_data",
        }
        assert all(auditoria.contenido["resultado"] == "consultada" for auditoria in auditorias)


def test_consulta_informacion_rechaza_reuso_con_otros_datos(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    servicio = ConsultarInformacionCliente(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    comando = ConsultarInformacionComando(
        uuid4(),
        "BANCO-001",
        uuid4(),
        cliente_id,
        "mortgage-life",
        "cotizacion",
        ("indicador_riesgo",),
    )
    servicio.ejecutar(comando, "corr-original")

    with pytest.raises(ErrorPerfilamiento, match="conflicto_idempotencia"):
        servicio.ejecutar(
            ConsultarInformacionComando(**{**comando.__dict__, "producto_id": "life"}),
            "corr-conflicto",
        )


def test_consulta_persistida_se_recupera_solo_con_consentimiento_vigente(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    servicio = ConsultarInformacionCliente(
        repo,
        consentimientos,
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    comando = ConsultarInformacionComando(
        uuid4(),
        "BANCO-001",
        uuid4(),
        cliente_id,
        "mortgage-life",
        "cotizacion",
        ("indicador_riesgo",),
    )
    consulta = servicio.ejecutar(comando, "corr-original")

    assert servicio.recuperar("BANCO-001", comando.operation_id, "corr-recuperar") == consulta
    assert consentimientos.actual is not None
    consentimientos.actual = consentimientos.actual.model_copy(
        update={"estado": "revocado", "version": 2}
    )
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.ejecutar(comando, "corr-reintento-revocado")
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.recuperar("BANCO-001", comando.operation_id, "corr-recuperar-revocado")


def test_consulta_revocada_bloquea_antes_de_consultar_fuentes(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    assert consentimientos.actual is not None
    consentimientos.actual = consentimientos.actual.model_copy(update={"estado": "revocado"})

    class FuenteProhibida:
        nombre: Literal["open_finance"] = "open_finance"

        def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
            pytest.fail("No se consulta una fuente despues de la revocacion.")

    servicio = ConsultarInformacionCliente(
        repo, consentimientos, (FuenteProhibida(),), lambda: AHORA
    )
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.ejecutar(
            ConsultarInformacionComando(
                uuid4(),
                "BANCO-001",
                uuid4(),
                cliente_id,
                "mortgage-life",
                "cotizacion",
                ("indicador_riesgo",),
            ),
            "corr-revocado",
        )


def test_fuente_fallida_abre_circuito_sin_respuesta_parcial(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()

    class FuenteFallida:
        nombre: Literal["open_finance"] = "open_finance"
        llamadas = 0

        def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
            self.llamadas += 1
            raise ErrorPerfilamiento("fuente_no_disponible", 503)

    fuente = FuenteFallida()
    consentimiento = ConsentimientosPrueba(cliente_id)
    assert consentimiento.actual is not None
    consentimiento.actual = consentimiento.actual.model_copy(
        update={"alcances": ("open_finance",), "fuentes": ("open_finance",)}
    )
    servicio = ConsultarInformacionCliente(repo, consentimiento, (fuente,), lambda: AHORA)

    for expected in ("fuente_no_disponible", "fuente_no_disponible", "circuito_abierto"):
        with pytest.raises(ErrorPerfilamiento, match=expected):
            servicio.ejecutar(
                ConsultarInformacionComando(
                    uuid4(),
                    "BANCO-001",
                    uuid4(),
                    cliente_id,
                    "mortgage-life",
                    "cotizacion",
                    ("indicador_riesgo",),
                ),
                "corr-falla",
            )

    assert fuente.llamadas == 2
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(ConsultaInformacionModel)) == 0


@pytest.mark.parametrize(
    "estado",
    [
        "ausente",
        "revocado",
        "rechazado",
        "vencido",
        "ajeno",
        "alcance",
        "fuente",
        "finalidad",
        "datos",
        "actor",
        "terminos",
        "legado",
    ],
)
def test_sin_consentimiento_aplicable_no_consulta_fuentes(
    repo: RepositorioPerfiles, estado: str
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    assert consentimientos.actual is not None
    if estado == "ausente":
        consentimientos.actual = None
    elif estado in {"revocado", "rechazado"}:
        consentimientos.actual = consentimientos.actual.model_copy(update={"estado": estado})
    elif estado == "vencido":
        consentimientos.actual = consentimientos.actual.model_copy(update={"vigente_hasta": AHORA})
    elif estado == "ajeno":
        consentimientos.actual = consentimientos.actual.model_copy(update={"cliente_id": uuid4()})
    else:
        cambios: dict[str, dict[str, object]] = {
            "alcance": {"alcances": ("open_data",)},
            "fuente": {"fuentes": ("open_data",)},
            "finalidad": {"finalidad": "mercadeo"},
            "datos": {"datos": ("movimientos",)},
            "actor": {"actor_id": uuid4()},
            "terminos": {"version_terminos": "desconocida"},
            "legado": {"actor_id": None, "fuentes": (), "datos": (), "version_terminos": None},
        }
        consentimientos.actual = consentimientos.actual.model_copy(update=cambios[estado])

    class FuenteNoAutorizada:
        nombre: Literal["open_finance"] = "open_finance"

        def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
            pytest.fail("No se debe consultar ninguna fuente sin el consentimiento requerido.")

    servicio = ConsultarPerfil(repo, consentimientos, (FuenteNoAutorizada(),), lambda: AHORA)
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


@pytest.mark.parametrize(
    ("escenario", "motivo"),
    [("no_disponible", "fuente_no_disponible"), ("vencido", "fuente_vencida")],
)
def test_fuentes_fallidas_aplican_default_trazable(
    repo: RepositorioPerfiles, escenario: str, motivo: str
) -> None:
    cliente_id = uuid4()
    fuente = FuenteMock("open_data", "no_disponible" if escenario == "no_disponible" else "vencido")
    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), fuente),
        lambda: AHORA,
    )
    perfil = servicio.generar(cliente_id, uuid4(), "corr-falla")
    assert perfil.fallback_used
    assert len(perfil.degradaciones) == 1
    assert perfil.degradaciones[0].motivo == motivo
    assert perfil.degradaciones[0].politica == "default"
    assert {fuente.version for fuente in perfil.fuentes} == {"mock-1", "default-1"}
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 1
        assert session.scalar(select(func.count()).select_from(ConsultaModel)) == 1


def test_revocacion_en_vuelo_bloquea_entrega_y_reutilizacion(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)

    class FuenteQueRevoca:
        nombre: Literal["open_data"] = "open_data"

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


def test_legacy_profile_remains_readable_after_hu48_upgrade(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    perfil_id = uuid4()
    referencia_id = uuid4()
    contenido = {
        "perfilId": str(perfil_id),
        "clienteId": str(cliente_id),
        "referenciaId": str(referencia_id),
        "version": 1,
        "modeloVersion": "perfil-mock-1",
        "score": "0.50",
        "nivel": "A",
        "consentimientoId": str(uuid4()),
        "consentimientoVersion": 1,
        "calculadoEn": AHORA.isoformat(),
        "vigenteHasta": (AHORA + timedelta(minutes=15)).isoformat(),
        "fuentes": [
            fuente.model_dump(mode="json", by_alias=True)
            for fuente in (
                FuenteMock("open_finance").consultar(cliente_id, AHORA),
                FuenteMock("open_data").consultar(cliente_id, AHORA),
            )
        ],
        "fallbackUsed": False,
    }
    hash_contenido = hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with Session(repo.engine) as session, session.begin():
        session.add(
            PerfilModel(
                perfil_id=perfil_id,
                cliente_id=cliente_id,
                version=1,
                contenido=contenido,
                hash_contenido=hash_contenido,
            )
        )

    perfil = repo.obtener(perfil_id, cliente_id)

    assert perfil.operation_id == referencia_id
    assert perfil.credito_id == str(referencia_id)
    assert perfil.regla.version == "legacy"
    assert perfil.hash_decision


def test_events_are_idempotent_durable_and_never_restore_an_old_version(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    actual = consentimientos.actual
    assert actual is not None
    revoke = EventoConsentimiento(
        evento_id=uuid4(),
        cliente_id=cliente_id,
        consentimiento_id=actual.consentimiento_id,
        version=2,
        tipo="ConsentimientoRevocado",
        estado="revocado",
        efectiva_en=AHORA,
        correlation_id="corr-revocacion",
    )
    ack = repo.aplicar_consentimiento(revoke)
    assert repo.aplicar_consentimiento(revoke) == ack
    old_grant = revoke.model_copy(
        update={
            "evento_id": uuid4(),
            "version": 1,
            "tipo": "ConsentimientoAutorizado",
            "estado": "autorizado",
        }
    )
    assert repo.aplicar_consentimiento(old_grant)["versionAplicada"] == 2
    restarted = RepositorioPerfiles(str(repo.engine.url))
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        restarted.validar_version_consentimiento(actual)
    renewed = actual.model_copy(update={"version": 3})
    restarted.validar_version_consentimiento(renewed)
    conflict = revoke.model_copy(update={"evento_id": uuid4()})
    with pytest.raises(ErrorPerfilamiento, match="conflicto_evento"):
        repo.aplicar_consentimiento(conflict)
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(EventoConsentimientoModel)) == 2


def test_event_invalidates_persisted_profile_even_with_stale_authoritative_response(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    servicio = ConsultarPerfil(
        repo, consentimientos, (FuenteMock("open_finance"), FuenteMock("open_data")), lambda: AHORA
    )
    perfil = servicio.generar(cliente_id, uuid4(), "antes")
    actual = consentimientos.actual
    assert actual is not None
    event = EventoConsentimiento(
        evento_id=uuid4(),
        cliente_id=cliente_id,
        consentimiento_id=actual.consentimiento_id,
        version=2,
        estado="revocado",
        tipo="ConsentimientoRevocado",
        efectiva_en=AHORA,
        correlation_id="revocacion",
    )
    repo.aplicar_consentimiento(event)
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.obtener(perfil.perfil_id, cliente_id, "despues")
    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        servicio.generar(cliente_id, uuid4(), "despues")
    with Session(repo.engine) as session:
        denied = list(
            session.scalars(
                select(ConsultaModel).where(
                    ConsultaModel.contenido["correlationId"].as_string() == "despues"
                )
            )
        )
        assert len(denied) == 2
        assert all(row.contenido["resultado"] == "consentimiento_no_vigente" for row in denied)


def test_internal_event_endpoint_authentication_validation_and_replay(
    repo: RepositorioPerfiles,
) -> None:
    app = create_app(Config(internal_api_token="interno-test", database_url=str(repo.engine.url)))
    client = app.test_client()
    path = "/v1/eventos/consentimientos"
    evento = EventoConsentimiento(
        evento_id=uuid4(),
        cliente_id=uuid4(),
        consentimiento_id=uuid4(),
        version=1,
        tipo="ConsentimientoRechazado",
        estado="rechazado",
        efectiva_en=AHORA,
        correlation_id="corr-evento",
    ).model_dump(mode="json", by_alias=True)
    headers = {"Authorization": "Bearer interno-test"}
    assert client.post(path, json=evento).status_code == 401
    for change in ({"version": True}, {"estado": "autorizado"}, {"datoExtra": "no"}):
        assert client.post(path, json={**evento, **change}, headers=headers).status_code == 400
    response = client.post(path, json=evento, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["versionAplicada"] == 1
    assert client.post(path, json=evento, headers=headers).get_json() == response.get_json()
    conflict = client.post(path, json={**evento, "correlationId": "otra"}, headers=headers)
    assert conflict.status_code == 409
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(EventoConsentimientoModel)) == 1
        assert session.scalar(select(func.count()).select_from(ConsultaModel)) == 1


def test_event_ack_and_audit_are_atomic(repo: RepositorioPerfiles) -> None:
    evento = EventoConsentimiento(
        evento_id=uuid4(),
        cliente_id=uuid4(),
        consentimiento_id=uuid4(),
        version=1,
        tipo="ConsentimientoRevocado",
        estado="revocado",
        efectiva_en=AHORA,
        correlation_id="atomicidad",
    )

    def fail_audit(*args: object) -> None:
        raise RuntimeError("auditoria_no_disponible")

    event.listen(ConsultaModel, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="auditoria_no_disponible"):
            repo.aplicar_consentimiento(evento)
    finally:
        event.remove(ConsultaModel, "before_insert", fail_audit)
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(EventoConsentimientoModel)) == 0
        assert session.scalar(select(func.count()).select_from(ConsultaModel)) == 0
    assert repo.aplicar_consentimiento(evento)["eventoId"] == str(evento.evento_id)


def test_concurrent_duplicates_produce_one_durable_ack(repo: RepositorioPerfiles) -> None:
    evento = EventoConsentimiento(
        evento_id=uuid4(),
        cliente_id=uuid4(),
        consentimiento_id=uuid4(),
        version=1,
        tipo="ConsentimientoRevocado",
        estado="revocado",
        efectiva_en=AHORA,
        correlation_id="concurrente",
    )
    barrier = Barrier(2)

    def wait_for_duplicate(*args: object) -> None:
        barrier.wait(timeout=5)

    event.listen(EventoConsentimientoModel, "before_insert", wait_for_duplicate)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            acks = list(executor.map(repo.aplicar_consentimiento, (evento, evento)))
        assert acks[0] == acks[1]
    finally:
        event.remove(EventoConsentimientoModel, "before_insert", wait_for_duplicate)
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(EventoConsentimientoModel)) == 1
        assert session.scalar(select(func.count()).select_from(ConsultaModel)) == 1


def test_risk_is_idempotent_by_operation_and_reconstructable(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    referencia_id = uuid4()
    operation_id = uuid4()
    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )

    perfil = servicio.generar(
        cliente_id,
        referencia_id,
        "corr-hu48",
        operation_id=operation_id,
        credito_id="HIP-2026-001",
    )
    reintento = servicio.generar(
        cliente_id,
        referencia_id,
        "corr-hu48-reintento",
        operation_id=operation_id,
        credito_id="HIP-2026-001",
    )

    assert reintento == perfil
    assert perfil.operation_id == operation_id
    assert perfil.credito_id == "HIP-2026-001"
    evidencia = {
        "clienteId": str(perfil.cliente_id),
        "creditoId": perfil.credito_id,
        "operationId": str(perfil.operation_id),
        "consentimientoId": str(perfil.consentimiento_id),
        "consentimientoVersion": perfil.consentimiento_version,
        "fuentes": [fuente.model_dump(mode="json", by_alias=True) for fuente in perfil.fuentes],
        "variables": [
            variable.model_dump(mode="json", by_alias=True) for variable in perfil.variables
        ],
        "variablesExcluidas": [
            variable.model_dump(mode="json", by_alias=True)
            for variable in perfil.variables_excluidas
        ],
        "regla": perfil.regla.model_dump(mode="json", by_alias=True),
        "degradaciones": [
            degradacion.model_dump(mode="json", by_alias=True)
            for degradacion in perfil.degradaciones
        ],
        "score": str(perfil.score),
    }
    assert (
        perfil.hash_decision
        == hashlib.sha256(
            json.dumps(evidencia, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 1

    with pytest.raises(ErrorPerfilamiento, match="conflicto_idempotencia"):
        servicio.generar(
            cliente_id,
            uuid4(),
            "corr-hu48-conflicto",
            operation_id=operation_id,
            credito_id="HIP-OTRO",
        )


def test_concurrent_retries_persist_one_risk(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    referencia_id = uuid4()
    operation_id = uuid4()
    barrera = Barrier(2)

    class FuenteConcurrente:
        nombre: Literal["open_finance"] = "open_finance"

        def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
            barrera.wait(timeout=5)
            return FuenteMock("open_finance").consultar(cliente_id, ahora)

    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteConcurrente(), FuenteMock("open_data")),
        lambda: AHORA,
    )

    def ejecutar(correlation_id: str) -> Perfil:
        return servicio.generar(
            cliente_id,
            referencia_id,
            correlation_id,
            operation_id=operation_id,
            credito_id="HIP-CONCURRENTE",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        perfiles = list(executor.map(ejecutar, ("corr-a", "corr-b")))

    assert perfiles[0] == perfiles[1]
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(PerfilModel)) == 1


def test_failed_source_uses_recent_authorized_cache(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    disponible = ConsultarPerfil(
        repo,
        consentimientos,
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    disponible.generar(cliente_id, uuid4(), "corr-cache-base")
    degradado = ConsultarPerfil(
        repo,
        consentimientos,
        (FuenteMock("open_finance", "no_disponible"), FuenteMock("open_data")),
        lambda: AHORA + timedelta(minutes=1),
    ).generar(cliente_id, uuid4(), "corr-cache")

    assert degradado.fallback_used
    assert degradado.degradaciones[0].politica == "cache"
    assert degradado.degradaciones[0].version_politica == "degradacion-1"
    assert any(fuente.version == "mock-1:cache" for fuente in degradado.fuentes)


def test_cache_from_previous_consent_is_not_reused(
    repo: RepositorioPerfiles,
) -> None:
    cliente_id = uuid4()
    consentimientos = ConsentimientosPrueba(cliente_id)
    disponible = ConsultarPerfil(
        repo,
        consentimientos,
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    disponible.generar(cliente_id, uuid4(), "corr-consent-cache-base")
    assert consentimientos.actual is not None
    consentimientos.actual = consentimientos.actual.model_copy(
        update={"consentimiento_id": uuid4(), "version": 2}
    )

    degradado = ConsultarPerfil(
        repo,
        consentimientos,
        (FuenteMock("open_finance", "no_disponible"), FuenteMock("open_data")),
        lambda: AHORA + timedelta(minutes=1),
    ).generar(cliente_id, uuid4(), "corr-new-consent")

    assert degradado.degradaciones[0].politica == "default"
    assert any(fuente.version == "default-1" for fuente in degradado.fuentes)


def test_hu48_application_latency_baseline(repo: RepositorioPerfiles) -> None:
    cliente_id = uuid4()
    servicio = ConsultarPerfil(
        repo,
        ConsentimientosPrueba(cliente_id),
        (FuenteMock("open_finance"), FuenteMock("open_data")),
        lambda: AHORA,
    )
    latencias_ms = []
    for indice in range(200):
        operacion = uuid4()
        medida = perf_counter()
        servicio.generar(
            cliente_id,
            uuid4(),
            f"corr-capacidad-{indice}",
            operation_id=operacion,
            credito_id=f"HIP-{indice:06}",
        )
        latencias_ms.append((perf_counter() - medida) * 1000)
    ordenadas = sorted(latencias_ms)
    p95 = ordenadas[189]
    p99 = ordenadas[197]

    assert p95 <= 400
    assert p99 <= 800
