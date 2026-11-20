from typing import Any, Protocol


class VerificationGateway(Protocol):
    def verification_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        bearer: str,
        correlation_id: str,
    ) -> dict[str, Any]: ...
