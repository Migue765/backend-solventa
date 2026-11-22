from datetime import datetime
from typing import Protocol

from bff_web.domain.operational_reports import ReportSnapshot


class ReportSources(Protocol):
    def fetch(self, cut_off: datetime, correlation_id: str) -> ReportSnapshot: ...
