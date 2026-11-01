import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import jwt
import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cotizacion.app import create_app as create_quote_app
from cotizacion.config import Config as QuoteConfig
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    SagaEmisionModel,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
PATH = "/v1/clientes/me/dashboard"
INTERNAL_TOKEN = "dashboard-test-service-credential"
ISSUER = "https://identity.dashboard.test"
USER = User(
    UUID("2923bf51-1899-4a3e-9547-b8ccb9c2c84f"),
    "Cliente",
    "ana@solventa.co",
    "cliente",
    phone="+573001234567",
    birth_date=date(1990, 1, 1),
)
Runtime = tuple[FlaskClient, FlaskClient, sessionmaker[Session]]


class IdentityGatewayStub:
    def get_client(self, client_id: UUID, correlation_id: str) -> User | None:
        return User(
            client_id,
            USER.name,
            USER.email,
            USER.role,
            phone=USER.phone,
            birth_date=USER.birth_date,
        )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sign_token(key: rsa.RSAPrivateKey, user: User = USER) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.user_id),
            "iss": ISSUER,
            "aud": "solventa-api",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": str(uuid4()),
            "client_id": "web",
            "scope": "cotizaciones",
            "nombre": user.name,
            "correo": user.email,
            "rol": user.role,
        },
        key,
        algorithm="RS256",
        headers={"kid": "dashboard-test"},
    )


@pytest.fixture(scope="session")
def keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(keypair: rsa.RSAPrivateKey) -> str:
    return sign_token(keypair)


@pytest.fixture
def runtime(
    tmp_path: Path, keypair: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Runtime]:
    database_url = f"sqlite:///{tmp_path / 'dashboard.db'}"
    migration_config = AlembicConfig(str(ROOT / "services" / "cotizacion" / "alembic.ini"))
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, _: object) -> None:
        from sqlite3 import Connection

        assert isinstance(connection, Connection)
        connection.execute("PRAGMA foreign_keys=ON")

    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
    monkeypatch.setattr("cotizacion.app.crear_sesiones", lambda *args: factory)
    quote_app = create_quote_app(
        QuoteConfig(database_url=database_url, internal_api_token=INTERNAL_TOKEN)
    )
    server = make_server("127.0.0.1", 0, quote_app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    bff = create_app(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            quote_service_url=f"http://127.0.0.1:{server.server_port}",
            internal_api_token=INTERNAL_TOKEN,
        ),
        identity_gateway=IdentityGatewayStub(),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "web"
        ),
    )
    try:
        yield bff.test_client(), quote_app.test_client(), factory
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        engine.dispose()


