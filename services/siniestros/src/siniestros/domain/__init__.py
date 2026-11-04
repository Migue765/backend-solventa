import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)
from sqlalchemy.engine import make_url


class Problem(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def iso_timestamp(value: object) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("An ISO timestamp with an explicit time zone is required.")
    return value


Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(iso_timestamp),
    AfterValidator(lambda value: value.astimezone(UTC)),
]


class ClaimInput(Input):
    polizaId: UUID
    fechaEvento: Timestamp
    tipoEvento: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    descripcion: Annotated[str, StringConstraints(min_length=10, max_length=5000)]
    lugar: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class Location(Input):
    consentimiento: StrictBool
    latitud: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitud: float = Field(ge=-180, le=180, allow_inf_nan=False)
    fecha: Timestamp


class UploadInput(Input):
    mimeType: Literal["image/jpeg", "image/png", "application/pdf", "video/mp4"]
    tamanoBytes: StrictInt = Field(gt=0)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    ubicacion: Location | None = None
    fechaCaptura: Timestamp | None = None


@dataclass(frozen=True)
class Config:
    database_url: str
    storage_root: Path
    internal_token: str
    policies_url: str
    max_count: int = 10
    max_chunk: int = 1024 * 1024
    photo_bytes: int = 10 * 1024 * 1024
    document_bytes: int = 20 * 1024 * 1024
    video_bytes: int = 100 * 1024 * 1024
    video_seconds: int = 120
    allowed_mimes: tuple[str, ...] = ("image/jpeg", "image/png", "application/pdf", "video/mp4")

    def __post_init__(self) -> None:
        if not self.internal_token.strip() or not self.database_url.strip():
            raise ValueError("Database and internal credentials are required.")
        database = make_url(self.database_url)
        if database.get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("Siniestros supports SQLite and PostgreSQL only.")
        if database.get_backend_name() == "sqlite" and (
            database.database in {None, "", ":memory:"} or database.query.get("mode") == "memory"
        ):
            raise ValueError("Evidence operations require a durable database.")
        if not self.storage_root.is_absolute():
            raise ValueError("SINIESTROS_STORAGE_ROOT must be an absolute private directory.")
        if not self.policies_url.startswith(("http://", "https://")):
            raise ValueError("POLIZAS_SERVICE_URL must be an HTTP service URL.")
        if any(
            value <= 0
            for value in (
                self.max_count,
                self.max_chunk,
                self.photo_bytes,
                self.document_bytes,
                self.video_bytes,
                self.video_seconds,
            )
        ):
            raise ValueError("Evidence limits must be positive.")
        if self.max_chunk > 1024 * 1024:
            raise ValueError("Evidence chunks cannot exceed the BFF limit of 1 MiB.")
        if not self.allowed_mimes or len(set(self.allowed_mimes)) != len(self.allowed_mimes):
            raise ValueError("Evidence MIME types must be a nonempty distinct allowlist.")
        if set(self.allowed_mimes) - {"image/jpeg", "image/png", "application/pdf", "video/mp4"}:
            raise ValueError("An evidence MIME type has no installed validator.")

    @classmethod
    def from_environment(cls) -> "Config":
        return cls(
            database_url=os.environ["SINIESTROS_DATABASE_URL"],
            storage_root=Path(os.environ["SINIESTROS_STORAGE_ROOT"]),
            internal_token=os.environ["INTERNAL_API_TOKEN"],
            policies_url=os.environ["POLIZAS_SERVICE_URL"].rstrip("/"),
            max_count=int(os.getenv("EVIDENCIA_MAX_CANTIDAD", "10")),
            max_chunk=int(os.getenv("EVIDENCIA_MAX_CHUNK_BYTES", str(1024 * 1024))),
            photo_bytes=int(os.getenv("EVIDENCIA_MAX_FOTO_BYTES", str(10 * 1024 * 1024))),
            document_bytes=int(os.getenv("EVIDENCIA_MAX_DOCUMENTO_BYTES", str(20 * 1024 * 1024))),
            video_bytes=int(os.getenv("EVIDENCIA_MAX_VIDEO_BYTES", str(100 * 1024 * 1024))),
            video_seconds=int(os.getenv("EVIDENCIA_MAX_VIDEO_SECONDS", "120")),
            allowed_mimes=tuple(
                item.strip()
                for item in os.getenv(
                    "EVIDENCIA_MIME_PERMITIDOS", "image/jpeg,image/png,application/pdf,video/mp4"
                ).split(",")
            ),
        )

    @property
    def formats(self) -> dict[str, int]:
        supported = {
            "image/jpeg": self.photo_bytes,
            "image/png": self.photo_bytes,
            "application/pdf": self.document_bytes,
            "video/mp4": self.video_bytes,
        }
        return {mime: supported[mime] for mime in self.allowed_mimes}

    def public(self) -> dict[str, object]:
        return {
            "maxCantidad": self.max_count,
            "maxChunkBytes": self.max_chunk,
            "maxVideoSeconds": self.video_seconds,
            "formatos": [
                {"mimeType": mime, "maxBytes": maximum} for mime, maximum in self.formats.items()
            ],
        }


def now() -> str:
    return datetime.now(UTC).isoformat()
