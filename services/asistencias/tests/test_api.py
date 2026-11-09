from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from uuid import UUID, uuid4

import asistencias.repository as repository_module
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from asistencias.app import create_app
from asistencias.authorization import ResourceAuthorizer
from asistencias.domain import Config, Problem
from asistencias.models import Assistance, Base, LocationAudit, Provider
from flask import Flask
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

TOKEN = "asistencias-interno"
CLIENT_ID = uuid4()
POLICY_ID = uuid4()
CLAIM_ID = uuid4()
PROVIDER_ID = UUID("11111111-1111-4111-8111-111111111111")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Cliente-ID": str(CLIENT_ID),
}


class FakeAuthorizer(ResourceAuthorizer):
    def __init__(self) -> None:
        self.policy_allowed = True
        self.claim_allowed = True
        self.calls: list[tuple[str, UUID, UUID]] = []
        self.failure: Problem | None = None

    def authorized_policy(self, client_id: UUID, policy_id: UUID, correlation_id: UUID) -> bool:
        if self.failure:
            raise self.failure
        self.calls.append(("poliza", client_id, policy_id))
        return self.policy_allowed

    def authorized_claim(self, client_id: UUID, claim_id: UUID, correlation_id: UUID) -> bool:
        if self.failure:
            raise self.failure
        self.calls.append(("siniestro", client_id, claim_id))
        return self.claim_allowed


@pytest.fixture
def authorizer() -> FakeAuthorizer:
    return FakeAuthorizer()


@pytest.fixture
def app(tmp_path: Path, authorizer: FakeAuthorizer) -> Flask:
    url = f"sqlite:///{tmp_path / 'asistencias.db'}"
    migration = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    migration.set_main_option("sqlalchemy.url", url)
    command.upgrade(migration, "head")
    return create_app(
        Config(url, TOKEN, "http://polizas.test", "http://siniestros.test"),
        authorizer,
    )


def location(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "latitud": 4.711,
        "longitud": -74.0721,
        "origen": "permiso",
        "confirmada": True,
        "confirmadaEn": datetime.now(UTC).isoformat(),
    }
    return {**base, **changes}


def assistance(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "prestadorId": str(PROVIDER_ID),
        "tipoAsistencia": "grua",
        "ubicacion": location(),
        "polizaId": str(POLICY_ID),
    }
    return {**base, **changes}


def register(app: Flask, operation: UUID, payload: dict[str, object]) -> TestResponse:
    return app.test_client().post(
        "/v1/asistencias",
        headers={**HEADERS, "Idempotency-Key": str(operation)},
        json=payload,
    )


def test_consulta_prestadores_cercanos_sin_exponer_coordenadas(app: Flask) -> None:
    response = app.test_client().post(
        "/v1/prestadores/consulta",
        headers=HEADERS,
        json={"ubicacion": location(), "tipoServicio": "grua", "radioKm": 10},
    )

    assert response.status_code == 200
    providers = response.get_json()["prestadores"]
    assert providers[0] == {
        "id": str(PROVIDER_ID),
        "nombre": "Red Solventa Gruas Centro",
        "tipoServicio": "grua",
        "distanciaKm": 0.0,
    }
    assert all(
        set(provider) == {"id", "nombre", "tipoServicio", "distanciaKm"} for provider in providers
    )
    with Session(app.extensions["asistencias_engine"]) as session:
        audit = session.scalar(select(LocationAudit))
        assert audit is not None
        assert audit.cliente_id == str(CLIENT_ID)
        assert audit.proposito == "consulta_prestadores"


def test_consulta_manual_y_radio_excluyen_prestadores_lejanos(app: Flask) -> None:
    response = app.test_client().post(
        "/v1/prestadores/consulta",
        headers=HEADERS,
        json={"ubicacion": location(origen="manual"), "radioKm": 2},
    )

    assert response.status_code == 200
    assert [provider["id"] for provider in response.get_json()["prestadores"]] == [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]


def test_registra_asistencia_con_poliza_autorizada(app: Flask, authorizer: FakeAuthorizer) -> None:
    response = register(app, uuid4(), assistance())

    assert response.status_code == 201
    assert response.get_json()["estado"] == "registrada"
    assert response.get_json()["tipoAsistencia"] == "grua"
    assert authorizer.calls == [("poliza", CLIENT_ID, POLICY_ID)]
    with Session(app.extensions["asistencias_engine"]) as session:
        stored = session.scalar(select(Assistance))
        assert stored is not None
        assert float(stored.latitud) == 4.711
        assert stored.origen_ubicacion == "permiso"
        assert (
            session.scalar(
                select(func.count())
                .select_from(LocationAudit)
                .where(LocationAudit.proposito == "registro_asistencia")
            )
            == 1
        )


