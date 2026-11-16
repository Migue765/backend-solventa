import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_cancellation import (
    CancellationConfirmation,
    CancellationConflictError,
    PolicyNotCancellableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.infrastructure.http_policy_cancellation_gateway import (
    HttpPolicyCancellationGateway,
)

TARGET = "bff_web.infrastructure.http_policy_renewal_gateway.urlopen"
HASH = "d" * 64
CONFIRMATION = CancellationConfirmation.model_validate(
    {"confirma": True, "motivoCancelacion": "otro", "liquidacionHash": HASH}
)


class Reply(io.BytesIO):
    def __init__(self, body: object, status: int = 200) -> None:
        super().__init__(json.dumps(body).encode())
        self.status = status


def settlement(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "fechaEfectiva": "2026-11-21",
        "finCobertura": "2026-11-21",
        "coberturasFinalizadas": [{"codigo": "fallecimiento", "nombre": "Fallecimiento"}],
        "periodoActual": {"inicio": "2026-11-11", "fin": "2026-12-11"},
        "periodosAnulados": [],
        "primaMensual": {"monto": "25000", "moneda": "COP"},
        "diasPeriodo": 31,
        "diasNoUsados": 20,
        "devolucionProporcional": {"monto": "16129", "moneda": "COP"},
        "devolucionPeriodosAnulados": {"monto": "0", "moneda": "COP"},
        "devolucion": {"monto": "16129", "moneda": "COP"},
        "valorPendiente": {"monto": "0", "moneda": "COP"},
        "reglaVersion": "academica-cancelacion-v1",
        "liquidacionHash": HASH,
    }
    payload.update(overrides)
    return payload


def cancellation(policy_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(uuid4()),
        "polizaId": str(policy_id),
        "estado": "cancelada",
        "motivoCancelacion": "otro",
        "fechaEfectiva": "2026-11-21",
        "liquidacion": settlement(),
        "confirmadaEn": "2026-11-21T15:00:00Z",
        "simulada": True,
    }
    payload.update(overrides)
    return payload


def http_error(status: int, body: object) -> HTTPError:
    return HTTPError(
        "http://polizas", status, "error", Message(), io.BytesIO(json.dumps(body).encode())
    )


def gateway() -> HttpPolicyCancellationGateway:
    return HttpPolicyCancellationGateway("http://polizas", "token-de-prueba")


def test_consulta_envia_cliente_y_valida_poliza() -> None:
    policy_id, client_id = uuid4(), uuid4()
    body = {
        "polizaId": str(policy_id),
        "tipo": "life",
        "estado": "activa",
        "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11"},
        "cancelable": True,
        "motivo": None,
        "liquidacion": settlement(),
        "cancelacion": None,
    }

    def respond(request: Request, timeout: float) -> Reply:
        assert request.full_url == f"http://polizas/v1/polizas/{policy_id}/cancelacion"
        assert request.get_method() == "GET"
        assert request.get_header("X-cliente-id") == str(client_id)
        return Reply(body)

    with patch(TARGET, side_effect=respond):
        state = gateway().get_state(policy_id, client_id, "corr")
    assert state.settlement is not None and state.settlement.settlement_hash == HASH

    with (
        patch(TARGET, return_value=Reply({**body, "polizaId": str(uuid4())})),
        pytest.raises(RenewalServiceUnavailableError),
    ):
        gateway().get_state(policy_id, client_id, "corr")


def test_confirmacion_envia_clave_y_cuerpo() -> None:
    policy_id, key = uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> Reply:
        assert request.full_url == f"http://polizas/v1/polizas/{policy_id}/cancelaciones"
        assert request.get_header("Idempotency-key") == str(key)
        assert isinstance(request.data, bytes)
        assert json.loads(request.data) == {
            "confirma": True,
            "motivoCancelacion": "otro",
            "liquidacionHash": HASH,
        }
        assert timeout == 5
        return Reply(cancellation(policy_id), 201)

    with patch(TARGET, side_effect=respond):
        cancelled, status = gateway().cancel(policy_id, uuid4(), key, CONFIRMATION, "corr")
    assert status == 201 and cancelled.state == "cancelada"


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({}, 202),
        ({"polizaId": str(uuid4())}, 201),
        ({"motivoCancelacion": "costo"}, 201),
        ({"liquidacion": settlement(liquidacionHash="0" * 64)}, 200),
        ({"simulada": False}, 201),
    ],
)
def test_cancelaciones_incoherentes_no_se_aceptan(
    overrides: dict[str, object], status: int
) -> None:
    policy_id = uuid4()
    with (
        patch(TARGET, return_value=Reply(cancellation(policy_id, **overrides), status)),
        pytest.raises(RenewalServiceUnavailableError),
    ):
        gateway().cancel(policy_id, uuid4(), uuid4(), CONFIRMATION, "corr")


@pytest.mark.parametrize(
    ("error", "expected", "attribute"),
    [
        (http_error(404, {"error": "poliza_no_encontrada"}), PolicyNotFoundError, None),
        (
            http_error(422, {"error": "poliza_no_cancelable", "motivo": "renovacion_en_curso"}),
            PolicyNotCancellableError,
            ("reason", "renovacion_en_curso"),
        ),
        (
            http_error(422, {"error": "poliza_no_cancelable", "motivo": "desconocido"}),
            RenewalServiceUnavailableError,
            None,
        ),
        (
            http_error(409, {"error": "liquidacion_cambio"}),
            CancellationConflictError,
            ("code", "liquidacion_cambio"),
        ),
        (
            http_error(409, {"error": "idempotencia_conflictiva"}),
            CancellationConflictError,
            ("code", "idempotencia_conflictiva"),
        ),
        (http_error(422, {"error": "poliza_no_renovable"}), RenewalServiceUnavailableError, None),
        (http_error(500, {"error": "interno"}), RenewalServiceUnavailableError, None),
    ],
)
def test_traduce_errores_de_polizas(
    error: Exception, expected: type[Exception], attribute: tuple[str, object] | None
) -> None:
    with patch(TARGET, side_effect=error), pytest.raises(expected) as raised:
        gateway().cancel(uuid4(), uuid4(), uuid4(), CONFIRMATION, "corr")
    if attribute is not None:
        assert getattr(raised.value, attribute[0]) == attribute[1]
