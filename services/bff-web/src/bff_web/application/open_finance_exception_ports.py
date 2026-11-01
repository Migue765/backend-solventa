from typing import Protocol
from uuid import UUID


class OpenFinanceExceptionGateway(Protocol):
    def list_pending(self, partner_id: str, correlation_id: str) -> dict[str, object]: ...

    def get(
        self, exception_id: UUID, partner_id: str, correlation_id: str
    ) -> dict[str, object]: ...

    def resolve(
        self,
        exception_id: UUID,
        partner_id: str,
        actor_id: UUID,
        decision: str,
        reason: str,
        correlation_id: str,
    ) -> dict[str, object]: ...
