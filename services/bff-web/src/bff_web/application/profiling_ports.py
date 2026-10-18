from typing import Protocol
from uuid import UUID


class ProfilingGateway(Protocol):
    def get_consent(self, user_id: UUID, correlation_id: str) -> dict[str, object]: ...

    def set_consent(
        self, user_id: UUID, authorized: bool, version: int, correlation_id: str
    ) -> dict[str, object]: ...

    def get_profile(
        self, user_id: UUID, request_id: str, correlation_id: str
    ) -> dict[str, object]: ...
