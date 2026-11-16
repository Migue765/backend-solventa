import io
import json
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_renewal import (
    PolicyNotFoundError,
    PolicyNotRenewableError,
    RenewalConflictError,
    RenewalRequest,
    RenewalServiceUnavailableError,
)
from bff_web.infrastructure.http_policy_renewal_gateway import HttpPolicyRenewalGateway

TARGET = "bff_web.infrastructure.http_policy_renewal_gateway.urlopen"
HASH = "d" * 64
REQUEST = RenewalRequest.model_validate({"aceptaCondiciones": True, "condicionesHash": HASH})


class Reply(io.BytesIO):
    def __init__(self, body: object, status: int = 200) -> None:
        super().__init__(json.dumps(body).encode())
        self.status = status


def renewal(policy_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(uuid4()),
        "polizaId": str(policy_id),
        "estado": "renovada",
        "motivo": None,
        "finAnterior": "2026-12-11",
        "nuevaVigencia": {"inicio": "2026-12-12", "fin": "2027-01-12"},
        "coberturas": [{"nombre": "Fallecimiento", "descripcion": "Respaldo economico."}],
        "prima": {"monto": "25000", "moneda": "COP", "periodicidad": "mensual"},
        "condiciones": ["La informacion de salud debe ser verificable."],
        "condicionesHash": HASH,
        "reglaVersion": "academica-renovacion-v1",
        "creadaEn": "2026-12-10T15:00:00Z",
        "actualizadaEn": "2026-12-10T15:00:01Z",
        "simulada": True,
    }
    payload.update(overrides)
    return payload


def eligibility(policy_id: UUID) -> dict[str, object]:
    return {
        "polizaId": str(policy_id),
        "tipo": "automotive",
        "vigenciaActual": {"inicio": "2026-11-11", "fin": "2026-12-11"},
        "renovable": False,
        "motivo": "fuera_de_ventana",
        "ventana": {"desde": "2026-11-26", "hasta": "2026-12-11"},
        "oferta": None,
        "ultimoIntento": None,
    }


def http_error(status: int, body: object) -> HTTPError:
    return HTTPError(
        "http://polizas", status, "error", Message(), io.BytesIO(json.dumps(body).encode())
    )


def gateway() -> HttpPolicyRenewalGateway:
    return HttpPolicyRenewalGateway("http://polizas/", "token-de-prueba")


def test_consulta_envia_cliente_token_correlacion_y_timeout_corto() -> None:
    policy_id, client_id = uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> Reply:
        assert request.full_url == f"http://polizas/v1/polizas/{policy_id}/renovacion"
        assert request.get_method() == "GET"
        assert request.get_header("X-cliente-id") == str(client_id)
        assert request.get_header("Authorization") == "Bearer token-de-prueba"
        assert request.get_header("X-correlation-id") == "corr"
        assert timeout == 0.8
        return Reply(eligibility(policy_id))

    with patch(TARGET, side_effect=respond):
        result = gateway().get_eligibility(policy_id, client_id, "corr")
    assert result.reason == "fuera_de_ventana"


def test_renovacion_envia_clave_y_aceptacion() -> None:
    policy_id, client_id, key = uuid4(), uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> Reply:
        assert request.full_url == f"http://polizas/v1/polizas/{policy_id}/renovaciones"
        assert request.get_method() == "POST"
        assert request.get_header("Idempotency-key") == str(key)
        assert isinstance(request.data, bytes)
        assert json.loads(request.data) == {"aceptaCondiciones": True, "condicionesHash": HASH}
        assert timeout == 5
        return Reply(renewal(policy_id), 201)

    with patch(TARGET, side_effect=respond):
        result, status = gateway().renew(policy_id, client_id, key, REQUEST, "corr")
    assert status == 201 and result.state == "renovada"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"estado": "pendiente"}, 201),
        ({"estado": "renovada"}, 202),
        ({"condicionesHash": "e" * 64}, 201),
        ({"polizaId": str(uuid4())}, 201),
        ({"simulada": False}, 201),
        ({"estado": "rechazada"}, 201),
    ],
)
def test_respuestas_incoherentes_no_confirman_renovacion(
    body: dict[str, object], status: int
) -> None:
    policy_id = uuid4()
    reply = Reply(renewal(policy_id, **body), status)
    with patch(TARGET, return_value=reply), pytest.raises(RenewalServiceUnavailableError):
        gateway().renew(policy_id, uuid4(), uuid4(), REQUEST, "corr")


def test_elegibilidad_de_otra_poliza_no_se_acepta() -> None:
    with (
        patch(TARGET, return_value=Reply(eligibility(uuid4()))),
        pytest.raises(RenewalServiceUnavailableError),
    ):
        gateway().get_eligibility(uuid4(), uuid4(), "corr")


@pytest.mark.parametrize(
    ("error", "expected", "attribute"),
    [
        (http_error(404, {"error": "poliza_no_encontrada"}), PolicyNotFoundError, None),
        (
            http_error(422, {"error": "poliza_no_renovable", "motivo": "poliza_vencida"}),
            PolicyNotRenewableError,
            ("reason", "poliza_vencida"),
        ),
        (
            http_error(409, {"error": "renovacion_en_curso", "renovacionId": str(UUID(int=7))}),
            RenewalConflictError,
            ("renewal_id", UUID(int=7)),
        ),
        (http_error(409, {"error": "otro"}), RenewalServiceUnavailableError, None),
        (
            http_error(422, {"error": "poliza_no_renovable", "motivo": "x"}),
            RenewalServiceUnavailableError,
            None,
        ),
        (http_error(404, {"error": "ruta"}), RenewalServiceUnavailableError, None),
        (http_error(401, {"error": "no_autorizado"}), RenewalServiceUnavailableError, None),
        (URLError("caido"), RenewalServiceUnavailableError, None),
        (TimeoutError(), RenewalServiceUnavailableError, None),
    ],
)
def test_traduce_errores_de_polizas(
    error: Exception, expected: type[Exception], attribute: tuple[str, Any] | None
) -> None:
    with patch(TARGET, side_effect=error), pytest.raises(expected) as raised:
        gateway().renew(uuid4(), uuid4(), uuid4(), REQUEST, "corr")
    if attribute is not None:
        assert getattr(raised.value, attribute[0]) == attribute[1]


def test_sin_configuracion_no_llama_a_polizas() -> None:
    with patch(TARGET) as urlopen, pytest.raises(RenewalServiceUnavailableError):
        HttpPolicyRenewalGateway("", "token").get_eligibility(uuid4(), uuid4(), "corr")
    urlopen.assert_not_called()