def create_paid_request(
    client: FlaskClient, token: str, product: str, payment_status: int = 200
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    start = date.today() + timedelta(days=30)
    product_data = {
        "automotive": {"vehiclePlate": "ABC123"},
        "life": {"birthDate": "1990-01-01"},
        "device-protection": {
            "deviceType": "phone",
            "brandModel": "Test",
            "purchaseValue": "3000000",
        },
        "payment-protection": {"creditType": "libre", "monthlyPayment": "500000"},
        "mortgage-life": {"birthDate": "1990-01-01", "mortgageBalance": "100000000"},
        "travel": {
            "destinationCountryCode": "ES",
            "departureDate": start.isoformat(),
            "returnDate": (start + timedelta(days=7)).isoformat(),
        },
        "parametric": {
            "airline": "Test",
            "flightNumber": "AV123",
            "flightDate": start.isoformat(),
        },
        "parametric-weather": {
            "location": "Medellin",
            "weatherEvent": "heavy-rain",
            "startDate": start.isoformat(),
            "endDate": (start + timedelta(days=7)).isoformat(),
            "insuredAmount": "10000000",
        },
    }[product]
    response = client.post(
        "/v1/solicitudes-cotizacion",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"productoId": product, "datosProducto": product_data},
    )
    assert response.status_code == 201, response.get_json()
    request_id = response.get_json()["id"]
    base = f"/v1/solicitudes-cotizacion/{request_id}"
    response = client.get(base + "/ofertas", headers=bearer(token))
    assert response.status_code == 200, response.get_json()
    offer = response.get_json()["ofertas"][0]
    response = client.put(
        base + "/seleccion",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"ofertaId": offer["id"], "version": offer["version"]},
    )
    assert response.status_code == 200, response.get_json()
    selection = response.get_json()
    response = client.post(
        base + "/suscripcion", headers={**bearer(token), "Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 201, response.get_json()
    subscription = response.get_json()
    response = client.post(
        base + "/pago-simulado",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert response.status_code == payment_status, response.get_json()
    return selection, subscription, response.get_json()


def validate_contract(payload: dict[str, object]) -> None:
    document = yaml.safe_load((ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text())
    schema = {
        "$ref": "#/components/schemas/Dashboard",
        "components": document["components"],
    }
    OAS31Validator(schema, format_checker=OAS31Validator.FORMAT_CHECKER).validate(payload)


@pytest.mark.parametrize(
    ("product", "period"),
    [
        ("automotive", "mensual"),
        ("travel", "por_viaje"),
        ("parametric", "por_vuelo"),
        ("parametric-weather", "por_periodo"),
    ],
)
def test_dashboard_reads_main_flow_with_foreign_keys(
    runtime: Runtime, token: str, keypair: rsa.RSAPrivateKey, product: str, period: str
) -> None:
    client, _, factory = runtime
    selection, subscription, payment = create_paid_request(client, token, product)
    other_user = User(uuid4(), "Otro Cliente", "otro@solventa.co", "cliente")
    other_token = sign_token(keypair, user=other_user)
    create_paid_request(client, other_token, "automotive")
    with factory.begin() as session:
        quote_id = session.scalar(
            select(CotizacionModel.cotizacion_id).where(CotizacionModel.cliente_id == USER.user_id)
        )
        session.execute(
            update(CotizacionModel)
            .where(CotizacionModel.cotizacion_id == quote_id)
            .values(valida_hasta=datetime(2020, 1, 1, tzinfo=UTC))
        )
        session.execute(
            update(SagaEmisionModel)
            .where(SagaEmisionModel.suscripcion_id == UUID(str(subscription["id"])))
            .values(estado="emitida", version=3)
        )
    response = client.get(PATH, headers=bearer(token))
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    dashboard = response.get_json()
    validate_contract(dashboard)
    assert len(dashboard["cotizaciones"]) == len(dashboard["suscripciones"]) == 1
    assert len(dashboard["pagos"]) == 1
    quote = dashboard["cotizaciones"][0]
    assert quote["id"] == str(quote_id)
    assert quote["productoId"] == product
    assert quote["solicitudId"] == selection["solicitudId"]
    assert quote["estado"] == "CALCULADA"
    assert quote["validaHasta"] == "2020-01-01T00:00:00Z"
    assert quote["seleccion"] == {
        key: selection[key]
        for key in ("ofertaId", "nombreOferta", "prima", "periodicidad", "seleccionadaEn")
    }
    assert dashboard["suscripciones"][0]["estado"] == subscription["estado"]
    assert dashboard["suscripciones"][0]["saga"]["estado"] == "emitida"
    assert dashboard["pagos"][0] == {
        key: payment[key]
        for key in (
            "id",
            "solicitudId",
            "suscripcionId",
            "estado",
            "simulada",
            "importe",
            "periodicidad",
            "confirmadaEn",
        )
    }
    assert dashboard["pagos"][0]["periodicidad"] == period
    assert (
        dashboard["polizas"] == dashboard["notificaciones"] == {"disponibilidad": "no_implementado"}
    )
    other = client.get(PATH, headers=bearer(other_token)).get_json()
    assert other["cotizaciones"][0]["id"] != quote["id"]
    assert other["pagos"][0]["id"] != payment["id"]


def test_genuine_empty_dashboard(runtime: Runtime, token: str) -> None:
    client, _, _ = runtime
    response = client.get(PATH, headers=bearer(token))
    assert response.status_code == 200
    assert response.get_json() == {
        "cotizaciones": [],
        "suscripciones": [],
        "pagos": [],
        "polizas": {"disponibilidad": "no_implementado"},
        "notificaciones": {"disponibilidad": "no_implementado"},
    }
    validate_contract(response.get_json())


@pytest.mark.parametrize("credential", [None, "invalid"])
def test_dashboard_requires_jwt(runtime: Runtime, credential: str | None) -> None:
    client, _, _ = runtime
    response = client.get(PATH, headers=bearer(credential) if credential else {})
    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"


@pytest.mark.parametrize("query", ["clienteId=otro", "page=1"])
def test_public_dashboard_rejects_query(runtime: Runtime, token: str, query: str) -> None:
    client, _, _ = runtime
    response = client.get(PATH + "?" + query, headers=bearer(token))
    assert response.status_code == 400


def test_public_dashboard_rejects_body(runtime: Runtime, token: str) -> None:
    client, _, _ = runtime
    assert client.get(PATH, headers=bearer(token), json={}).status_code == 400


@pytest.mark.parametrize("credential", [None, "invalid", "jwt"])
def test_internal_dashboard_requires_service_credential(
    runtime: Runtime, token: str, credential: str | None
) -> None:
    _, client, _ = runtime
    headers = {} if credential is None else bearer(token if credential == "jwt" else credential)
    response = client.get(
        "/v1/dashboard", query_string={"clienteId": str(USER.user_id)}, headers=headers
    )
    assert response.status_code == 401


@pytest.mark.parametrize("user_id", [None, "invalid"])
def test_internal_dashboard_requires_uuid(runtime: Runtime, user_id: str | None) -> None:
    _, client, _ = runtime
    response = client.get(
        "/v1/dashboard",
        query_string={} if user_id is None else {"clienteId": user_id},
        headers=bearer(INTERNAL_TOKEN),
    )
    assert response.status_code == 400


def test_database_failure_is_not_empty_success(runtime: Runtime, token: str) -> None:
    client, _, factory = runtime
    with factory.begin() as session:
        session.execute(text("DROP TABLE pagos_simulados"))
    response = client.get(PATH, headers=bearer(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert "cotizaciones" not in response.get_json()


def test_internal_dashboard_fails_closed_without_configuration(tmp_path: Path) -> None:
    client = create_quote_app(
        QuoteConfig(database_url=f"sqlite:///{tmp_path / 'unused.db'}")
    ).test_client()
    assert (
        client.get(
            "/v1/dashboard",
            query_string={"clienteId": str(USER.user_id)},
            headers=bearer(INTERNAL_TOKEN),
        ).status_code
        == 503
    )


def test_quote_without_selection_and_legacy_quote_are_real_records(
    runtime: Runtime, token: str
) -> None:
    client, _, factory = runtime
    response = client.post(
        "/v1/solicitudes-cotizacion",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}},
    )
    assert response.status_code == 201
    created = response.get_json()
    with factory.begin() as session:
        original = session.scalar(select(CotizacionModel))
        assert original is not None
        legacy = CotizacionModel(
            cotizacion_id=uuid4(),
            cliente_id=USER.user_id,
            producto_id=uuid4(),
            estado="CALCULADA",
            moneda="USD",
            prima_estimada="12.34",
            creada_en=datetime(2020, 1, 1, tzinfo=UTC),
            valida_hasta=datetime(2020, 1, 15, tzinfo=UTC),
        )
        session.add(legacy)
        expected_premium = {"monto": str(original.prima_estimada), "moneda": original.moneda}
        legacy_id = str(legacy.cotizacion_id)
        legacy_product = str(legacy.producto_id)
    response = client.get(PATH, headers=bearer(token))
    assert response.status_code == 200, response.get_json()
    dashboard = response.get_json()
    validate_contract(dashboard)
    recent, historical = dashboard["cotizaciones"]
    assert recent["solicitudId"] == created["id"]
    assert recent["creadaEn"] == created["creadaEn"]
    assert recent["primaEstimada"] == expected_premium
    assert recent["seleccion"] is None
    assert historical["id"] == legacy_id
    assert historical["solicitudId"] is None
    assert historical["productoId"] == legacy_product
    assert historical["primaEstimada"] == {"monto": "12.34", "moneda": "USD"}
    assert historical["seleccion"] is None
    assert dashboard["suscripciones"] == dashboard["pagos"] == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE selecciones_oferta SET periodicidad = 'desconocida'",
        "UPDATE pagos_simulados SET monto = 0",
        "DELETE FROM sagas_emision",
    ],
)
def test_invalid_persisted_data_is_not_success(
    runtime: Runtime, token: str, statement: str
) -> None:
    client, _, factory = runtime
    create_paid_request(client, token, "automotive")
    with factory.begin() as session:
        session.execute(text(statement))
    response = client.get(PATH, headers=bearer(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert "cotizaciones" not in response.get_json()


def test_dashboard_is_one_read_and_does_not_generate_profiles(
    runtime: Runtime, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, quote_client, factory = runtime
    create_paid_request(client, token, "automotive")
    for extension in (
        "proveedor_perfil",
        "gestionar_ofertas",
        "procesar_suscripcion",
        "registrar_solicitud",
        "procesar_pago_simulado",
    ):
        monkeypatch.setitem(quote_client.application.extensions, extension, object())
    statements: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get(PATH, headers=bearer(token))
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
