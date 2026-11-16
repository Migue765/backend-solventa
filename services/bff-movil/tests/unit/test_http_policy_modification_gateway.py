from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationRejectedError,
    ModificationRequest,
    PolicyNotFoundError,
    PolicyNotModifiableError,
    PolicyServiceUnavailableError,
)
from bff_movil.infrastructure.http_policy_renewal_gateway import (
    MAX_PAYLOAD,
    HttpPolicyRenewalGateway,
)
from policy_modification_data import CHANGE, CONFIRMATION, POLICY_ID, impact, result, state
from support import IdentityServer

CLIENT = UUID("2923bf51-1899-4a3e-9547-b8ccb9c2c84f")
STATE_PATH = f"/v1/polizas/{POLICY_ID}/modificacion"
SIMULATION_PATH = f"/v1/polizas/{POLICY_ID}/modificaciones/simulacion"
CONFIRM_PATH = f"/v1/polizas/{POLICY_ID}/modificaciones"
REQUEST = ModificationRequest.model_validate(CHANGE)
CONFIRM = ModificationConfirmation.model_validate(CONFIRMATION)


@pytest.fixture
def server() -> Iterator[IdentityServer]:
    instance = IdentityServer()
    yield instance
    instance.close()


@pytest.fixture
def gateway(server: IdentityServer) -> HttpPolicyRenewalGateway:
    return HttpPolicyRenewalGateway(server.url + "/", "interno", 1, 1, 1)


def test_consulta_simula_y_confirma_con_identidad_interna(
    server: IdentityServer, gateway: HttpPolicyRenewalGateway
) -> None:
    server.respond("GET", STATE_PATH, 200, state())
    server.respond("POST", SIMULATION_PATH, 200, impact())
    server.respond("POST", CONFIRM_PATH, 201, result())
    key = uuid4()

    assert gateway.get_state(POLICY_ID, CLIENT, "corr-1").policy_id == POLICY_ID
    assert gateway.simulate(POLICY_ID, CLIENT, REQUEST, "corr-2").impact_hash == "e" * 64
    confirmed, status = gateway.confirm(POLICY_ID, CLIENT, key, CONFIRM, "corr-3")

    assert (confirmed.new_version, status) == (2, 201)
    get, simulate, confirm = server.requests
    get_headers = {name.lower(): value for name, value in get["headers"].items()}
    confirm_headers = {name.lower(): value for name, value in confirm["headers"].items()}
    assert get_headers["authorization"] == "Bearer interno"
    assert get_headers["x-cliente-id"] == str(CLIENT)
    assert get_headers["x-correlation-id"] == "corr-1"
    assert get["body"] is None
    assert simulate["body"] == CHANGE
    assert confirm_headers["idempotency-key"] == str(key)
    assert confirm["body"] == CONFIRMATION


def test_reintento_idempotente_devuelve_200(
    server: IdentityServer, gateway: HttpPolicyRenewalGateway
) -> None:
    server.respond("POST", CONFIRM_PATH, 200, result())
    assert gateway.confirm(POLICY_ID, CLIENT, uuid4(), CONFIRM, "c")[1] == 200


@pytest.mark.parametrize(
    ("status", "body", "expected", "attributes"),
    [
        (404, {"error": "poliza_no_encontrada"}, PolicyNotFoundError, {}),
        (
            422,
            {"error": "poliza_no_modificable", "motivo": "poliza_vencida"},
            PolicyNotModifiableError,
            {"reason": "poliza_vencida"},
        ),
        (
            422,
            {"error": "cambio_no_permitido", "campo": "plan"},
            ModificationRejectedError,
            {"code": "cambio_no_permitido", "field": "plan"},
        ),
        (
            422,
            {"error": "cambio_no_permitido", "campo": 3},
            ModificationRejectedError,
            {"field": None},
        ),
        (422, {"error": "sin_cambios"}, ModificationRejectedError, {"code": "sin_cambios"}),
        (
            409,
            {"error": "version_desactualizada", "versionActual": 4},
            ModificationConflictError,
            {"code": "version_desactualizada", "current_version": 4},
        ),
        (409, {"error": "impacto_cambio"}, ModificationConflictError, {"code": "impacto_cambio"}),
        (
            409,
            {"error": "idempotencia_conflictiva"},
            ModificationConflictError,
            {"code": "idempotencia_conflictiva"},
        ),
        (
            422,
            {"error": "poliza_no_modificable", "motivo": "otro"},
            PolicyServiceUnavailableError,
            {},
        ),
        (
            409,
            {"error": "version_desactualizada", "versionActual": True},
            PolicyServiceUnavailableError,
            {},
        ),
        (409, {"error": "version_desactualizada"}, PolicyServiceUnavailableError, {}),
        (400, {"error": "solicitud_invalida"}, PolicyServiceUnavailableError, {}),
        (500, ["no", "objeto"], PolicyServiceUnavailableError, {}),
        (302, {}, PolicyServiceUnavailableError, {}),
    ],
)
def test_traduce_errores_de_polizas(
    server: IdentityServer,
    gateway: HttpPolicyRenewalGateway,
    status: int,
    body: object,
    expected: type[Exception],
    attributes: dict[str, object],
) -> None:
    headers = {"Location": "http://example.invalid/"} if status == 302 else None
    server.respond("POST", CONFIRM_PATH, status, body, headers)
    with pytest.raises(expected) as raised:
        gateway.confirm(POLICY_ID, CLIENT, uuid4(), CONFIRM, "c")
    for name, value in attributes.items():
        assert getattr(raised.value, name) == value


