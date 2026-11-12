from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_renewal import (
    PolicyNotFoundError,
    PolicyNotRenewableError,
    Renewal,
    RenewalConflictError,
    RenewalEligibility,
    RenewalRequest,
    RenewalServiceUnavailableError,
)
from flask import Flask
from support import USER, KeyPair, sign_token

POLICY_ID = UUID("0b5f8f62-3c1e-4a3e-9a51-6d8f0f3a9c11")
HASH = "c" * 64


def offer() -> dict[str, object]:
    return {
        "polizaId": str(POLICY_ID),
        "producto": "Microseguro de Vida",
        "finAnterior": "2026-12-11",
        "nuevaVigencia": {"inicio": "2026-12-12", "fin": "2027-01-12"},
        "coberturas": [{"nombre": "Fallecimiento", "descripcion": "Respaldo economico."}],
        "prima": {"monto": "25000", "moneda": "COP", "periodicidad": "mensual"},
        "condiciones": ["La informacion de salud debe ser verificable."],
        "requierePago": True,
        "reglaVersion": "academica-renovacion-v1",
        "condicionesHash": HASH,
    }


def renewal(state: str = "renovada") -> Renewal:
    data = {key: value for key, value in offer().items() if key not in {"producto", "requierePago"}}
    return Renewal.model_validate(
        {
            **data,
            "id": str(uuid4()),
            "estado": state,
            "motivo": "pago_rechazado" if state == "rechazada" else None,
            "creadaEn": "2026-12-10T15:00:00Z",
            "actualizadaEn": "2026-12-10T15:00:01Z",
            "simulada": True,
        }
    )


def eligibility() -> RenewalEligibility:
    return RenewalEligibility.model_validate(
        {
            "polizaId": str(POLICY_ID),
            "tipo": "life",
            "vigenciaActual": {"inicio": "2026-11-11", "fin": "2026-12-11"},
            "renovable": True,
            "motivo": None,
            "ventana": {"desde": "2026-11-26", "hasta": "2026-12-11"},
            "oferta": offer(),
            "ultimoIntento": None,
        }
    )


@dataclass
class StubRenewalGateway:
    failure: Exception | None = None
    result: tuple[Renewal, int] = field(default_factory=lambda: (renewal(), 201))
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def get_eligibility(
        self, policy_id: UUID, client_id: UUID, correlation_id: str
    ) -> RenewalEligibility:
        self.calls.append(("get", policy_id, client_id))
        if self.failure is not None:
            raise self.failure
        return eligibility()

    def renew(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        request: RenewalRequest,
        correlation_id: str,
    ) -> tuple[Renewal, int]:
        self.calls.append(("post", policy_id, client_id, idempotency_key, request.conditions_hash))
        if self.failure is not None:
            raise self.failure
        return self.result


@pytest.fixture
def gateway(app: Flask) -> StubRenewalGateway:
    stub = StubRenewalGateway()
    app.extensions["policy_renewal_gateway"] = stub
    return stub


@pytest.fixture
def token(keypair: KeyPair) -> str:
    return sign_token(keypair, scope="cliente")


def auth(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


ELIGIBILITY = f"/v1/clientes/me/polizas/{POLICY_ID}/renovacion"
RENEWALS = f"/v1/clientes/me/polizas/{POLICY_ID}/renovaciones"
BODY = {"aceptaCondiciones": True, "condicionesHash": HASH}


def test_consulta_elegibilidad_con_cliente_de_la_sesion(
    app: Flask, gateway: StubRenewalGateway, token: str
) -> None:
    response = app.test_client().get(ELIGIBILITY, headers=auth(token))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["oferta"]["condicionesHash"] == HASH
    assert gateway.calls == [("get", POLICY_ID, USER.user_id)]


def test_confirma_renovacion_aceptada_con_clave_idempotente(
    app: Flask, gateway: StubRenewalGateway, token: str
) -> None:
    key = str(uuid4())
    response = app.test_client().post(RENEWALS, json=BODY, headers=auth(token, key))

    assert response.status_code == 201
    assert response.get_json()["estado"] == "renovada"
    assert gateway.calls == [("post", POLICY_ID, USER.user_id, UUID(key), HASH)]


def test_pendiente_indica_reintento(app: Flask, gateway: StubRenewalGateway, token: str) -> None:
    gateway.result = (renewal("pendiente"), 202)
    response = app.test_client().post(RENEWALS, json=BODY, headers=auth(token, str(uuid4())))

    assert response.status_code == 202
    assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize(
    "body",
    [
        {"aceptaCondiciones": False, "condicionesHash": HASH},
        {"aceptaCondiciones": "true", "condicionesHash": HASH},
        {"condicionesHash": HASH},
        {"aceptaCondiciones": True, "condicionesHash": "corto"},
        {**BODY, "clienteId": str(uuid4())},
        None,
    ],
)
def test_exige_aceptacion_explicita_y_no_acepta_cliente_del_navegador(
    app: Flask, gateway: StubRenewalGateway, token: str, body: object
) -> None:
    response = app.test_client().post(RENEWALS, json=body, headers=auth(token, str(uuid4())))

    assert response.status_code == 400
    assert gateway.calls == []


def test_valida_identificadores_y_parametros(
    app: Flask, gateway: StubRenewalGateway, token: str
) -> None:
    client = app.test_client()
    assert client.post(RENEWALS, json=BODY, headers=auth(token)).status_code == 400
    assert client.post(RENEWALS, json=BODY, headers=auth(token, "clave")).status_code == 400
    bad_policy = "/v1/clientes/me/polizas/no-uuid/renovacion"
    assert client.get(bad_policy, headers=auth(token)).status_code == 400
    assert client.get(ELIGIBILITY + "?clienteId=x", headers=auth(token)).status_code == 400
    assert gateway.calls == []


def test_requiere_sesion_de_cliente(
    app: Flask, gateway: StubRenewalGateway, keypair: KeyPair
) -> None:
    client = app.test_client()
    assert client.get(ELIGIBILITY).status_code == 401
    advisor = sign_token(keypair, scope="cotizaciones")
    assert client.get(ELIGIBILITY, headers=auth(advisor)).status_code == 403
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code", "extension"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada", None),
        (
            PolicyNotRenewableError("fuera_de_ventana"),
            422,
            "poliza_no_renovable",
            ("motivo", "fuera_de_ventana"),
        ),
        (RenewalConflictError("condiciones_cambiaron"), 409, "condiciones_cambiaron", None),
        (
            RenewalConflictError("renovacion_en_curso", POLICY_ID),
            409,
            "renovacion_en_curso",
            ("renovacionId", str(POLICY_ID)),
        ),
        (RenewalServiceUnavailableError(), 503, "servicio_no_disponible", None),
    ],
)
def test_resultados_de_negocio_y_fallos_son_problemas(
    app: Flask,
    gateway: StubRenewalGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
    extension: tuple[str, str] | None,
) -> None:
    gateway.failure = failure
    response = app.test_client().post(RENEWALS, json=BODY, headers=auth(token, str(uuid4())))

    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    assert response.get_json()["code"] == code
    if extension is not None:
        assert response.get_json()[extension[0]] == extension[1]
    if status == 503:
        assert response.headers["Retry-After"] == "5"
