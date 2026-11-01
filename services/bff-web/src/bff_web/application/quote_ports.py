from typing import Protocol
from uuid import UUID

from bff_web.domain.dashboard import Dashboard
from bff_web.domain.quotes import (
    InsuranceProduct,
    InsuranceProductDetail,
    OfferSelection,
    OffersResult,
    ProvisionalOfferSelection,
    ProvisionalSelectionState,
    QuoteRequestDraft,
    QuoteRequestRecord,
    SimulatedPaymentResult,
    Subscription,
    TravelDestination,
)


class QuoteGateway(Protocol):
    def get_dashboard(self, user_id: UUID) -> Dashboard: ...

    def list_products(self) -> tuple[InsuranceProduct, ...]: ...

    def get_product(self, product_id: str) -> InsuranceProductDetail: ...

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
        selection_revision: int | None = None,
    ) -> OfferSelection: ...

    def get_provisional_selection(
        self, user_id: UUID, request_id: str
    ) -> ProvisionalSelectionState: ...

    def select_provisional_offer(
        self,
        user_id: UUID,
        request_id: str,
        offer_id: UUID,
        version: int,
        expected_revision: int,
        idempotency_key: UUID,
    ) -> ProvisionalOfferSelection: ...

    def start_subscription(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> Subscription: ...

    def get_subscription(self, user_id: UUID, request_id: str) -> Subscription: ...

    def confirm_simulated_payment(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> SimulatedPaymentResult: ...

    def get_simulated_payment(self, user_id: UUID, request_id: str) -> SimulatedPaymentResult: ...
