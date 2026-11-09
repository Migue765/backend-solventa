import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from asistencias.domain import Config, Problem


class ResourceAuthorizer:
    def authorized_policy(self, client_id: UUID, policy_id: UUID, correlation_id: UUID) -> bool:
        raise NotImplementedError

    def authorized_claim(self, client_id: UUID, claim_id: UUID, correlation_id: UUID) -> bool:
        raise NotImplementedError


class HttpResourceAuthorizer(ResourceAuthorizer):
    def __init__(self, config: Config) -> None:
        self.config = config

    def authorized_policy(self, client_id: UUID, policy_id: UUID, correlation_id: UUID) -> bool:
        query = urlencode({"clienteId": str(client_id)})
        payload = self._get(f"{self.config.policies_url}/v1/polizas?{query}", correlation_id)
        policies = payload.get("polizas")
        if not isinstance(policies, list):
            raise Problem(503, "servicio_no_disponible")
        return any(
            isinstance(policy, dict)
            and policy.get("id") == str(policy_id)
            and policy.get("estado") == "vigente"
            for policy in policies
        )

    def authorized_claim(self, client_id: UUID, claim_id: UUID, correlation_id: UUID) -> bool:
        try:
            payload = self._get(
                f"{self.config.claims_url}/v1/siniestros/{claim_id}/estado",
                correlation_id,
                client_id,
            )
        except ResourceNotFound:
            return False
        return payload.get("id") == str(claim_id)

    def _get(
        self, url: str, correlation_id: UUID, client_id: UUID | None = None
    ) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {self.config.internal_token}",
            "X-Correlation-ID": str(correlation_id),
        }
        if client_id is not None:
            headers["X-Cliente-ID"] = str(client_id)
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.config.dependency_timeout_seconds) as response:
                body = response.read(1024 * 1024 + 1)
                if response.status != 200 or len(body) > 1024 * 1024:
                    raise Problem(503, "servicio_no_disponible")
        except HTTPError as error:
            if error.code == 404:
                raise ResourceNotFound from error
            raise Problem(503, "servicio_no_disponible") from error
        except (OSError, HTTPException, ValueError, TypeError) as error:
            raise Problem(503, "servicio_no_disponible") from error
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise Problem(503, "servicio_no_disponible") from error
        if not isinstance(payload, dict):
            raise Problem(503, "servicio_no_disponible")
        return payload


class ResourceNotFound(Exception):
    pass
