from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app as create_bff
from bff_web.config import Config as BffConfig
from bff_web.domain.auth import User
from bff_web.infrastructure.development_credentials import DevelopmentCredential
from bff_web.infrastructure.passwords import hash_password
from cotizacion.app import create_app as create_quotes
from cotizacion.config import Config as QuoteConfig
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import InstantaneaDecisionModel
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from flask import Flask
from identidad.app import Config as IdentityConfig
from identidad.app import create_app as create_identity
from perfilamiento.app import Config as ProfileConfig
from perfilamiento.app import create_app as create_profiles
from perfilamiento.persistence import ConsultaModel, RepositorioPerfiles
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

TOKEN = "token-interno-solo-pruebas"
PASSWORD = "PruebaLocal2026!"
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running(app: Flask) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def credential(user_id: UUID, email: str) -> DevelopmentCredential:
    return DevelopmentCredential(
        User(user_id, "Cliente de prueba", email, "cliente", "cc", "1234567890", "+573001234567"),
        hash_password(PASSWORD),
    )


@pytest.mark.parametrize("scenario", ["disponible", "no_disponible", "vencido"])
def test_hu11_por_http_de_extremo_a_extremo(tmp_path: Path, scenario: str) -> None:
    identity = create_identity(IdentityConfig(TOKEN, f"sqlite:///{tmp_path / 'identity.db'}"))
    assert identity.test_cli_runner().invoke(args=["init-db"]).exit_code == 0
    quote_db = f"sqlite:///{tmp_path / 'quotes.db'}"
    migration = AlembicConfig(str(ROOT / "services" / "cotizacion" / "alembic.ini"))
    migration.set_main_option("sqlalchemy.url", quote_db)
    command.upgrade(migration, "head")
    with ExitStack() as stack:
        identity_url = stack.enter_context(running(identity))
        profiles = create_profiles(
            ProfileConfig(
                TOKEN,
                f"sqlite:///{tmp_path / 'profiles.db'}",
                identity_url,
                mock_escenario=(
                    "no_disponible"
                    if scenario == "no_disponible"
                    else "vencido"
                    if scenario == "vencido"
                    else "disponible"
                ),
            )
        )
        assert profiles.test_cli_runner().invoke(args=["init-db"]).exit_code == 0
        profile_url = stack.enter_context(running(profiles))
        quotes = create_quotes(
            QuoteConfig(
                database_url=quote_db,
                internal_api_token=TOKEN,
                perfilamiento_url=profile_url,
                perfilamiento_habilitado=True,
            )
        )
        quote_url = stack.enter_context(running(quotes))
        user_id = uuid4()
        bff = create_bff(
            BffConfig(
                credentials=(
                    credential(user_id, "cliente@solventa.co"),
                    credential(uuid4(), "otro@solventa.co"),
                ),
                session_cookie_secure=False,
                quote_service_url=quote_url,
                identity_service_url=identity_url,
                internal_api_token=TOKEN,
            )
        )
        client = bff.test_client()
        consent_url = "/v1/consentimientos/perfilamiento"
        assert client.get(consent_url).status_code == 401
        assert client.put(consent_url, json={"autorizado": True, "version": 0}).status_code == 401
        login = client.post(
            "/v1/sesiones", json={"correo": "cliente@solventa.co", "contrasena": PASSWORD}
        )
        assert login.status_code == 200
        assert client.get(consent_url).status_code == 404
        payload = {"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}}
        headers = {"Idempotency-Key": str(uuid4()), "X-Correlation-ID": str(uuid4())}
        denied = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
        assert denied.status_code == 403
        assert denied.get_json()["code"] == "consentimiento_ausente"
        assert denied.get_json()["correlationId"] == headers["X-Correlation-ID"]
        assert (
            client.put(
                consent_url, json={"autorizado": True, "version": 0, "clienteId": str(uuid4())}
            ).status_code
            == 400
        )
        authorized = client.put(consent_url, json={"autorizado": True, "version": 0})
        assert authorized.status_code == 200
        assert authorized.get_json()["clienteId"] == str(user_id)
        created = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
        if scenario != "disponible":
            assert created.status_code == (503 if scenario == "no_disponible" else 422)
            assert "perfilId" not in created.get_json()
            assert created.get_json()["code"] == (
                "perfilamiento_no_disponible" if scenario == "no_disponible" else "perfil_vencido"
            )
            return
        assert created.status_code == 201
        request_id = created.get_json()["id"]
        profile_path = f"/v1/solicitudes-cotizacion/{request_id}/perfilamiento"
        response = client.get(profile_path, headers=headers)
        assert response.status_code == 200
        profile = response.get_json()
        assert profile["solicitudId"] == request_id
        assert profile["score"] == "0.50"
        assert profile["nivel"] == "A"
        assert profile["consentimientoVersion"] == 1
        assert all(source["simulada"] for source in profile["fuentes"])
        assert response.headers["Cache-Control"] == "no-store"
        assert "clienteId" not in profile and "referenciaId" not in profile
        repo: RepositorioCotizacionesSqlAlchemy = quotes.extensions["repositorio_cotizaciones"]
        record = repo.obtener_solicitud(request_id, user_id)
        assert record is not None
        assert record.cotizacion.instantanea is not None
        evidence = record.cotizacion.instantanea
        assert evidence.perfil_id == profile["perfilId"]
        assert evidence.contenido["perfil"]["evidencia"]["consentimientoVersion"] == 1
        assert evidence.contenido["perfil"]["evidencia"]["referenciaId"] == str(
            record.cotizacion.cotizacion_id
        )
        offers_url = f"/v1/solicitudes-cotizacion/{request_id}/ofertas"
        offers = client.get(offers_url)
        assert offers.status_code == 200
        assert offers.get_json()["tasaAplicada"] == "Simulada (Riesgo A)"
        duplicate = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
        assert duplicate.get_json()["id"] == request_id
        assert client.get(profile_path).get_json()["perfilId"] == profile["perfilId"]
        other = bff.test_client()
        assert (
            other.post(
                "/v1/sesiones", json={"correo": "otro@solventa.co", "contrasena": PASSWORD}
            ).status_code
            == 200
        )
        assert other.get(profile_path).status_code == 404
        assert client.put(consent_url, json={"autorizado": False, "version": 0}).status_code == 409
        revoked = client.put(consent_url, json={"autorizado": False, "version": 1})
        assert revoked.status_code == 200 and revoked.get_json()["version"] == 2
        assert client.get(profile_path).status_code == 403
        assert client.get(offers_url).status_code == 403
        selected = offers.get_json()["ofertas"][0]
        assert (
            client.put(
                f"/v1/solicitudes-cotizacion/{request_id}/seleccion",
                json={"ofertaId": selected["id"], "version": selected["version"]},
                headers={"Idempotency-Key": str(uuid4())},
            ).status_code
            == 403
        )
        assert client.put(consent_url, json={"autorizado": True, "version": 2}).status_code == 200
        assert client.get(profile_path).status_code == 403
        assert client.get(offers_url).status_code == 403
        profile_repo: RepositorioPerfiles = profiles.extensions["perfiles"]
        with Session(profile_repo.engine) as session:
            audit = session.scalars(select(ConsultaModel)).all()
            assert any(
                entry.contenido["correlationId"] == headers["X-Correlation-ID"] for entry in audit
            )
        # La evidencia historica no se modifica al revocar ni al autorizar de nuevo.
        with crear_sesiones(quote_db)() as session:
            snapshot = session.get(InstantaneaDecisionModel, record.cotizacion.cotizacion_id)
            assert snapshot is not None
            assert snapshot.hash_contenido == evidence.hash_contenido
