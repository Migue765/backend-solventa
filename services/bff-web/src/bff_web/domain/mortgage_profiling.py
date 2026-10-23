from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class MortgageClient:
    client_id: UUID
    document_type: str
    identification: str
    email: str
    phone: str
    birth_date: date


@dataclass(frozen=True)
class MortgageCredit:
    reference: str
    amount: Decimal
    term_months: int


@dataclass(frozen=True)
class MortgageProfilingCommand:
    operation_id: UUID
    partner_id: str
    advisor_id: UUID
    client: MortgageClient
    credit: MortgageCredit
    consent_id: UUID
    consent_version: int


@dataclass(frozen=True)
class MortgageProfilingResult:
    operation_id: UUID
    partner_id: str
    advisor_id: UUID
    client_id: UUID
    status: str
    profiling_reference: str
    request_id: str
    quote_id: UUID
    received_at: datetime


class MortgageProfilingError(Exception):
    def __init__(self, code: str, status: int, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after
