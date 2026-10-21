import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

import pytest
from bff_web.domain.profiling import ProfilingError
from bff_web.infrastructure.http_profiling_gateway import HttpProfilingGateway


def gateway() -> HttpProfilingGateway:
    return HttpProfilingGateway("http://identidad", "http://cotizacion", "token-de-prueba")


@pytest.mark.parametrize("scope", [None, {"finalidad": "cotizacion", "fuentes": ["open_data"]}])
def test_envia_identidad_de_sesion_token_y_correlacion(scope: dict[str, object] | None) -> None:
    user_id = uuid4()
    payload = {"estado": "autorizado", "version": 1}

    def respond(request: Request, timeout: float) -> io.BytesIO:
        assert request.full_url == (
            f"http://identidad/v1/clientes/{user_id}/consentimiento-perfilamiento"
        )
        assert request.get_header("Authorization") == "Bearer token-de-prueba"
        assert request.get_header("X-correlation-id") == "corr-prueba"
        assert request.method == "PUT"
        assert isinstance(request.data, bytes)
        expected: dict[str, object] = {"autorizado": True, "version": 0}
        if scope is not None:
            expected["solicitud"] = scope
        assert json.loads(request.data) == expected
        assert timeout == 5
        return io.BytesIO(json.dumps(payload).encode())

    with patch("bff_web.infrastructure.http_profiling_gateway.urlopen", side_effect=respond):
        assert gateway().set_consent(user_id, True, 0, "corr-prueba", scope) == payload


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, "solicitud_invalida", "solicitud_invalida"),
        (403, "consentimiento_no_vigente", "consentimiento_no_vigente"),
        (404, "consentimiento_ausente", "consentimiento_ausente"),
        (404, "no_encontrado", "solicitud_no_encontrada"),
        (422, "perfil_vencido", "perfil_vencido"),
        (409, "conflicto_version", "conflicto_version"),
        (500, "mensaje_privado_del_proveedor", "perfilamiento_no_disponible"),
    ],
)
def test_propaga_solo_errores_controlados(status: int, code: str, expected: str) -> None:
    error = HTTPError(
        "http://interno",
        status,
        "fallo",
        Message(),
        io.BytesIO(json.dumps({"error": code}).encode()),
    )
    with patch("bff_web.infrastructure.http_profiling_gateway.urlopen", side_effect=error):
        with pytest.raises(ProfilingError) as caught:
            gateway().get_consent(uuid4(), "corr")
    assert caught.value.code == expected
    assert caught.value.status == (status if status != 500 else 503)


def test_timeout_y_respuesta_invalida_no_parecen_un_perfil_disponible() -> None:
    with patch(
        "bff_web.infrastructure.http_profiling_gateway.urlopen", side_effect=URLError("timeout")
    ):
        with pytest.raises(ProfilingError, match="perfilamiento_no_disponible"):
            gateway().get_profile(uuid4(), "AQT-2026-ABCDEF1234", "corr")
    with patch(
        "bff_web.infrastructure.http_profiling_gateway.urlopen",
        return_value=io.BytesIO(b"[]"),
    ):
        with pytest.raises(ProfilingError, match="perfilamiento_no_disponible"):
            gateway().get_profile(uuid4(), "AQT-2026-ABCDEF1234", "corr")
