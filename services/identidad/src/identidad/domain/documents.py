import re
from typing import Literal, cast

DocumentType = Literal["cc", "ce", "passport", "ppt"]

_PATTERNS = {
    "cc": re.compile(r"[0-9]{6,10}"),
    "ce": re.compile(r"[0-9]{6,12}"),
    "passport": re.compile(r"[A-Z0-9]{5,16}"),
    "ppt": re.compile(r"[0-9]{7,10}"),
}


def normalize_document(document_type: str, number: str) -> tuple[DocumentType, str]:
    normalized_type = document_type.strip().casefold()
    normalized_number = number.strip().upper()
    pattern = _PATTERNS.get(normalized_type)
    if pattern is None or pattern.fullmatch(normalized_number) is None:
        raise ValueError("Documento invalido.")
    return cast(DocumentType, normalized_type), normalized_number
