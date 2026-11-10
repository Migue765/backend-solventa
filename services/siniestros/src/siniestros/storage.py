"""Durable store for confirmed evidence.

Chunks are always staged in SINIESTROS_STORAGE_ROOT. Without SINIESTROS_EVIDENCIAS_BUCKET that
directory is also the durable store and must be a persistent volume. With a bucket, confirmed
evidence is published to S3 before the confirmation commits and the directory is only scratch.
"""

import base64
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any, BinaryIO, Protocol, TypeVar, cast

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import BotoCoreError, ClientError

from siniestros.domain import Config

logger = logging.getLogger(__name__)

PREFIX = "evidencias/"
COPY_BUFFER = 1024 * 1024
T = TypeVar("T")


class S3Client(Protocol):
    def put_object(
        self, *, Bucket: str, Key: str, Body: IO[bytes], ChecksumSHA256: str
    ) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


class EvidenceStore(Protocol):
    def publish(self, name: str, staged: Path, sha256: str) -> None:
        """Make validated evidence durable; called before the confirmation commits."""

    def release(self, staged: Path) -> None:
        """Drop the staged copy once the confirmation has committed."""

    def open(self, name: str, staged: Path) -> BinaryIO:
        """Open the confirmed bytes, positioned at the start."""

    def discard(self, name: str, staged: Path) -> None:
        """Erase every copy of a cancelled or rejected upload."""


class LocalEvidence:
    def publish(self, name: str, staged: Path, sha256: str) -> None:
        return None

    def release(self, staged: Path) -> None:
        return None

    def open(self, name: str, staged: Path) -> BinaryIO:
        return staged.open("rb")

    def discard(self, name: str, staged: Path) -> None:
        staged.unlink(missing_ok=True)


class S3Evidence:
    def __init__(self, bucket: str, client: S3Client, scratch: Path) -> None:
        self.bucket = bucket
        self.client = client
        self.scratch = scratch

    def publish(self, name: str, staged: Path, sha256: str) -> None:
        # S3 rejects the object unless the received bytes match the validated digest.
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        with staged.open("rb") as body:
            s3_call(
                "put_object",
                lambda: self.client.put_object(
                    Bucket=self.bucket, Key=PREFIX + name, Body=body, ChecksumSHA256=checksum
                ),
            )

    def release(self, staged: Path) -> None:
        try:
            staged.unlink(missing_ok=True)
        except OSError as error:
            logger.warning(
                "No se pudo liberar la copia local de una evidencia confirmada; clase=%s",
                type(error).__name__,
            )

    def open(self, name: str, staged: Path) -> BinaryIO:
        copy = cast(BinaryIO, tempfile.TemporaryFile(dir=self.scratch))
        try:
            response = s3_call(
                "get_object", lambda: self.client.get_object(Bucket=self.bucket, Key=PREFIX + name)
            )
            body = response["Body"]
            try:
                s3_call("get_object", lambda: shutil.copyfileobj(body, copy, COPY_BUFFER))
            finally:
                body.close()
            copy.seek(0)
        except BaseException:
            copy.close()
            raise
        return copy

    def discard(self, name: str, staged: Path) -> None:
        # Remote first: if S3 fails, the local bytes remain and the operation can be retried.
        s3_call(
            "delete_object",
            lambda: self.client.delete_object(Bucket=self.bucket, Key=PREFIX + name),
        )
        staged.unlink(missing_ok=True)


def s3_call(operation: str, action: Callable[[], T]) -> T:
    try:
        return action()
    except (BotoCoreError, ClientError) as error:
        code = (
            error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else None
        )
        logger.error(
            "Fallo de S3 en evidencias; operacion=%s clase=%s codigo=%s",
            operation,
            type(error).__name__,
            code,
        )
        # Storage failures share the OSError path: the API answers 503 and the client retries.
        raise OSError(f"S3 {operation} failed.") from error


def s3_client() -> S3Client:
    # Confirmation publishes while holding the global write lock: fail with a retryable 503
    # well before gunicorn's 30 s worker timeout instead of blocking every writer.
    return cast(
        S3Client,
        boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION") or None,
            config=BotocoreConfig(
                connect_timeout=3,
                read_timeout=10,
                retries={"mode": "standard", "max_attempts": 2},
            ),
        ),
    )


def evidence_store(config: Config) -> EvidenceStore:
    if config.evidence_bucket is None:
        return LocalEvidence()
    return S3Evidence(config.evidence_bucket, s3_client(), config.storage_root)
