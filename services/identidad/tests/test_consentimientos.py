from pathlib import Path
from uuid import UUID, uuid4

import pytest
from flask.testing import FlaskClient
from identidad.app import Config, create_app
from identidad.persistence import EventoConsentimientoModel, RepositorioConsentimientos
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture
def client(tmp_path: Path) -> FlaskClient:
    app = create_app(Config("prueba-interna", f"sqlite:///{tmp_path / 'identidad.db'}"))
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer prueba-interna"
    return client


def test_autorizacion_versionada_y_revocacion_durables(client: FlaskClient) -> None:
    cliente_id = uuid4()
    url = f"/v1/clientes/{cliente_id}/consentimiento-perfilamiento"
    assert client.get(url).status_code == 404
    granted = client.put(url, json={"autorizado": True, "version": 0})
    assert granted.status_code == 200
    body = granted.get_json()
    assert body["estado"] == "autorizado"
    assert body["version"] == 1
    assert body["finalidad"] == "cotizacion"
    assert body["alcances"] == ["open_finance", "open_data"]
    conflict = client.put(url, json={"autorizado": False, "version": 0})
    assert conflict.status_code == 409
    revoked = client.put(url, json={"autorizado": False, "version": 1})
    assert revoked.status_code == 200
    assert revoked.get_json()["version"] == 2
    assert revoked.get_json()["estado"] == "revocado"
    assert revoked.get_json()["consentimientoId"] == body["consentimientoId"]
    repo: RepositorioConsentimientos = client.application.extensions["consentimientos"]
    actual = repo.obtener(cliente_id)
    assert actual is not None and actual.version == 2
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(EventoConsentimientoModel)) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"autorizado": "true", "version": 0},
        {"autorizado": True, "version": -1},
        {"autorizado": True, "version": 0, "clienteId": str(uuid4())},
        {"autorizado": True, "version": True},
        {},
    ],
)
def test_rechaza_autorizacion_implicita_o_datos_invalidos(
    client: FlaskClient, body: dict[str, object]
) -> None:
    url = f"/v1/clientes/{uuid4()}/consentimiento-perfilamiento"
    assert client.put(url, json=body).status_code == 400


def test_exige_identidad_de_servicio_y_no_comparte_consentimiento(client: FlaskClient) -> None:
    cliente_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    url = f"/v1/clientes/{cliente_id}/consentimiento-perfilamiento"
    assert client.get(url, headers={"Authorization": ""}).status_code == 401
    assert client.get("/health", headers={"Authorization": ""}).status_code == 200
    assert client.put(url, json={"autorizado": True, "version": 0}).status_code == 200
    assert client.get(f"/v1/clientes/{uuid4()}/consentimiento-perfilamiento").status_code == 404
    assert client.get(url).headers["Cache-Control"] == "no-store"
