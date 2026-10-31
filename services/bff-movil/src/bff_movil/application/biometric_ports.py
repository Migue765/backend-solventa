from typing import Any, Protocol

from bff_movil.domain.auth import TokenSet


class BiometricGateway(Protocol):
    def biometric_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        bearer: str | None,
        correlation_id: str,
    ) -> dict[str, Any]: ...

    def biometric_login(self, proof: dict[str, object], correlation_id: str) -> TokenSet: ...
