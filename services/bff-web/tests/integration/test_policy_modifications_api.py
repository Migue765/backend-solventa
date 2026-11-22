from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationImpact,
    ModificationRejectedError,
    ModificationRequest,
    ModificationResult,
    ModificationState,
    PolicyNotModifiableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from flask import Flask
from support import USER, KeyPair, sign_token

POLICY_ID = UUID("6a1d7e3c-90b2-4f3f-8c2e-1b6f0e2d4a55")
ENDORSEMENT_ID = UUID("7b2e8f4d-a1c3-4a40-9d3f-2c7a1f3e5b66")
HASH = "e" * 64


def coverages(included: bool = True) -> list[dict[str, object]]:
    return [
        {
            "codigo": "fallecimiento",
            "nombre": "Fallecimiento",
            "descripcion": "Respaldo economico.",
            "opcional": False,
            "incluida": True,
        },
        {
            "codigo": "incapacidad_total",
            "nombre": "Incapacidad total y permanente",
            "descripcion": "Proteccion por incapacidad.",
            "opcional": True,
            "incluida": included,
        },
    ]


def version(number: int = 1, plan: str = "esencial") -> dict[str, object]:
    return {
        "version": number,
        "plan": plan,
        "coberturas": coverages(number == 1),
        "prima": {
            "monto": "25000" if number == 1 else "24700",
            "moneda": "COP",
            "periodicidad": "mensual",
        },
        "efectivoDesde": "2026-11-11" if number == 1 else "2026-11-16",
        "origen": "emision" if number == 1 else "modificacion",
        "endosoId": None if number == 1 else str(ENDORSEMENT_ID),
        "creadaEn": "2026-11-15T15:00:00Z",
    }


def impact() -> dict[str, object]:
    return {
        "versionBase": 1,
        "plan": {"anterior": "esencial", "nuevo": "premium"},
        "coberturas": coverages(False),
        "coberturasAgregadas": [],
        "coberturasRetiradas": ["incapacidad_total"],
        "primaAnterior": {"monto": "25000", "moneda": "COP"},
        "primaNueva": {"monto": "24700", "moneda": "COP"},
        "diferenciaMensual": {"monto": "-300", "moneda": "COP"},
        "ajusteProrrateado": {"monto": "-260", "moneda": "COP"},
        "diasRestantes": 26,
        "efectivoDesde": "2026-11-16",
        "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11", "cambia": False},
        "reglaVersion": "academica-modificacion-v1",
        "impactoHash": HASH,
    }


def state() -> ModificationState:
    return ModificationState.model_validate(
        {
            "polizaId": str(POLICY_ID),
            "tipo": "life",
            "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11"},
            "modificable": True,
            "motivo": None,
            "versionActual": version(),
            "opciones": {
                "planes": [{"codigo": "premium", "nombre": "Premium", "factor": "1.30"}],
                "coberturasOpcionales": [
                    {
                        "codigo": "incapacidad_total",
                        "nombre": "Incapacidad total y permanente",
                        "recargo": {"monto": "6000", "moneda": "COP"},
                    }
                ],
            },
            "historial": [version()],
        }
    )


def result() -> ModificationResult:
    return ModificationResult.model_validate(
        {
            "endosoId": str(ENDORSEMENT_ID),
            "polizaId": str(POLICY_ID),
            "versionAnterior": 1,
            "versionNueva": 2,
            "version": version(2, "premium"),
            "impacto": impact(),
            "creadoEn": "2026-11-15T15:00:00Z",
        }
    )


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
        return state()

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
        return result(), self.status


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


BASE = f"/v1/clientes/me/polizas/{POLICY_ID}"
CHANGE = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}
CONFIRMATION = {**CHANGE, "confirma": True, "impactoHash": HASH}


def test_consulta_simula_y_confirma_con_cliente_de_la_sesion(
    app: Flask, gateway: StubModificationGateway, token: str
) -> None:
    client = app.test_client()
    consulted = client.get(BASE + "/modificacion", headers=auth(token))
    assert consulted.status_code == 200
    assert consulted.headers["Cache-Control"] == "no-store"
    assert consulted.get_json()["versionActual"]["version"] == 1

    simulated = client.post(BASE + "/modificaciones/simulacion", json=CHANGE, headers=auth(token))
    assert simulated.status_code == 200
    assert simulated.get_json()["diferenciaMensual"]["monto"] == "-300"

    key = str(uuid4())
    confirmed = client.post(BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, key))
    assert confirmed.status_code == 201
    assert confirmed.get_json()["versionNueva"] == 2
    assert gateway.calls == [
        ("get", POLICY_ID, USER.user_id),
        ("simulate", POLICY_ID, USER.user_id, "premium"),
        ("confirm", POLICY_ID, USER.user_id, UUID(key)),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {**CONFIRMATION, "confirma": False},
        {**CONFIRMATION, "confirma": "true"},
        {**CONFIRMATION, "impactoHash": "corto"},
        {**CONFIRMATION, "fechaFin": "2030-01-01"},
        {**CONFIRMATION, "plan": "oro"},
        {**CONFIRMATION, "versionBase": "1"},
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
    assert gateway.calls == []


def test_valida_identificadores_y_sesion(
    app: Flask, gateway: StubModificationGateway, token: str, keypair: KeyPair
) -> None:
    client = app.test_client()
    assert (
        client.post(BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token)).status_code
        == 400
    )
    assert (
        client.get("/v1/clientes/me/polizas/x/modificacion", headers=auth(token)).status_code == 400
    )
    assert client.get(BASE + "/modificacion").status_code == 401
    advisor = sign_token(keypair, scope="cotizaciones")
    assert client.get(BASE + "/modificacion", headers=auth(advisor)).status_code == 403
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code", "extension"),
    [
        (PolicyNotFoundError(), 404, "poliza_no_encontrada", None),
        (
            PolicyNotModifiableError("poliza_vencida"),
            422,
            "poliza_no_modificable",
            ("motivo", "poliza_vencida"),
        ),
        (
            ModificationRejectedError("cambio_no_permitido", "coberturasOpcionales.x"),
            422,
            "cambio_no_permitido",
            ("campo", "coberturasOpcionales.x"),
        ),
        (ModificationRejectedError("sin_cambios"), 422, "sin_cambios", None),
        (
            ModificationConflictError("version_desactualizada", 3),
            409,
            "version_desactualizada",
            ("versionActual", 3),
        ),
        (ModificationConflictError("impacto_cambio"), 409, "impacto_cambio", None),
        (RenewalServiceUnavailableError(), 503, "servicio_no_disponible", None),
    ],
)
def test_rechazos_y_fallos_son_problemas(
    app: Flask,
    gateway: StubModificationGateway,
    token: str,
    failure: Exception,
    status: int,
    code: str,
    extension: tuple[str, object] | None,
) -> None:
    gateway.failure = failure
    response = app.test_client().post(
        BASE + "/modificaciones", json=CONFIRMATION, headers=auth(token, str(uuid4()))
    )
    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    assert response.get_json()["code"] == code
    if extension is not None:
        assert response.get_json()[extension[0]] == extension[1]


def test_modelos_rechazan_respuestas_incoherentes() -> None:
    incoherent = {**impact(), "diferenciaMensual": {"monto": "1", "moneda": "COP"}}
    with pytest.raises(ValueError):
        ModificationImpact.model_validate(incoherent)
    payload = result().model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError):
        ModificationResult.model_validate({**payload, "versionNueva": 3})
    bad_state = state().model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError):
        ModificationState.model_validate({**bad_state, "historial": []})
