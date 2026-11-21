import hashlib
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.policy_document import (
    DocumentAcceptance,
    DocumentConfirmation,
    DocumentConflictError,
    DocumentDownload,
    PolicyDocument,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.infrastructure.http_policy_renewal_gateway import HttpPolicyRenewalGateway, _payload


class HttpPolicyDocumentGateway(HttpPolicyRenewalGateway):
    def get_document(
        self,
        policy_id: UUID,
        client_id: UUID,
        correlation_id: str,
    ) -> PolicyDocument:
        payload, status = self._call(
            "GET", f"/v1/clientes/me/polizas/{policy_id}/documento", client_id, correlation_id
        )
        document = _validate(PolicyDocument, payload)
        if (
            status != 200
            or document.policy_id != policy_id
            or document.content.client_id != client_id
            or any(
                item.actor_id != client_id or item.policy_id != policy_id
                for item in document.acceptance_history
            )
        ):
            raise RenewalServiceUnavailableError()
        return document

    def accept(
        self,
        policy_id: UUID,
        client_id: UUID,
        key: UUID,
        confirmation: DocumentConfirmation,
        correlation_id: str,
    ) -> tuple[DocumentAcceptance, int]:
        payload, status = self._call(
            "POST",
            f"/v1/clientes/me/polizas/{policy_id}/aceptaciones",
            client_id,
            correlation_id,
            body=confirmation.model_dump(mode="json", by_alias=True),
            idempotency_key=key,
        )
        acceptance = _validate(DocumentAcceptance, payload)
        if (
            status not in {200, 201}
            or acceptance.policy_id != policy_id
            or acceptance.actor_id != client_id
            or acceptance.document_id != confirmation.document_id
            or acceptance.content_hash != confirmation.content_hash
            or acceptance.policy_version != confirmation.policy_version
        ):
            raise RenewalServiceUnavailableError()
        return acceptance, status

    def download(
        self,
        policy_id: UUID,
        client_id: UUID,
        document_id: UUID,
        correlation_id: str,
    ) -> DocumentDownload:
        if not self._base_url or not self._token:
            raise RenewalServiceUnavailableError()
        request = Request(
            f"{self._base_url}/v1/clientes/me/polizas/{policy_id}"
            f"/documentos/{document_id}/descarga",
            headers={
                "Authorization": "Bearer " + self._token,
                "X-Cliente-ID": str(client_id),
                "X-Correlation-ID": correlation_id,
                "Accept": "text/html",
            },
        )
        try:
            with urlopen(request, timeout=self._query_timeout) as response:  # noqa: S310
                content = response.read(1_048_577)
                content_hash = response.headers.get("X-Content-SHA256", "")
                if (
                    response.status != 200
                    or response.headers.get_content_type() != "text/html"
                    or response.headers.get_content_charset() != "utf-8"
                    or len(content) > 1_048_576
                    or re.fullmatch(r"[a-f0-9]{64}", content_hash) is None
                    or hashlib.sha256(content).hexdigest() != content_hash
                ):
                    raise RenewalServiceUnavailableError()
                content.decode("utf-8")
                return DocumentDownload(content, content_hash)
        except HTTPError as error:
            payload = _payload(error)
            if payload is not None:
                self._translate_error(error, payload)
            raise RenewalServiceUnavailableError() from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise RenewalServiceUnavailableError() from error

    def _translate_error(self, error: HTTPError, payload: dict[str, Any]) -> None:
        code = payload.get("error")
        if error.code == 404 and code == "poliza_no_encontrada":
            raise PolicyNotFoundError() from error
        if error.code == 409 and code in {
            "documento_desactualizado",
            "idempotencia_conflictiva",
        }:
            raise DocumentConflictError(str(code)) from error


def _validate[T: (PolicyDocument, DocumentAcceptance)](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise RenewalServiceUnavailableError() from error
