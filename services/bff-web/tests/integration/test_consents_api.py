from unittest.mock import Mock
from uuid import uuid4

import pytest
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.domain.auth import User
from bff_web.domain.profiling import ProfilingError
from flask import Flask
from support import USER, FakeJwks, KeyPair, bearer, sign_token

PATH = "/v1/consentimientos/perfilamiento"
PROFILE = "/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234/perfilamiento"


@pytest.fixture
def profiling(app: Flask) -> Mock:
    gateway = Mock(spec=ProfilingGateway)
    app.extensions["profiling_gateway"] = gateway
    return gateway


@pytest.mark.parametrize("path", [PATH, PROFILE])
@pytest.mark.parametrize("authorization", [None, "Bearer invalido"])
def test_profiling_requires_a_valid_session(
    app: Flask, authorization: str | None, path: str
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = app.test_client().get(path, headers=headers)
    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"


def test_consent_is_not_authorized_automatically(app: Flask, token: str, profiling: Mock) -> None:
    profiling.get_consent.side_effect = ProfilingError("consentimiento_ausente", 404)
    response = app.test_client().get(PATH, headers=bearer(token))
    assert response.status_code == 404
    assert response.get_json()["code"] == "consentimiento_ausente"


def test_explicit_authorization_and_revocation_are_forwarded(
    app: Flask, token: str, profiling: Mock
) -> None:
    profiling.set_consent.return_value = {"estado": "revocado", "version": 2}
    response = app.test_client().put(
        PATH, json={"autorizado": False, "version": 1}, headers=bearer(token)
    )
    assert response.status_code == 200
    assert response.get_json() == {"estado": "revocado", "version": 2}
    assert profiling.set_consent.call_args.args[:3] == (USER.user_id, False, 1)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "payload",
    [
        {"autorizado": "true", "version": 0},
        {"autorizado": True, "version": -1},
        {"autorizado": True, "version": True},
        {"autorizado": True, "version": 0, "clienteId": str(uuid4())},
    ],
)
def test_invalid_authorization_never_reaches_identity(
    app: Flask, token: str, profiling: Mock, payload: dict[str, object]
) -> None:
    response = app.test_client().put(PATH, json=payload, headers=bearer(token))
    assert response.status_code == 400
    profiling.set_consent.assert_not_called()


def test_the_subject_is_always_taken_from_the_jwt(
    app: Flask, keypair: KeyPair, profiling: Mock
) -> None:
    other = User(uuid4(), "Otro Cliente", "otro@solventa.co", "cliente")
    profiling.get_consent.return_value = {"estado": "autorizado"}
    app.test_client().get(
        PATH + "?clienteId=" + str(USER.user_id),
        headers=bearer(sign_token(keypair, user=other)),
    )
    assert profiling.get_consent.call_args.args[0] == other.user_id


def test_profile_is_proxied_with_the_authenticated_subject(
    app: Flask, token: str, profiling: Mock
) -> None:
    profiling.get_profile.return_value = {"solicitudId": "AQT-2026-ABCDEF1234"}
    response = app.test_client().get(PROFILE, headers=bearer(token))
    assert response.status_code == 200
    assert profiling.get_profile.call_args.args[:2] == (USER.user_id, "AQT-2026-ABCDEF1234")


def test_unconfigured_profiling_fails_closed(app: Flask, token: str) -> None:
    response = app.test_client().get(PATH, headers=bearer(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "perfilamiento_no_disponible"


def test_identity_outages_are_not_hidden(app: Flask, jwks: FakeJwks, token: str) -> None:
    jwks.failing = True
    response = app.test_client().get(PATH, headers=bearer(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
