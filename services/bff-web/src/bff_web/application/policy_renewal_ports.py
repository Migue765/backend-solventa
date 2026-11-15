from typing import Protocol
from uuid import UUID

from bff_web.domain.policy_modification import (
    ModificationConfirmation,
    ModificationImpact,
    ModificationRequest,
    ModificationResult,
    ModificationState,
)
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


class PolicyModificationGateway(Protocol):
    def get_state(
        self, policy_id: UUID, client_id: UUID, correlation_id: str
    ) -> ModificationState: ...

    def simulate(
        self,
        policy_id: UUID,
        client_id: UUID,
        request: ModificationRequest,
        correlation_id: str,
    ) -> ModificationImpact: ...

    def confirm(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        confirmation: ModificationConfirmation,
        correlation_id: str,
    ) -> tuple[ModificationResult, int]: ...
