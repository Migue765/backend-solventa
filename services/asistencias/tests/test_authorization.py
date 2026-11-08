import json
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request
from uuid import uuid4

import asistencias.authorization as authorization
import pytest
from asistencias.authorization import HttpResourceAuthorizer
from asistencias.domain import Config, Problem


class FakeResponse:
    status = 200

    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self.body


@pytest.fixture
def authorizer() -> HttpResourceAuthorizer:
    return HttpResourceAuthorizer(
        Config(
            "sqlite:///test.db",
            "interno",
            "http://polizas.test",
            "http://siniestros.test",
        )
    )


def test_autoriza_poliza_vigente_del_cliente(
    authorizer: HttpResourceAuthorizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id, policy_id, correlation_id = uuid4(), uuid4(), uuid4()
    forwarded: list[Request] = []

    def open_upstream(request: Request, timeout: float) -> FakeResponse:
        forwarded.append(request)
        assert timeout == 5
        return FakeResponse({"polizas": [{"id": str(policy_id), "estado": "vigente"}]})

    monkeypatch.setattr(authorization, "urlopen", open_upstream)

    assert authorizer.authorized_policy(client_id, policy_id, correlation_id)
    request = forwarded[0]
    assert request.full_url == f"http://polizas.test/v1/polizas?clienteId={client_id}"
    assert request.get_header("Authorization") == "Bearer interno"
    assert request.get_header("X-correlation-id") == str(correlation_id)


def test_autoriza_siniestro_con_cliente_derivado(
    authorizer: HttpResourceAuthorizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id, claim_id, correlation_id = uuid4(), uuid4(), uuid4()
    forwarded: list[Request] = []

    def open_upstream(request: Request, timeout: float) -> FakeResponse:
        forwarded.append(request)
        return FakeResponse({"id": str(claim_id), "estado": "reportado"})

    monkeypatch.setattr(authorization, "urlopen", open_upstream)

    assert authorizer.authorized_claim(client_id, claim_id, correlation_id)
    request = forwarded[0]
    assert request.full_url == f"http://siniestros.test/v1/siniestros/{claim_id}/estado"
    assert request.get_header("X-cliente-id") == str(client_id)


def test_siniestro_inexistente_no_autoriza(
    authorizer: HttpResourceAuthorizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_found(request: Request, timeout: float) -> FakeResponse:
        raise HTTPError(request.full_url, 404, "not found", Message(), None)

    monkeypatch.setattr(authorization, "urlopen", not_found)

    assert not authorizer.authorized_claim(uuid4(), uuid4(), uuid4())


@pytest.mark.parametrize("body", [[], {"polizas": "invalido"}])
def test_respuesta_invalida_no_se_interpreta_como_autorizacion(
    authorizer: HttpResourceAuthorizer,
    monkeypatch: pytest.MonkeyPatch,
    body: object,
) -> None:
    monkeypatch.setattr(
        authorization,
        "urlopen",
        lambda request, timeout: FakeResponse(body),
    )

    with pytest.raises(Problem) as failure:
        authorizer.authorized_policy(uuid4(), uuid4(), uuid4())

    assert failure.value.status == 503
    assert failure.value.code == "servicio_no_disponible"
