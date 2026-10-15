import re

_PATTERNS = {
    "cc": re.compile(r"[0-9]{6,10}"),
    "ce": re.compile(r"[0-9]{6,12}"),
    "passport": re.compile(r"[A-Z0-9]{5,16}"),
    "ppt": re.compile(r"[0-9]{7,10}"),
}


def validate_identification(document_type: str, identification: str) -> str:
    pattern = _PATTERNS.get(document_type)
    if pattern is None or pattern.fullmatch(identification) is None:
        raise ValueError("The identification does not match the document type.")
    return identification
