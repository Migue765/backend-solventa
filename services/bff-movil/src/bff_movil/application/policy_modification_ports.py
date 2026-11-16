from typing import Protocol
from uuid import UUID

from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationImpact,
    ModificationRequest,
    ModificationResult,
    ModificationState,
)


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
