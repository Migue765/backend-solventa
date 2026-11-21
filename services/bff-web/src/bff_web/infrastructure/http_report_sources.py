import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, TypeVar
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from pydantic import BaseModel, ValidationError

from bff_web.domain.operational_reports import (
    ClaimRecord,
    PaymentRecord,
    PolicyRecord,
    PremiumRecord,
    ReportSnapshot,
    ReportSourceUnavailableError,
    SourceTrace,
    fingerprint,
    instant,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
Record = TypeVar("Record", bound=BaseModel)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpReportSources:
    def __init__(
        self,
        policies_url: str,
        premiums_url: str,
        claims_url: str,
        internal_api_token: str,
        timeout: float = 3.0,
    ) -> None:
        self._policies_url = policies_url.rstrip("/")
        self._premiums_url = premiums_url.rstrip("/")
        self._claims_url = claims_url.rstrip("/")
        self._token = internal_api_token
        self._timeout = timeout
        self._opener: OpenerDirector = build_opener(_NoRedirect)

    def fetch(self, cut_off: datetime, correlation_id: str) -> ReportSnapshot:
        expected = instant(cut_off)
        query = urlencode({"corte": expected})
        policies_payload = self._read(
            "polizas", self._policies_url, f"/v1/reportes/polizas?{query}", correlation_id
        )
        premiums_payload = self._read(
            "primas", self._premiums_url, f"/v1/reportes/primas?{query}", correlation_id
        )
        claims_payload = self._read(
            "siniestros",
            self._claims_url,
            "/internal/v1/reportes/consulta",
            correlation_id,
            body={"corte": expected},
        )
        policies_raw = _collection(policies_payload, "polizas", "polizas")
        premiums_raw = _collection(premiums_payload, "primas", "primas")
        claims_raw = _collection(claims_payload, "siniestros", "siniestros")
        payments_raw = _collection(claims_payload, "siniestros", "pagos")
        sources = (
            _trace(policies_payload, "polizas", expected, policies_raw, len(policies_raw)),
            _trace(premiums_payload, "primas", expected, premiums_raw, len(premiums_raw)),
            _trace(
                claims_payload,
                "siniestros",
                expected,
                {"siniestros": claims_raw, "pagos": payments_raw},
                len(claims_raw) + len(payments_raw),
            ),
        )
        policies = _records("polizas", PolicyRecord, policies_raw)
        premiums = _records("primas", PremiumRecord, premiums_raw)
        claims = _records("siniestros", ClaimRecord, claims_raw)
        payments = _records("siniestros", PaymentRecord, payments_raw)
        _unique("polizas", [policy.id for policy in policies])
        _unique("polizas", [policy.pagoId for policy in policies])
        _unique("primas", [premium.pagoId for premium in premiums])
        _unique("siniestros", [claim.id for claim in claims])
        _unique("siniestros", [payment.id for payment in payments])
        _not_after("polizas", cut_off, (policy.emitidaEn for policy in policies))
        _not_after("primas", cut_off, (premium.confirmadaEn for premium in premiums))
        _not_after("siniestros", cut_off, (claim.reportadoEn for claim in claims))
        _not_after("siniestros", cut_off, (payment.creadoEn for payment in payments))
        return ReportSnapshot(cut_off, policies, premiums, claims, payments, sources)

    def _read(
        self,
        source: str,
        base_url: str,
        path: str,
        correlation_id: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not base_url or not self._token:
            raise ReportSourceUnavailableError(source)
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
            "X-Correlation-ID": correlation_id,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise ReportSourceUnavailableError(source)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except ReportSourceUnavailableError:
            raise
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise ReportSourceUnavailableError(source) from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ReportSourceUnavailableError(source)
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise ReportSourceUnavailableError(source) from error
        if not isinstance(payload, dict):
            raise ReportSourceUnavailableError(source)
        return payload


def _collection(payload: dict[str, Any], source: str, key: str) -> list[Any]:
    items = payload.get(key)
    if not isinstance(items, list):
        raise ReportSourceUnavailableError(source)
    return items


def _trace(
    payload: dict[str, Any], source: str, expected: str, content: object, records: int
) -> SourceTrace:
    reported = payload.get("registros")
    if (
        payload.get("fuente") != source
        or payload.get("corte") != expected
        or type(reported) is not int
        or reported != records
        or payload.get("huella") != fingerprint(content)
    ):
        raise ReportSourceUnavailableError(source)
    return SourceTrace(source, records, str(payload["huella"]))


def _records(source: str, model: type[Record], items: Sequence[Any]) -> tuple[Record, ...]:
    try:
        return tuple(model.model_validate(item) for item in items)
    except ValidationError as error:
        raise ReportSourceUnavailableError(source) from error


def _unique(source: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ReportSourceUnavailableError(source)


def _not_after(source: str, cut_off: datetime, values: Any) -> None:
    if any(value > cut_off for value in values):
        raise ReportSourceUnavailableError(source)
