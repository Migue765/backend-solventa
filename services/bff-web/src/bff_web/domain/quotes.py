from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class InsuranceProduct:
    product_id: str
    name: str
    description: str
    available: bool


@dataclass(frozen=True)
class TravelDestination:
    code: str
    name: str


@dataclass(frozen=True)
class QuoteRequestDraft:
    product_id: str
    customer_type: str
    document_type: str
    identification: str
    product_data: dict[str, str]
    email: str
    phone: str


@dataclass(frozen=True)
class QuoteRequestRecord:
    request_id: str
    draft: QuoteRequestDraft
    status: str
    created_at: datetime


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Offer:
    offer_id: UUID
    version: int
    name: str
    description: str
    monthly_premium: Money
    main_coverage: str
    deductible: str
    duration: str
    civil_liability: str
    coverages: tuple[str, ...]
    conditions: tuple[str, ...]
    additional_benefits: tuple[str, ...]
    recommended: bool
    status: str
    valid_until: datetime
    billing_period: str = "mensual"


@dataclass(frozen=True)
class OffersResult:
    request_id: str
    calculated_at: datetime
    valid_until: datetime
    applied_rate: str
    offers: tuple[Offer, ...]


@dataclass(frozen=True)
class OfferSelection:
    request_id: str
    offer_id: UUID
    version: int
    offer_name: str
    monthly_premium: Money
    selected_at: datetime
    billing_period: str = "mensual"


@dataclass(frozen=True)
class SubscriptionDecision:
    result: str
    rule_version: str
    decision_hash: str
    decided_at: datetime


@dataclass(frozen=True)
class IssuanceSaga:
    status: str
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class Subscription:
    subscription_id: UUID
    request_id: str
    offer_id: UUID
    offer_version: int
    status: str
    rule_version: str
    created_at: datetime
    updated_at: datetime
    decision: SubscriptionDecision
    saga: IssuanceSaga
