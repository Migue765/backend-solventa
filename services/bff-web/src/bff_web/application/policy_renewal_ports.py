from typing import Protocol
from uuid import UUID

from bff_web.domain.policy_renewal import Renewal, RenewalEligibility, RenewalRequest


class PolicyRenewalGateway(Protocol):
    def get_eligibility(
        self, policy_id: UUID, client_id: UUID, correlation_id: str
    ) -> RenewalEligibility: ...

    def renew(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        request: RenewalRequest,
        correlation_id: str,
    ) -> tuple[Renewal, int]: ...