def test_registra_asistencia_con_siniestro_autorizado(
    app: Flask, authorizer: FakeAuthorizer
) -> None:
    payload = assistance(polizaId=None, siniestroId=str(CLAIM_ID))

    response = register(app, uuid4(), payload)

    assert response.status_code == 201
    assert authorizer.calls == [("siniestro", CLIENT_ID, CLAIM_ID)]


def test_reintento_recupera_misma_asistencia_sin_reautorizar(
    app: Flask, authorizer: FakeAuthorizer
) -> None:
    operation = uuid4()
    payload = assistance()

    first = register(app, operation, payload)
    replay = register(app, operation, payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.get_json() == first.get_json()
    assert len(authorizer.calls) == 1
    with Session(app.extensions["asistencias_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Assistance)) == 1


def test_reintento_recupera_resultado_aunque_ubicacion_ya_no_este_vigente(
    app: Flask, authorizer: FakeAuthorizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = uuid4()
    payload = assistance()
    first = register(app, operation, payload)
    authorizer.failure = Problem(503, "servicio_no_disponible")
    future = datetime.now(UTC) + timedelta(days=2)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "FutureDateTime":
            return cls.fromtimestamp(future.timestamp(), tz or UTC)

    monkeypatch.setattr(repository_module, "datetime", FutureDateTime)

    replay = register(app, operation, payload)

    assert replay.status_code == 200
    assert replay.get_json() == first.get_json()
    assert len(authorizer.calls) == 1


def test_misma_operacion_con_otro_cuerpo_es_conflicto(app: Flask) -> None:
    operation = uuid4()
    assert register(app, operation, assistance()).status_code == 201

    conflict = register(app, operation, assistance(ubicacion=location(latitud=4.72)))

    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "idempotencia_conflictiva"


def test_rechaza_prestador_no_disponible_sin_confirmar_registro(app: Flask) -> None:
    with Session(app.extensions["asistencias_engine"]) as session, session.begin():
        session.execute(
            update(Provider).where(Provider.id == str(PROVIDER_ID)).values(disponible=False)
        )

    response = register(app, uuid4(), assistance())

    assert response.status_code == 409
    assert response.get_json()["code"] == "prestador_no_disponible"
    with Session(app.extensions["asistencias_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Assistance)) == 0


def test_recurso_ajeno_no_registra_asistencia(app: Flask, authorizer: FakeAuthorizer) -> None:
    authorizer.policy_allowed = False

    response = register(app, uuid4(), assistance())

    assert response.status_code == 404
    assert response.get_json()["code"] == "recurso_no_autorizado"
    with Session(app.extensions["asistencias_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Assistance)) == 0


def test_fallo_de_autorizacion_no_simula_registro(app: Flask, authorizer: FakeAuthorizer) -> None:
    authorizer.failure = Problem(503, "servicio_no_disponible")

    response = register(app, uuid4(), assistance())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    with Session(app.extensions["asistencias_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Assistance)) == 0


@pytest.mark.parametrize(
    "payload",
    [
        assistance(ubicacion=location(confirmada=False)),
        assistance(
            ubicacion=location(confirmadaEn=(datetime.now(UTC) - timedelta(days=2)).isoformat())
        ),
        assistance(polizaId=None, siniestroId=None),
        assistance(siniestroId=str(CLAIM_ID)),
        {**assistance(), "clienteId": str(CLIENT_ID)},
    ],
)
def test_rechaza_ubicacion_o_recurso_invalido(app: Flask, payload: dict[str, object]) -> None:
    response = register(app, uuid4(), payload)

    assert response.status_code in {400, 422}
    with Session(app.extensions["asistencias_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Assistance)) == 0


def test_requiere_canal_interno_y_cliente(app: Flask) -> None:
    missing_token = app.test_client().post(
        "/v1/prestadores/consulta",
        headers={"X-Cliente-ID": str(CLIENT_ID)},
        json={"ubicacion": location()},
    )
    missing_client = app.test_client().post(
        "/v1/prestadores/consulta",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"ubicacion": location()},
    )

    assert missing_token.status_code == 401
    assert missing_client.status_code == 400


def test_migracion_coincide_con_modelos(app: Flask) -> None:
    with app.extensions["asistencias_engine"].connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
