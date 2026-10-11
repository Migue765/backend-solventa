from typing import Protocol
from uuid import UUID

from bff_web.domain.quotes import InsuranceProduct, QuoteRequestDraft, QuoteRequestRecord


class QuoteGateway(Protocol):
    def list_products(self) -> tuple[InsuranceProduct, ...]: ...

    def create_request(
        self,
        user_id: UUID,
        draft: QuoteRequestDraft,
        idempotency_key: UUID,
    ) -> QuoteRequestRecord: ...

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord: ...
