import json
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from bff_web.domain.client_information import ClientInformationError, ClientInformationResult
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from flask import Flask
from support import USER, KeyPair, sign_token

from .test_client_information_api import ClientInformationGatewayStub
from .test_mortgage_profiling_api import ADVISOR, MortgageGatewayStub, _payload
from .test_partner_quotes_api import PARTNER, PATH, PartnerGateway, payload

CORRELATION_ID = "338995c8-770f-45c5-a420-60e50de46363"
RECOVERY = f"{PATH}/{uuid4()}?socioId=BANCO-001"
MORTGAGE = "/v1/perfilamientos-hipotecarios"
INFORMATION = "/v1/socios/consultas-informacion-cliente"
TARGETS = [
    ("POST", PATH, False),
    ("GET", "/v1/socios/actual", False),
    ("GET", RECOVERY, False),
    ("POST", MORTGAGE, True),
    ("GET", "/v1/asesor/clientes", True),
    ("POST", INFORMATION, False),
]


@pytest.fixture(autouse=True)
def valid_partner_consent(app: Flask) -> None:
    profiling = Mock()
    profiling.get_consent.return_value = {
        "consentimientoId": str(uuid4()),
        "clienteId": str(USER.user_id),
        "version": 2,
        "estado": "autorizado",
        "vigenteHasta": "2099-10-25T23:00:00Z",
        "actorId": str(USER.user_id),
        "fuentes": ["open_finance", "open_data"],
        "datos": ["indicador_riesgo"],
        "versionTerminos": "1.0",
    }
    app.extensions["profiling_gateway"] = profiling


def information_payload() -> dict[str, object]:
    return {
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "clienteId": str(USER.user_id),
        "productoId": "automotive",
        "finalidad": "cotizacion",
        "datosSolicitados": ["indicador_riesgo"],
    }


def audit_event(caplog: pytest.LogCaptureFixture, code: str, status: int) -> dict[str, object]:
    records = [record for record in caplog.records if getattr(record, "event", None)]
    assert len(records) == 1
    fields = records[0].__dict__
    assert fields["event"] == "sol325.rechazo"
    event = json.loads(records[0].getMessage())
    assert event == {
        key: fields[key]
        for key in (
            "event",
            "code",
            "reason",
            "status",
            "correlationId",
            "endpoint",
            "actorId",
            "socioId",
        )
        if key in fields
    }
    assert fields["code"] == code
    assert fields["status"] == status
    assert fields["correlationId"] == CORRELATION_ID
    assert fields["endpoint"] is not None
    for sensitive in (
        USER.name,
        USER.email,
        str(USER.user_id),
        PARTNER.name,
        PARTNER.email,
        ADVISOR.name,
        ADVISOR.email,
        "1023456789",
        "+573001234567",
        "1985-02-10",
        "HIP-2026-001",
        "ABC123",
        "no-registrar-credencial",
        "SOCIO-NO-VERIFICADO",
    ):
        assert sensitive not in repr(fields)
    assert fields["exc_info"] is None
    assert fields["args"] == ()
    return fields


@pytest.mark.parametrize("method,path,advisor", TARGETS)
@pytest.mark.parametrize("credential", ["missing", "invalid", "expired"])
def test_credentials_are_audited_without_untrusted_identity(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    advisor: bool,
    credential: str,
) -> None:
    user = ADVISOR if advisor else PARTNER
    token = sign_token(
        keypair,
        user=user,
        scope=user.role,
        socios=["BANCO-001"],
        expires_in=-60,
    )
    authorization = {
        "missing": "",
        "invalid": "Bearer no-registrar-credencial",
        "expired": "Bearer " + token,
    }[credential]
    body = _payload(uuid4()) if advisor else payload()
    body["socioId"] = "SOCIO-NO-VERIFICADO"
    response = app.test_client().open(
        path,
        method=method,
        json=body,
        headers={"Authorization": authorization, "X-Correlation-ID": CORRELATION_ID},
    )
    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    fields = audit_event(caplog, "sesion_invalida", 401)
    assert fields["reason"] == (
        "credencial_ausente" if credential == "missing" else "credencial_invalida"
    )
    assert "actorId" not in fields and "socioId" not in fields
    assert token not in repr(fields)


@pytest.mark.parametrize("method,path,advisor", TARGETS)
@pytest.mark.parametrize("claim", ["rol", "scope", "socios"])
def test_role_and_scope_rejections_preserve_public_contract(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    advisor: bool,
    claim: str,
) -> None:
    user = ADVISOR if advisor else PARTNER
    token = sign_token(
        keypair,
        user=user,
        scope="cliente" if claim == "scope" else user.role,
        rol="cliente" if claim == "rol" else user.role,
        socios=[] if claim == "socios" else ["BANCO-001"],
    )
    response = app.test_client().open(
        path,
        method=method,
        headers={"Authorization": "Bearer " + token, "X-Correlation-ID": CORRELATION_ID},
    )
    status, code = (401, "sesion_invalida") if advisor else (403, "alcance_insuficiente")
    assert response.status_code == status
    assert response.get_json()["code"] == code
    fields = audit_event(caplog, code, status)
    assert fields["reason"] == (
        "credencial_invalida" if advisor and claim != "rol" else "alcance_insuficiente"
    )
    assert "actorId" not in fields and "socioId" not in fields
    assert token not in repr(fields)


