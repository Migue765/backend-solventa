from typing import Protocol
from uuid import UUID

from bff_web.domain.policy_document import (
    DocumentAcceptance,
    DocumentConfirmation,
    DocumentDownload,
    PdfDocumentDownload,
    PolicyDocument,
)


class PolicyDocumentGateway(Protocol):
    def get_document(
        self,
        policy_id: UUID,
        client_id: UUID,
        correlation_id: str,
    ) -> PolicyDocument: ...

    def download(
        self,
        policy_id: UUID,
        client_id: UUID,
        document_id: UUID,
        correlation_id: str,
    ) -> DocumentDownload: ...

    def accept(
        self,
        policy_id: UUID,
        client_id: UUID,
        key: UUID,
        confirmation: DocumentConfirmation,
        correlation_id: str,
    ) -> tuple[DocumentAcceptance, int]: ...

    def download_pdf(
        self,
        policy_id: UUID,
        client_id: UUID,
        document_id: UUID,
        correlation_id: str,
    ) -> PdfDocumentDownload: ...
