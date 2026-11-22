import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.compliance import ComplianceError, StoredReport
from bff_web.domain.operational_reports import ReportSourceUnavailableError
from bff_web.infrastructure.http_report_sources import _NoRedirect

MAX_BYTES = 12 * 1024 * 1024
ERRORS = {
    (404, "recurso_no_encontrado"),
    (409, "idempotencia_conflictiva"),
    (422, "sin_partidas_para_conciliar"),
}


class HttpComplianceGateway:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0) -> None:
        self._url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect)

    def call(
        self,
        path: str,
        actor: UUID,
        correlation_id: str,
        body: dict[str, Any] | None = None,
        key: UUID | None = None,
    ) -> tuple[dict[str, Any], int]:
        if not self._url or not self._token:
            raise ReportSourceUnavailableError("cumplimiento")
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Actor-ID": str(actor),
            "X-Actor-Rol": "asesor",
            "X-Correlation-ID": correlation_id,
        }
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        request = Request(
            self._url + "/internal/v1/cumplimiento" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(MAX_BYTES + 1)
                status = response.status
                if status not in {200, 201} or len(raw) > MAX_BYTES:
                    raise ReportSourceUnavailableError("cumplimiento")
                payload = json.loads(raw)
        except HTTPError as error:
            try:
                failure = json.loads(error.read(4096))
            except ValueError:
                failure = None
            if isinstance(failure, dict) and (error.code, failure.get("error")) in ERRORS:
                raise ComplianceError(str(failure["error"]), error.code) from error
            raise ReportSourceUnavailableError("cumplimiento") from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise ReportSourceUnavailableError("cumplimiento") from error
        if not isinstance(payload, dict):
            raise ReportSourceUnavailableError("cumplimiento")
        try:
            if path.startswith("/reportes"):
                report = StoredReport.model_validate(payload)
                if body is not None and report.contenidoHash != body["contenidoHash"]:
                    raise ValueError("Huella distinta de la enviada.")
                if (
                    body is None
                    and not path.startswith("/reportes/operaciones/")
                    and str(report.reporteId) != path.split("/")[2]
                ):
                    raise ValueError("Recurso distinto del solicitado.")
            else:
                UUID(payload["intercambioId"])
                UUID(payload["reporteId"])
                if (
                    payload.get("simulado") is not True
                    or payload.get("adaptadorVersion") != "solventa-reaseguro-academico-v1"
                    or payload.get("estado") not in {"conciliado", "revision_pendiente"}
                    or payload.get("envioId") != payload["intercambioId"]
                    or not isinstance(payload.get("acuse"), dict)
                    or payload["acuse"].get("simulado") is not True
                    or payload["acuse"].get("envioId") != payload["envioId"]
                ):
                    raise ValueError("Intercambio no verificable.")
                if body is not None and payload["reporteId"] != body["reporteId"]:
                    raise ValueError("Reporte distinto del enviado.")
                if body is None and payload["intercambioId"] != path.split("/")[2]:
                    raise ValueError("Intercambio distinto del solicitado.")
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise ReportSourceUnavailableError("cumplimiento") from error
        return payload, status
