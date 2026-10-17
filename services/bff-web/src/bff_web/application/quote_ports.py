from typing import Protocol
from uuid import UUID

from bff_web.domain.quotes import (
    InsuranceProduct,
    OfferSelection,
    OffersResult,
    QuoteRequestDraft,
    QuoteRequestRecord,
    Subscription,
    TravelDestination,
)


class QuoteGateway(Protocol):
    def list_products(self) -> tuple[InsuranceProduct, ...]: ...

    def list_travel_destinations(self) -> tuple[TravelDestination, ...]: ...

    def create_request(
        self,
        user_id: UUID,
        draft: QuoteRequestDraft,
        idempotency_key: UUID,
    ) -> QuoteRequestRecord: ...

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord: ...

    def list_offers(self, user_id: UUID, request_id: str) -> OffersResult: ...

    def get_selection(self, user_id: UUID, request_id: str) -> OfferSelection: ...

    def select_offer(
        self,
        user_id: UUID,
        request_id: str,
        offer_id: UUID,
        version: int,
        idempotency_key: UUID,
    ) -> OfferSelection: ...

    def start_subscription(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> Subscription: ...

    def get_subscription(self, user_id: UUID, request_id: str) -> Subscription: ...
