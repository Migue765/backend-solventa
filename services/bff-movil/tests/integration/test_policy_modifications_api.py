from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationImpact,
    ModificationRejectedError,
    ModificationRequest,
    ModificationResult,
    ModificationState,
    PolicyCoverage,
    PolicyNotFoundError,
    PolicyNotModifiableError,
    PolicyServiceUnavailableError,
)
from bff_movil.infrastructure.http_policy_modification_gateway import (
    HttpPolicyModificationGateway,
)
from flask import Flask
from policy_modification_data import (
    CHANGE,
    CONFIRMATION,
    POLICY_ID,
    coverages,
    impact,
    result,
    state,
)
from support import USER, KeyPair, sign_token


@dataclass
class StubModificationGateway:
    failure: Exception | None = None
    status: int = 201
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def _fail(self) -> None:
        if self.failure is not None:
            raise self.failure

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> ModificationState:
        self.calls.append(("get", policy_id, client_id))
        self._fail()
        return ModificationState.model_validate(state())

    def simulate(
        self, policy_id: UUID, client_id: UUID, request: ModificationRequest, correlation_id: str
    ) -> ModificationImpact:
        self.calls.append(("simulate", policy_id, client_id, request.plan))
        self._fail()
        return ModificationImpact.model_validate(impact())

    def confirm(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        confirmation: ModificationConfirmation,
        correlation_id: str,
    ) -> tuple[ModificationResult, int]:
        self.calls.append(("confirm", policy_id, client_id, idempotency_key))
        self._fail()
        return ModificationResult.model_validate(result()), self.status


@pytest.fixture
def gateway(app: Flask) -> StubModificationGateway:
    stub = StubModificationGateway()
    app.extensions["policy_modification_gateway"] = stub
    return stub


@pytest.fixture
def token(keypair: KeyPair) -> str:
    return sign_token(keypair, scope="cliente")


def auth(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


BASE = f"/movil/v1/polizas/{POLICY_ID}"


def test_consulta_simula_y_confirma_con_cliente_de_la_sesion(
    app: Flask, gateway: StubModificationGateway, token: str
) -> None:
    client = app.test_client()
    consulted = client.get(BASE + "/modificacion", headers=auth(token))
    assert consulted.status_code == 200
    assert consulted.headers["Cache-Control"] == "no-store"
    assert consulted.headers["X-Content-Type-Options"] == "nosniff"
    assert consulted.get_json()["versionActual"]["version"] == 1
    assert consulted.get_json()["opciones"]["planes"][0]["codigo"] == "premium"

    simulated = client.post(BASE + "/modificaciones/simulacion", json=CHANGE, headers=auth(token))
    assert simulated.status_code == 200
    assert simulated.get_json()["diferenciaMensual"]["monto"] == "-300"
    assert simulated.get_json()["vigencia"]["cambia"] is False

    key = str(uuid4())
    confirmed = client.post(BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, key))
    assert confirmed.status_code == 201
    assert confirmed.get_json()["versionNueva"] == 2
    assert gateway.calls == [
        ("get", POLICY_ID, USER.user_id),
        ("simulate", POLICY_ID, USER.user_id, "premium"),
        ("confirm", POLICY_ID, USER.user_id, UUID(key)),
    ]


def test_reintento_idempotente_conserva_el_estado_del_servicio(
    app: Flask, gateway: StubModificationGateway, token: str
) -> None:
    gateway.status = 200
    response = app.test_client().post(
        BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == 200
    assert response.get_json()["versionAnterior"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {**CONFIRMATION, "confirma": False},
        {**CONFIRMATION, "confirma": "true"},
        {key: value for key, value in CONFIRMATION.items() if key != "confirma"},
        {**CONFIRMATION, "impactoHash": "corto"},
        {**CONFIRMATION, "fechaFin": "2030-01-01"},
        {**CONFIRMATION, "prima": "1000"},
        {**CONFIRMATION, "plan": "oro"},
        {**CONFIRMATION, "versionBase": "1"},
        {**CONFIRMATION, "coberturasOpcionales": ["a", "a"]},
        None,
    ],
)
def test_confirmacion_explicita_y_campos_permitidos(
    app: Flask, gateway: StubModificationGateway, token: str, body: object
) -> None:
    response = app.test_client().post(
        BASE + "/modificaciones", json=body, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"
    assert gateway.calls == []


@pytest.mark.parametrize(
    "body", [{**CHANGE, "fechaFin": "2030-01-01"}, {**CHANGE, "plan": "oro"}, [], None]
)
def test_simulacion_solo_admite_campos_permitidos(
    app: Flask, gateway: StubModificationGateway, token: str, body: object
) -> None:
    response = app.test_client().post(
        BASE + "/modificaciones/simulacion", json=body, headers=auth(token)
    )
    assert response.status_code == 400
    assert gateway.calls == []


def test_valida_identificadores_sesion_y_alcance(
    app: Flask, gateway: StubModificationGateway, token: str, keypair: KeyPair
) -> None:
    client = app.test_client()
    assert (
        client.post(BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token)).status_code
        == 400
    )
    assert (
        client.post(
            BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, "no-uuid")
        ).status_code
        == 400
    )
    assert client.get("/movil/v1/polizas/x/modificacion", headers=auth(token)).status_code == 400
    assert client.get(BASE + "/modificacion?a=1", headers=auth(token)).status_code == 400
    assert (
        client.post(
            "/movil/v1/polizas/x/modificaciones/simulacion", json=CHANGE, headers=auth(token)
        ).status_code
        == 400
    )
    assert client.get(BASE + "/modificacion").status_code == 401
    assert client.get(BASE + "/modificacion", headers=auth("no-es-jwt")).status_code == 401
    web = sign_token(keypair, scope="cliente", client_id="web")
    assert client.get(BASE + "/modificacion", headers=auth(web)).status_code == 401
    advisor = sign_token(keypair, scope="cotizaciones")
    forbidden = client.get(BASE + "/modificacion", headers=auth(advisor))
    assert forbidden.status_code == 403
    assert forbidden.get_json()["code"] == "alcance_insuficiente"
    assert gateway.calls == []


