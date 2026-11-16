import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from uuid import UUID, uuid4

import pytest
from bff_web.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationRejectedError,
    ModificationRequest,
    PolicyNotModifiableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.infrastructure.http_policy_modification_gateway import (
    HttpPolicyModificationGateway,
)

TARGET = "bff_web.infrastructure.http_policy_renewal_gateway.urlopen"
HASH = "f" * 64
REQUEST = ModificationRequest.model_validate(
    {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}
)
CONFIRMATION = ModificationConfirmation.model_validate(
    {
        "versionBase": 1,
        "plan": "premium",
        "coberturasOpcionales": [],
        "confirma": True,
        "impactoHash": HASH,
    }
)


class Reply(io.BytesIO):
    def __init__(self, body: object, status: int = 200) -> None:
        super().__init__(json.dumps(body).encode())
        self.status = status


def impact(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "versionBase": 1,
        "plan": {"anterior": "esencial", "nuevo": "premium"},
        "coberturas": [
            {
                "codigo": "fallecimiento",
                "nombre": "Fallecimiento",
                "descripcion": "Respaldo.",
                "opcional": False,
                "incluida": True,
            }
        ],
        "coberturasAgregadas": [],
        "coberturasRetiradas": [],
        "primaAnterior": {"monto": "19000", "moneda": "COP"},
        "primaNueva": {"monto": "24700", "moneda": "COP"},
        "diferenciaMensual": {"monto": "5700", "moneda": "COP"},
        "ajusteProrrateado": {"monto": "4000", "moneda": "COP"},
        "diasRestantes": 20,
        "efectivoDesde": "2026-11-16",
        "vigencia": {"inicio": "2026-11-11", "fin": "2026-12-11", "cambia": False},
        "reglaVersion": "academica-modificacion-v1",
        "impactoHash": HASH,
    }
    payload.update(overrides)
    return payload


def result(policy_id: UUID, **overrides: object) -> dict[str, object]:
    endorsement = str(uuid4())
    payload: dict[str, object] = {
        "endosoId": endorsement,
        "polizaId": str(policy_id),
        "versionAnterior": 1,
        "versionNueva": 2,
        "version": {
            "version": 2,
            "plan": "premium",
            "coberturas": impact()["coberturas"],
            "prima": {"monto": "24700", "moneda": "COP", "periodicidad": "mensual"},
            "efectivoDesde": "2026-11-16",
            "origen": "modificacion",
            "endosoId": endorsement,
            "creadaEn": "2026-11-15T15:00:00Z",
        },
        "impacto": impact(),
        "creadoEn": "2026-11-15T15:00:00Z",
    }
    payload.update(overrides)
    return payload


def http_error(status: int, body: object) -> HTTPError:
    return HTTPError(
        "http://polizas", status, "error", Message(), io.BytesIO(json.dumps(body).encode())
    )


def gateway() -> HttpPolicyModificationGateway:
    return HttpPolicyModificationGateway("http://polizas", "token-de-prueba")


def test_simulacion_envia_propuesta_y_valida_impacto() -> None:
    policy_id, client_id = uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> Reply:
        assert (
            request.full_url == f"http://polizas/v1/polizas/{policy_id}/modificaciones/simulacion"
        )
        assert request.get_header("X-cliente-id") == str(client_id)
        assert isinstance(request.data, bytes)
        assert json.loads(request.data) == {
            "versionBase": 1,
            "plan": "premium",
            "coberturasOpcionales": [],
        }
        return Reply(impact())

    with patch(TARGET, side_effect=respond):
        assert gateway().simulate(policy_id, client_id, REQUEST, "corr").impact_hash == HASH


def test_confirmacion_envia_clave_y_valida_version() -> None:
    policy_id, key = uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> Reply:
        assert request.get_header("Idempotency-key") == str(key)
        assert timeout == 5
        return Reply(result(policy_id), 201)

    with patch(TARGET, side_effect=respond):
        confirmed, status = gateway().confirm(policy_id, uuid4(), key, CONFIRMATION, "corr")
    assert status == 201 and confirmed.new_version == 2


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({}, 202),
        ({"polizaId": str(uuid4())}, 201),
        ({"versionAnterior": 2, "versionNueva": 3}, 201),
        ({"impacto": impact(impactoHash="0" * 64)}, 201),
    ],
)
def test_confirmaciones_incoherentes_no_se_aceptan(
    overrides: dict[str, object], status: int
) -> None:
    policy_id = uuid4()
    with (
        patch(TARGET, return_value=Reply(result(policy_id, **overrides), status)),
        pytest.raises(RenewalServiceUnavailableError),
    ):
        gateway().confirm(policy_id, uuid4(), uuid4(), CONFIRMATION, "corr")


def test_impacto_de_otra_propuesta_no_se_acepta() -> None:
    with (
        patch(
            TARGET, return_value=Reply(impact(plan={"anterior": "esencial", "nuevo": "integral"}))
        ),
        pytest.raises(RenewalServiceUnavailableError),
    ):
        gateway().simulate(uuid4(), uuid4(), REQUEST, "corr")


@pytest.mark.parametrize(
    ("error", "expected", "attribute"),
    [
        (http_error(404, {"error": "poliza_no_encontrada"}), PolicyNotFoundError, None),
        (
            http_error(422, {"error": "poliza_no_modificable", "motivo": "fin_de_vigencia"}),
            PolicyNotModifiableError,
            ("reason", "fin_de_vigencia"),
        ),
        (
            http_error(422, {"error": "cambio_no_permitido", "campo": "coberturasOpcionales.x"}),
            ModificationRejectedError,
            ("field", "coberturasOpcionales.x"),
        ),
        (
            http_error(422, {"error": "sin_cambios"}),
            ModificationRejectedError,
            ("code", "sin_cambios"),
        ),
        (
            http_error(409, {"error": "version_desactualizada", "versionActual": 4}),
            ModificationConflictError,
            ("current_version", 4),
        ),
        (
            http_error(409, {"error": "version_desactualizada", "versionActual": True}),
            RenewalServiceUnavailableError,
            None,
        ),
        (
            http_error(409, {"error": "impacto_cambio"}),
            ModificationConflictError,
            ("code", "impacto_cambio"),
        ),
        (http_error(422, {"error": "poliza_no_renovable"}), RenewalServiceUnavailableError, None),
    ],
)
def test_traduce_errores_de_polizas(
    error: Exception, expected: type[Exception], attribute: tuple[str, object] | None
) -> None:
    with patch(TARGET, side_effect=error), pytest.raises(expected) as raised:
        gateway().confirm(uuid4(), uuid4(), uuid4(), CONFIRMATION, "corr")
    if attribute is not None:
        assert getattr(raised.value, attribute[0]) == attribute[1]
