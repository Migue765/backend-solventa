from typing import Protocol
from uuid import UUID

from bff_web.domain.claims import ClaimDetail, ClaimDraft, ClaimList, ClaimPolicies


class ClaimGateway(Protocol):
    def create(
        self, user_id: UUID, draft: ClaimDraft, key: UUID, correlation_id: str
    ) -> tuple[ClaimDetail, bool]: ...

    def policies(self, user_id: UUID, correlation_id: str) -> ClaimPolicies: ...

    def list(self, user_id: UUID, correlation_id: str) -> ClaimList: ...

    def get(self, user_id: UUID, claim_id: UUID, correlation_id: str) -> ClaimDetail: ...
