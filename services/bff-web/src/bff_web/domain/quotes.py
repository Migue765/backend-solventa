from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InsuranceProduct:
    product_id: str
    name: str
    description: str
    available: bool


@dataclass(frozen=True)
class QuoteRequestDraft:
    product_id: str
    customer_type: str
    identification: str
    vehicle_plate: str
    email: str
    phone: str


@dataclass(frozen=True)
class QuoteRequestRecord:
    request_id: str
    draft: QuoteRequestDraft
    status: str
    created_at: datetime