def test_error_sin_json_es_no_disponible(
    server: IdentityServer, gateway: HttpPolicyRenewalGateway
) -> None:
    server.respond_raw("GET", STATE_PATH, 502, b"<html>")
    with pytest.raises(PolicyServiceUnavailableError):
        gateway.get_state(POLICY_ID, CLIENT, "c")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", STATE_PATH, {**state(), "polizaId": str(uuid4())}),
        ("GET", STATE_PATH, {"polizaId": str(POLICY_ID)}),
        ("POST", SIMULATION_PATH, {**impact(), "versionBase": 2}),
        (
            "POST",
            SIMULATION_PATH,
            {**impact(), "plan": {"anterior": "esencial", "nuevo": "integral"}},
        ),
        ("POST", CONFIRM_PATH, {**result(), "polizaId": str(uuid4())}),
        ("POST", CONFIRM_PATH, {**result(), "impacto": {**impact(), "impactoHash": "f" * 64}}),
    ],
)
def test_respuestas_incoherentes_no_se_aceptan(
    server: IdentityServer,
    gateway: HttpPolicyRenewalGateway,
    method: str,
    path: str,
    body: object,
) -> None:
    server.respond(method, path, 200, body)
    with pytest.raises(PolicyServiceUnavailableError):
        if path == STATE_PATH:
            gateway.get_state(POLICY_ID, CLIENT, "c")
        elif path == SIMULATION_PATH:
            gateway.simulate(POLICY_ID, CLIENT, REQUEST, "c")
        else:
            gateway.confirm(POLICY_ID, CLIENT, uuid4(), CONFIRM, "c")


def test_confirmacion_con_estado_inesperado_no_se_acepta(
    server: IdentityServer, gateway: HttpPolicyRenewalGateway
) -> None:
    server.respond("POST", CONFIRM_PATH, 202, result())
    with pytest.raises(PolicyServiceUnavailableError):
        gateway.confirm(POLICY_ID, CLIENT, uuid4(), CONFIRM, "c")


def test_rechaza_respuestas_grandes_o_invalidas(
    server: IdentityServer, gateway: HttpPolicyRenewalGateway
) -> None:
    server.respond_raw("GET", STATE_PATH, 200, b"[" + b" " * MAX_PAYLOAD + b"]")
    with pytest.raises(PolicyServiceUnavailableError):
        gateway.get_state(POLICY_ID, CLIENT, "c")
    server.respond_raw("GET", STATE_PATH, 200, b"no-json")
    with pytest.raises(PolicyServiceUnavailableError):
        gateway.get_state(POLICY_ID, CLIENT, "c")


def test_sin_configuracion_o_sin_red_no_esta_disponible(server: IdentityServer) -> None:
    for gateway in (
        HttpPolicyRenewalGateway("", "interno"),
        HttpPolicyRenewalGateway(server.url, ""),
    ):
        with pytest.raises(PolicyServiceUnavailableError):
            gateway.get_state(POLICY_ID, CLIENT, "c")
    assert server.requests == []
    offline = HttpPolicyRenewalGateway("http://127.0.0.1:9", "interno", 0.5, 0.5, 0.5)
    with pytest.raises(PolicyServiceUnavailableError):
        offline.simulate(POLICY_ID, CLIENT, REQUEST, "c")