def test_limita_el_cuerpo(app: Flask, gateway: StubModificationGateway, token: str) -> None:
    client = app.test_client()
    large = client.post(
        BASE + "/modificaciones/simulacion",
        data=b"{" + b" " * (16 * 1024) + b"}",
        headers={**auth(token), "Content-Type": "application/json"},
    )
    assert large.status_code == 413
    chunked = client.post(
        BASE + "/modificaciones",
        data=b"{}",
        headers={
            **auth(token, str(uuid4())),
            "Content-Type": "application/json",
            "Transfer-Encoding": "chunked",
        },
    )
    assert chunked.status_code == 411
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code", "extensions"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada", {}),
        (
            PolicyNotModifiableError("poliza_vencida"),
            422,
            "poliza_no_modificable",
            {"motivo": "poliza_vencida"},
        ),
        (
            ModificationRejectedError("cambio_no_permitido", "coberturasOpcionales.x"),
            422,
            "cambio_no_permitido",
            {"campo": "coberturasOpcionales.x", "accion": "ajustar_cambios"},
        ),
        (
            ModificationRejectedError("sin_cambios"),
            422,
            "sin_cambios",
            {"accion": "ajustar_cambios"},
        ),
        (
            ModificationConflictError("version_desactualizada", 3),
            409,
            "version_desactualizada",
            {"versionActual": 3, "accion": "consultar_poliza"},
        ),
        (
            ModificationConflictError("impacto_cambio"),
            409,
            "impacto_cambio",
            {"accion": "simular_de_nuevo"},
        ),
        (
            ModificationConflictError("idempotencia_conflictiva"),
            409,
            "idempotencia_conflictiva",
            {"accion": "nueva_operacion"},
        ),
        (
            PolicyServiceUnavailableError(),
            503,
            "servicio_no_disponible",
            {"accion": "reintentar_misma_clave"},
        ),
    ],
)
def test_rechazos_de_confirmacion_indican_causa_recuperable(
    app: Flask,
    gateway: StubModificationGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
    extensions: dict[str, object],
) -> None:
    gateway.failure = failure
    response = app.test_client().post(
        BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    body = response.get_json()
    assert body["code"] == code
    assert {name: body.get(name) for name in extensions} == extensions
    if status == 503:
        assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada"),
        (PolicyNotModifiableError("fin_de_vigencia"), 422, "poliza_no_modificable"),
        (ModificationRejectedError("cambio_no_permitido", "plan"), 422, "cambio_no_permitido"),
        (ModificationConflictError("version_desactualizada", 2), 409, "version_desactualizada"),
        (PolicyServiceUnavailableError(), 503, "servicio_no_disponible"),
    ],
)
def test_rechazos_de_simulacion(
    app: Flask,
    gateway: StubModificationGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
) -> None:
    gateway.failure = failure
    response = app.test_client().post(
        BASE + "/modificaciones/simulacion", json=CHANGE, headers=auth(token)
    )
    assert response.status_code == status
    assert response.get_json()["code"] == code
    if status == 503:
        assert response.get_json()["accion"] == "reintentar"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada"),
        (PolicyServiceUnavailableError(), 503, "servicio_no_disponible"),
    ],
)
def test_fallos_de_consulta(
    app: Flask,
    gateway: StubModificationGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
) -> None:
    gateway.failure = failure
    response = app.test_client().get(BASE + "/modificacion", headers=auth(token))
    assert response.status_code == status
    assert response.get_json()["code"] == code


def test_sin_configuracion_responde_no_disponible(app: Flask, token: str) -> None:
    response = app.test_client().get(BASE + "/modificacion", headers=auth(token))
    assert response.status_code == 503
    assert response.get_json()["accion"] == "reintentar"


def test_lee_la_url_de_polizas_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad")
    monkeypatch.setenv("JWT_ISSUER", "https://iss")
    monkeypatch.setenv("POLIZAS_SERVICE_URL", "http://polizas.svc:8080/")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "interno")
    config = Config.from_environment()
    assert config.policies_service_url == "http://polizas.svc:8080"
    gateway = create_app(config).extensions["policy_modification_gateway"]
    assert isinstance(gateway, HttpPolicyModificationGateway)


def test_modelos_rechazan_respuestas_incoherentes() -> None:
    with pytest.raises(ValueError):
        ModificationImpact.model_validate(
            {**impact(), "diferenciaMensual": {"monto": "1", "moneda": "COP"}}
        )
    with pytest.raises(ValueError):
        ModificationImpact.model_validate({**impact(), "efectivoDesde": "2027-01-01"})
    with pytest.raises(ValueError):
        ModificationResult.model_validate({**result(), "versionNueva": 3})
    with pytest.raises(ValueError):
        ModificationState.model_validate({**state(), "historial": []})
    with pytest.raises(ValueError):
        ModificationState.model_validate({**state(), "motivo": "poliza_vencida"})
    with pytest.raises(ValueError):
        ModificationState.model_validate(
            {
                **state(),
                "versionActual": None,
                "modificable": False,
                "motivo": "poliza_vencida",
                "opciones": None,
            }
        )
    with pytest.raises(ValueError):
        ModificationState.model_validate(
            {**state(), "vigencia": {"inicio": "2026-12-11", "fin": "2026-11-11"}}
        )
    with pytest.raises(ValueError):
        PolicyCoverage.model_validate({**coverages()[0], "incluida": False})
