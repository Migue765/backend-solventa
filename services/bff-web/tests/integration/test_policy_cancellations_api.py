from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_cancellation import (
    Cancellation,
    CancellationConfirmation,
    CancellationConflictError,
    CancellationState,
    PolicyNotCancellableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from flask import Flask
from support import USER, KeyPair, sign_token

POLICY_ID = UUID("6a1d7e3c-90b2-4f3f-8c2e-1b6f0e2d4a55")
CANCELLATION_ID = UUID("8c3f9a5e-b2d4-4b51-8e4a-3d8b2a4f6c77")
HASH = "c" * 64


def settlement(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "fechaEfectiva": "2026-11-21",
        "finCobertura": "2026-11-21",
        "coberturasFinalizadas": [{"codigo": "fallecimiento", "nombre": "Fallecimiento"}],
        "periodoActual": {"inicio": "2026-11-11", "fin": "2026-12-11"},
        "periodosAnulados": [
            {
                "inicio": "2026-12-12",
                "fin": "2027-01-11",
                "prima": {"monto": "25000", "moneda": "COP"},
            }
        ],
        "primaMensual": {"monto": "25000", "moneda": "COP"},
        "diasPeriodo": 31,
        "diasNoUsados": 20,
        "devolucionProporcional": {"monto": "16129", "moneda": "COP"},
        "devolucionPeriodosAnulados": {"monto": "25000", "moneda": "COP"},
        "devolucion": {"monto": "41129", "moneda": "COP"},
        "valorPendiente": {"monto": "0", "moneda": "COP"},
        "reglaVersion": "academica-cancelacion-v1",
        "liquidacionHash": HASH,
    }
    payload.update(overrides)
    return payload


def cancellation() -> dict[str, object]:
    return {
        "id": str(CANCELLATION_ID),
        "polizaId": str(POLICY_ID),
        "estado": "cancelada",
        "motivoCancelacion": "costo",
        "fechaEfectiva": "2026-11-21",
        "liquidacion": settlement(),
        "confirmadaEn": "2026-11-21T15:00:00Z",
        "simulada": True,
    }


def state(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "polizaId": str(POLICY_ID),
        "tipo": "life",
        "estado": "activa",
        "vigencia": {"inicio": "2026-11-11", "fin": "2027-01-11"},
        "cancelable": True,
        "motivo": None,
        "liquidacion": settlement(),
        "cancelacion": None,
    }
    payload.update(overrides)
    return payload


@dataclass
class StubCancellationGateway:
    failure: Exception | None = None
    status: int = 201
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def _fail(self) -> None:
        if self.failure is not None:
            raise self.failure

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> CancellationState:
        self.calls.append(("get", policy_id, client_id))
        self._fail()
        return CancellationState.model_validate(state())

    def cancel(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        confirmation: CancellationConfirmation,
        correlation_id: str,
    ) -> tuple[Cancellation, int]:
        self.calls.append(("cancel", policy_id, client_id, idempotency_key, confirmation.reason))
        self._fail()
        return Cancellation.model_validate(cancellation()), self.status


@pytest.fixture
def gateway(app: Flask) -> StubCancellationGateway:
    stub = StubCancellationGateway()
    app.extensions["policy_cancellation_gateway"] = stub
    return stub


@pytest.fixture
def token(keypair: KeyPair) -> str:
    return sign_token(keypair, scope="cliente")


def auth(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": "Bearer " + token}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


BASE = f"/v1/clientes/me/polizas/{POLICY_ID}"
CONFIRMATION = {"confirma": True, "motivoCancelacion": "costo", "liquidacionHash": HASH}


def test_consulta_y_confirma_con_cliente_de_la_sesion(
    app: Flask, gateway: StubCancellationGateway, token: str
) -> None:
    client = app.test_client()
    consulted = client.get(BASE + "/cancelacion", headers=auth(token))
    assert consulted.status_code == 200
    assert consulted.headers["Cache-Control"] == "no-store"
    assert consulted.get_json()["liquidacion"]["devolucion"]["monto"] == "41129"

    key = str(uuid4())
    confirmed = client.post(BASE + "/cancelaciones", json=CONFIRMATION, headers=auth(token, key))
    assert confirmed.status_code == 201
    assert confirmed.get_json()["estado"] == "cancelada"
    gateway.status = 200
    replayed = client.post(BASE + "/cancelaciones", json=CONFIRMATION, headers=auth(token, key))
    assert replayed.status_code == 200
    assert gateway.calls == [
        ("get", POLICY_ID, USER.user_id),
        ("cancel", POLICY_ID, USER.user_id, UUID(key), "costo"),
        ("cancel", POLICY_ID, USER.user_id, UUID(key), "costo"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {**CONFIRMATION, "confirma": False},
        {**CONFIRMATION, "confirma": "true"},
        {key: value for key, value in CONFIRMATION.items() if key != "confirma"},
        {**CONFIRMATION, "liquidacionHash": "corto"},
        {**CONFIRMATION, "motivoCancelacion": "porque_si"},
        {**CONFIRMATION, "devolucion": "999999"},
        None,
    ],
)
def test_exige_confirmacion_explicita(
    app: Flask, gateway: StubCancellationGateway, token: str, body: object
) -> None:
    response = app.test_client().post(
        BASE + "/cancelaciones", json=body, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == 400
    assert gateway.calls == []


def test_valida_identificadores_y_sesion(
    app: Flask, gateway: StubCancellationGateway, token: str, keypair: KeyPair
) -> None:
    client = app.test_client()
    assert (
        client.post(BASE + "/cancelaciones", json=CONFIRMATION, headers=auth(token)).status_code
        == 400
    )
    assert (
        client.get("/v1/clientes/me/polizas/x/cancelacion", headers=auth(token)).status_code == 400
    )
    assert client.get(BASE + "/cancelacion?clienteId=x", headers=auth(token)).status_code == 400
    assert client.get(BASE + "/cancelacion").status_code == 401
    advisor = sign_token(keypair, scope="cotizaciones")
    assert client.get(BASE + "/cancelacion", headers=auth(advisor)).status_code == 403
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code", "motivo"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada", None),
        (
            PolicyNotCancellableError("poliza_cancelada"),
            422,
            "poliza_no_cancelable",
            "poliza_cancelada",
        ),
        (
            PolicyNotCancellableError("renovacion_en_curso"),
            422,
            "poliza_no_cancelable",
            "renovacion_en_curso",
        ),
        (CancellationConflictError("liquidacion_cambio"), 409, "liquidacion_cambio", None),
        (
            CancellationConflictError("idempotencia_conflictiva"),
            409,
            "idempotencia_conflictiva",
            None,
        ),
        (RenewalServiceUnavailableError(), 503, "servicio_no_disponible", None),
    ],
)
def test_rechazos_y_fallos_son_problemas(
    app: Flask,
    gateway: StubCancellationGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
    motivo: str | None,
) -> None:
    gateway.failure = failure
    response = app.test_client().post(
        BASE + "/cancelaciones", json=CONFIRMATION, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    assert response.get_json()["code"] == code
    if motivo is not None:
        assert response.get_json()["motivo"] == motivo


def test_consulta_no_disponible_es_problema(
    app: Flask, gateway: StubCancellationGateway, token: str
) -> None:
    gateway.failure = RenewalServiceUnavailableError()
    response = app.test_client().get(BASE + "/cancelacion", headers=auth(token))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize(
    "payload",
    [
        state(cancelable=False),
        state(motivo="poliza_vencida"),
        state(estado="cancelada"),
        state(cancelacion=cancellation()),
        state(liquidacion=settlement(devolucion={"monto": "1", "moneda": "COP"})),
        state(liquidacion=settlement(devolucionPeriodosAnulados={"monto": "0", "moneda": "COP"})),
        state(liquidacion=settlement(fechaEfectiva="2026-12-20", finCobertura="2026-12-20")),
        state(liquidacion=settlement(finCobertura="2026-12-11")),
    ],
)
def test_modelos_rechazan_respuestas_incoherentes(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CancellationState.model_validate(payload)


def test_poliza_cancelada_expone_la_cancelacion_registrada() -> None:
    cancelled = CancellationState.model_validate(
        state(
            estado="cancelada",
            cancelable=False,
            motivo="poliza_cancelada",
            liquidacion=None,
            cancelacion=cancellation(),
        )
    )
    assert cancelled.cancellation is not None
    assert cancelled.cancellation.cancellation_id == CANCELLATION_ID
    with pytest.raises(ValueError):
        Cancellation.model_validate({**cancellation(), "fechaEfectiva": "2026-11-22"})
