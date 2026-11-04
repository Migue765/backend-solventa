import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import UUID, uuid4

import pytest
from bff_web.domain.claims import ClaimNotFoundError, ClaimServiceUnavailableError
from bff_web.infrastructure.http_claim_gateway import HttpClaimGateway

TARGET = "bff_web.infrastructure.http_claim_gateway.urlopen"


def status_payload(claim_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(claim_id),
        "polizaId": str(uuid4()),
        "tipo": "asistido",
        "estado": "en_evaluacion",
        "descripcion": "Estamos evaluando la informacion del caso.",
        "ocurridoEn": "2026-11-01T09:30:00-05:00",
        "reportadoEn": "2026-11-01T15:00:00Z",
        "actualizadoEn": "2026-11-01T16:00:00Z",
        "historial": [
            {
                "estado": "reportado",
                "descripcion": "Recibimos el aviso de tu siniestro.",
                "ocurridoEn": "2026-11-01T15:00:00Z",
            },
            {
                "estado": "en_evaluacion",
                "descripcion": "Estamos evaluando la informacion del caso.",
                "ocurridoEn": "2026-11-01T16:00:00Z",
            },
        ],
    }
    payload.update(overrides)
    return payload


def http_error(status: int, body: object) -> HTTPError:
    return HTTPError(
        "http://siniestros", status, "error", Message(), io.BytesIO(json.dumps(body).encode())
    )


def test_consulta_con_cliente_de_sesion_token_y_correlacion() -> None:
    claim_id, client_id = uuid4(), uuid4()

    def respond(request: Request, timeout: float) -> io.BytesIO:
        assert request.full_url == (
            f"http://siniestros/v1/siniestros/{claim_id}?clienteId={client_id}"
        )
        assert request.get_header("Authorization") == "Bearer token-de-prueba"
        assert request.get_header("X-correlation-id") == "corr-prueba"
        assert timeout == 0.8
        return io.BytesIO(json.dumps(status_payload(claim_id)).encode())

    with patch(TARGET, side_effect=respond):
        status = HttpClaimGateway("http://siniestros/", "token-de-prueba").get_status(
            claim_id, client_id, "corr-prueba"
        )

    assert status.state == "en_evaluacion"
    assert len(status.history) == 2


def test_siniestro_no_encontrado() -> None:
    with (
        patch(TARGET, side_effect=http_error(404, {"error": "siniestro_no_encontrado"})),
        pytest.raises(ClaimNotFoundError),
    ):
        HttpClaimGateway("http://siniestros", "t").get_status(uuid4(), uuid4(), "c")


@pytest.mark.parametrize(
    "failure",
    [
        http_error(404, {"error": "otra_ruta"}),
        http_error(404, "no-json"),
        http_error(400, {"error": "solicitud_invalida"}),
        http_error(401, {"error": "no_autorizado"}),
        http_error(503, {"error": "siniestro_no_integro"}),
        URLError("caido"),
        TimeoutError(),
    ],
)
def test_fallos_no_se_presentan_como_estado(failure: Exception) -> None:
    with patch(TARGET, side_effect=failure), pytest.raises(ClaimServiceUnavailableError):
        HttpClaimGateway("http://siniestros", "t").get_status(uuid4(), uuid4(), "c")


@pytest.mark.parametrize(
    "overrides",
    [
        {"estado": "inventado"},
        {"historial": []},
        {"estado": "aprobado"},
        {"actualizadoEn": "2026-11-01T17:00:00Z"},
        {"actualizadoEn": "2026-11-01T16:00:00"},
        {"tipo": "otro"},
        {"ocurridoEn": "2026-11-01T16:30:00Z"},
        {"interno": "x"},
        {"id": str(uuid4())},
    ],
    ids=[
        "estado",
        "vacio",
        "vigente",
        "fecha",
        "sin-zona",
        "tipo",
        "evento-posterior",
        "extra",
        "otro-id",
    ],
)
def test_respuesta_incoherente_no_se_presenta_como_estado(overrides: dict[str, object]) -> None:
    claim_id = uuid4()
    body = io.BytesIO(json.dumps(status_payload(claim_id, **overrides)).encode())

    with patch(TARGET, return_value=body), pytest.raises(ClaimServiceUnavailableError):
        HttpClaimGateway("http://siniestros", "t").get_status(claim_id, uuid4(), "c")


def test_sin_credencial_interna_no_consulta() -> None:
    with patch(TARGET) as urlopen, pytest.raises(ClaimServiceUnavailableError):
        HttpClaimGateway("http://siniestros", "").get_status(uuid4(), uuid4(), "c")

    urlopen.assert_not_called()
