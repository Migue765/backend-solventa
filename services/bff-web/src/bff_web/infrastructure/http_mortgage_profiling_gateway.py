import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.mortgage_profiling import (
    MortgageProfilingCommand,
    MortgageProfilingError,
    MortgageProfilingResult,
)


class HttpMortgageProfilingGateway:
    def __init__(
        self,
        base_url: str,
        internal_api_token: str,
        timeout_seconds: float = 0.8,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_api_token = internal_api_token
        self._timeout_seconds = timeout_seconds

    def start(
        self, command: MortgageProfilingCommand, correlation_id: str
    ) -> MortgageProfilingResult:
        if not self._internal_api_token:
            raise MortgageProfilingError("servicio_no_disponible", 503)
        request = Request(
            f"{self._base_url}/v1/perfilamientos-hipotecarios",
            data=json.dumps(_command_json(command)).encode(),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._internal_api_token}",
                "Idempotency-Key": str(command.operation_id),
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as error:
            raise _controlled_error(error) from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise MortgageProfilingError("servicio_no_disponible", 503) from error
        if not isinstance(payload, dict):
            raise MortgageProfilingError("servicio_no_disponible", 503)
        return _result(payload)


def _command_json(command: MortgageProfilingCommand) -> dict[str, object]:
    return {
        "operationId": str(command.operation_id),
        "socioId": command.partner_id,
        "asesorId": str(command.advisor_id),
        "cliente": {
            "clienteId": str(command.client.client_id),
            "tipoDocumento": command.client.document_type,
            "numeroDocumento": command.client.identification,
            "correo": command.client.email,
            "telefono": command.client.phone,
            "fechaNacimiento": command.client.birth_date.isoformat(),
        },
        "credito": {
            "referencia": command.credit.reference,
            "monto": str(command.credit.amount),
            "plazoMeses": command.credit.term_months,
        },
        "consentimientoId": str(command.consent_id),
        "consentimientoVersion": command.consent_version,
    }


def _controlled_error(error: HTTPError) -> MortgageProfilingError:
    try:
        payload = json.load(error)
        code = payload["error"]
    except (ValueError, KeyError, TypeError):
        return MortgageProfilingError("servicio_no_disponible", 503)
    allowed = {
        400: {"solicitud_invalida"},
        403: {"consentimiento_ausente", "consentimiento_no_vigente"},
        409: {"conflicto_idempotencia"},
        422: {"perfil_vencido", "regla_de_negocio"},
        503: {"perfilamiento_no_disponible", "servicio_no_disponible"},
    }
    if not isinstance(code, str) or code not in allowed.get(error.code, set()):
        return MortgageProfilingError("servicio_no_disponible", 503)
    return MortgageProfilingError(code, error.code)


def _result(payload: dict[str, Any]) -> MortgageProfilingResult:
    try:
        return MortgageProfilingResult(
            operation_id=UUID(payload["operationId"]),
            partner_id=str(payload["socioId"]),
            advisor_id=UUID(payload["asesorId"]),
            client_id=UUID(payload["clienteId"]),
            status=str(payload["estado"]),
            profiling_reference=str(payload["perfilamientoReferencia"]),
            request_id=str(payload["solicitudId"]),
            quote_id=UUID(payload["cotizacionId"]),
            received_at=datetime.fromisoformat(str(payload["recibidaEn"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MortgageProfilingError("servicio_no_disponible", 503) from error