@pytest.mark.parametrize("method,path,advisor", [TARGETS[0], TARGETS[2], TARGETS[3], TARGETS[5]])
def test_foreign_partner_is_never_attributed_as_authorized(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    advisor: bool,
) -> None:
    user = ADVISOR if advisor else PARTNER
    body = (
        information_payload()
        if path == INFORMATION
        else _payload(uuid4())
        if advisor
        else payload()
    )
    body["socioId"] = "SOCIO-NO-VERIFICADO"
    response = app.test_client().open(
        path.replace("socioId=BANCO-001", "socioId=SOCIO-NO-VERIFICADO"),
        method=method,
        json=body,
        headers={
            "Authorization": "Bearer "
            + sign_token(keypair, user=user, scope=user.role, socios=["BANCO-001"]),
            "Idempotency-Key": str(body["operationId"]),
            "X-Correlation-ID": CORRELATION_ID,
        },
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "socio_no_autorizado"
    fields = audit_event(caplog, "socio_no_autorizado", 403)
    assert fields["actorId"] == str(user.user_id)
    assert "socioId" not in fields


@pytest.mark.parametrize(
    "method,path,advisor", [target for target in TARGETS if target[1] != "/v1/asesor/clientes"]
)
def test_version_rejection_does_not_trust_request_partner(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    advisor: bool,
) -> None:
    user = ADVISOR if advisor else PARTNER
    body = _payload(uuid4()) if advisor else payload()
    response = app.test_client().open(
        path,
        method=method,
        json=body,
        headers={
            "Authorization": "Bearer "
            + sign_token(keypair, user=user, scope=user.role, socios=["BANCO-001"]),
            "API-Version": "no-registrar-credencial",
            "X-Correlation-ID": CORRELATION_ID,
        },
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "version_no_soportada"
    fields = audit_event(caplog, "version_no_soportada", 400)
    assert fields["actorId"] == str(user.user_id)
    assert "socioId" not in fields


@pytest.mark.parametrize("advisor", [False, True])
@pytest.mark.parametrize(
    "code,status,public_code",
    [
        ("cuota_socio_excedida", 429, "cuota_socio_excedida"),
        ("socio_ocupado", 429, "socio_ocupado"),
        ("operacion_en_curso", 409, "operacion_en_curso"),
        ("conflicto_idempotencia", 409, "idempotencia_conflicto"),
        ("operacion_reintentable", 409, "operacion_reintentable"),
    ],
)
def test_admission_rejection_records_only_verified_context(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    advisor: bool,
    code: str,
    status: int,
    public_code: str,
) -> None:
    error = MortgageProfilingError(code, status, 12)
    app.extensions["partner_quote_gateway"] = PartnerGateway(error=error)
    app.extensions["mortgage_profiling_gateway"] = MortgageGatewayStub(error=error)
    user = ADVISOR if advisor else PARTNER
    body = _payload(uuid4()) if advisor else payload()
    token = sign_token(keypair, user=user, scope=user.role, socios=["BANCO-001"])
    response = app.test_client().post(
        MORTGAGE if advisor else PATH,
        json=body,
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": str(body["operationId"]),
            "X-Correlation-ID": CORRELATION_ID,
        },
    )
    assert response.status_code == status
    assert response.get_json()["code"] == public_code
    assert response.headers["Retry-After"] == "12"
    fields = audit_event(caplog, public_code, status)
    assert fields["actorId"] == str(user.user_id)
    assert fields["socioId"] == "BANCO-001"
    assert token not in repr(fields)


def test_audit_context_does_not_leak_into_next_request(
    app: Flask, keypair: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    body = payload()
    app.extensions["partner_quote_gateway"] = PartnerGateway()
    client = app.test_client()
    response = client.post(
        PATH,
        json=body,
        headers={
            "Authorization": "Bearer "
            + sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"]),
            "Idempotency-Key": str(body["operationId"]),
        },
    )
    assert response.status_code == 201
    assert not caplog.records
    response = client.post(PATH, json=body, headers={"X-Correlation-ID": CORRELATION_ID})
    fields = audit_event(caplog, "sesion_invalida", 401)
    assert "actorId" not in fields and "socioId" not in fields


def test_invalid_correlation_is_not_logged_verbatim(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    response = app.test_client().get(
        "/v1/socios/actual", headers={"X-Correlation-ID": "no-registrar-credencial"}
    )
    records = [record for record in caplog.records if getattr(record, "event", None)]
    assert len(records) == 1
    correlation_id = records[0].__dict__["correlationId"]
    assert str(UUID(correlation_id)) == response.headers["X-Correlation-ID"]
    assert "no-registrar-credencial" not in repr(records[0].__dict__)


@pytest.mark.parametrize("invalid", ["body", "missing_key", "different_key", "purpose", "extra"])
def test_client_information_payload_rejection_does_not_trust_payload_partner(
    app: Flask, keypair: KeyPair, caplog: pytest.LogCaptureFixture, invalid: str
) -> None:
    body = information_payload()
    token = sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"])
    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": str(body["operationId"]),
        "X-Correlation-ID": CORRELATION_ID,
    }
    if invalid == "body":
        body["clienteId"] = "no-registrar-credencial"
    elif invalid == "missing_key":
        del headers["Idempotency-Key"]
    elif invalid == "different_key":
        headers["Idempotency-Key"] = str(uuid4())
    elif invalid == "purpose":
        body["finalidad"] = "no-registrar-credencial"
    else:
        body["correo"] = USER.email
    response = app.test_client().post(INFORMATION, json=body, headers=headers)
    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    fields = audit_event(caplog, "solicitud_invalida", 400)
    assert fields["actorId"] == str(PARTNER.user_id)
    assert "socioId" not in fields
    assert token not in repr(fields)


@pytest.mark.parametrize(
    "code,status,public_code",
    [
        ("producto_no_autorizado", 403, "producto_no_autorizado"),
        ("consentimiento_ausente", 403, "consentimiento_invalido"),
        ("consentimiento_no_vigente", 403, "consentimiento_invalido"),
        ("finalidad_no_autorizada", 403, "finalidad_no_autorizada"),
        ("conflicto_idempotencia", 409, "idempotencia_conflicto"),
    ],
)
def test_client_information_controlled_error_keeps_contract_and_audits_trusted_context(
    app: Flask,
    keypair: KeyPair,
    caplog: pytest.LogCaptureFixture,
    code: str,
    status: int,
    public_code: str,
) -> None:
    class RejectedGateway:
        def consult(self, payload: dict[str, object], correlation_id: str) -> None:
            assert correlation_id == CORRELATION_ID
            raise ClientInformationError(code, status)

    app.extensions["client_information_gateway"] = RejectedGateway()
    body = information_payload()
    token = sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"])
    response = app.test_client().post(
        INFORMATION,
        json=body,
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": str(body["operationId"]),
            "X-Correlation-ID": CORRELATION_ID,
        },
    )
    assert response.status_code == status
    assert response.get_json()["code"] == public_code
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    assert "Retry-After" not in response.headers
    fields = audit_event(caplog, public_code, status)
    assert fields["actorId"] == str(PARTNER.user_id)
    assert fields["socioId"] == "BANCO-001"
    assert token not in repr(fields)


def test_client_information_unavailable_product_is_audited_after_membership(
    app: Flask, keypair: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    body = {**information_payload(), "productoId": "not-in-catalog"}
    response = app.test_client().post(
        INFORMATION,
        json=body,
        headers={
            "Authorization": "Bearer "
            + sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"]),
            "Idempotency-Key": str(body["operationId"]),
            "X-Correlation-ID": CORRELATION_ID,
        },
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "producto_no_autorizado"
    fields = audit_event(caplog, "producto_no_autorizado", 403)
    assert fields["actorId"] == str(PARTNER.user_id)
    assert fields["socioId"] == "BANCO-001"


def test_client_information_success_and_replay_add_no_rejection_or_context_leak(
    app: Flask, keypair: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = ClientInformationGatewayStub()
    result = gateway.consult(
        {
            **information_payload(),
            "actorId": str(PARTNER.user_id),
        },
        CORRELATION_ID,
    )
    calls: list[dict[str, object]] = []

    class PersistedGateway:
        def consult(
            self, payload: dict[str, object], correlation_id: str
        ) -> ClientInformationResult:
            assert correlation_id == CORRELATION_ID
            calls.append(payload)
            return result

    app.extensions["client_information_gateway"] = PersistedGateway()
    body = information_payload()
    body["operationId"] = str(result.operation_id)
    client = app.test_client()
    headers = {
        "Authorization": "Bearer "
        + sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"]),
        "Idempotency-Key": str(body["operationId"]),
        "X-Correlation-ID": CORRELATION_ID,
    }
    for _ in range(2):
        response = client.post(INFORMATION, json=body, headers=headers)
        assert response.status_code == 201
        assert response.get_json() == result.model_dump(mode="json", by_alias=True)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["API-Version"] == "1"
        assert response.headers["X-Correlation-ID"] == CORRELATION_ID
        assert not [record for record in caplog.records if getattr(record, "event", None)]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0]["actorId"] == str(PARTNER.user_id)
    assert "usuarioId" not in calls[0]
    denied = client.post(INFORMATION, json=body, headers={"X-Correlation-ID": CORRELATION_ID})
    assert denied.status_code == 401
    fields = audit_event(caplog, "sesion_invalida", 401)
    assert "actorId" not in fields and "socioId" not in fields
