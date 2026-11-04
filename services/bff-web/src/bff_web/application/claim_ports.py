from typing import Protocol
from uuid import UUID

from bff_web.domain.claims import ClaimStatus


class ClaimGateway(Protocol):
    def get_status(self, claim_id: UUID, client_id: UUID, correlation_id: str) -> ClaimStatus: ...
