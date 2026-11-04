import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any
from uuid import uuid4

from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from siniestros import media
from siniestros.domain import ClaimInput, Config, Problem, UploadInput, now
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Audit, Claim, Upload
from siniestros.policies import policies


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection: Any, _: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=FULL")

    return engine


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def claim_record(claim: Claim) -> dict[str, Any]:
    return {**claim.payload, "id": claim.id, "estado": "registrado", "registradoEn": claim.created}


def upload_record(upload: Upload) -> dict[str, Any]:
    return {
        "id": upload.id,
        "siniestroId": upload.claim_id,
        "estado": upload.status,
        "offset": upload.offset,
        "tamanoBytes": upload.size,
        "mimeType": upload.mime,
        "sha256": upload.digest,
        "evidenciaId": upload.id if upload.status == "confirmada" else None,
    }


def evidence_record(upload: Upload) -> dict[str, Any]:
    return {
        "id": upload.id,
        "siniestroId": upload.claim_id,
        "mimeType": upload.mime,
        "tamanoBytes": upload.size,
        "sha256": upload.digest,
        "autorId": upload.owner,
        "creadaEn": upload.confirmed,
        "fechaCaptura": upload.captured,
        "ubicacion": upload.location,
    }


class Repository:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.engine = make_engine(config.database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        config.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if config.storage_root.is_symlink():
            raise ValueError("Private storage cannot be a symbolic link.")

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self.sessions() as session, session.begin():
            # SQLite has no SELECT FOR UPDATE; serialize writers across processes.
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            elif self.engine.dialect.name == "postgresql":
                session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(307313)")
            else:
                raise ValueError("Siniestros supports SQLite and PostgreSQL transactions only.")
            yield session

    @staticmethod
    def audit(session: Session, actor: str | None, action: str, result: str, cid: str) -> None:
        session.add(
            Audit(
                id=str(uuid4()),
                actor=actor,
                action=action,
                result=result,
                correlation=cid,
                created=now(),
            )
        )

    def failure(self, actor: str | None, action: str, code: str, cid: str) -> None:
        with self.write() as session:
            self.audit(session, actor, action, code, cid)

    @staticmethod
    def claim(session: Session, owner: str, claim_id: str, lock: bool = False) -> Claim:
        query = select(Claim).where(Claim.id == claim_id, Claim.owner == owner)
        claim = session.scalar(query.with_for_update() if lock else query)
        if claim is None:
            raise Problem(404, "siniestro_no_encontrado")
        return claim

    def list_claims(self, owner: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [
                claim_record(claim)
                for claim in session.scalars(
                    select(Claim)
                    .where(Claim.owner == owner)
                    .order_by(Claim.created.desc(), Claim.id)
                )
            ]

    def get_claim(self, owner: str, claim_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            return claim_record(self.claim(session, owner, claim_id))

    def create_claim(
        self, owner: str, operation: str, data: ClaimInput, cid: str
    ) -> tuple[dict[str, Any], int]:
        payload = data.model_dump(mode="json")
        digest = fingerprint(payload)
        with self.write() as session:
            existing = session.scalar(
                select(Claim).where(Claim.owner == owner, Claim.operation == operation)
            )
            if existing is not None:
                if existing.fingerprint != digest:
                    raise Problem(409, "idempotencia_conflictiva")
                return claim_record(existing), 200
            selected = next(
                (p for p in policies(self.config, owner, cid) if p["id"] == str(data.polizaId)),
                None,
            )
            if selected is None:
                raise Problem(404, "poliza_no_encontrada")
            day = data.fechaEvento.date().isoformat()
            if (
                data.fechaEvento > datetime.now(UTC)
                or not selected["fechaInicio"] <= day <= selected["fechaFin"]
            ):
                raise Problem(422, "fecha_evento_invalida")
            created = now()
            claim = Claim(
                id=str(uuid4()),
                owner=owner,
                operation=operation,
                fingerprint=digest,
                payload=payload,
                created=created,
                estado="reportado",
                actualizado_en=datetime.fromisoformat(created),
            )
            session.add(claim)
            self.audit(session, owner, "registrar_siniestro", "registrado", cid)
            session.flush()
            session.add(
                CambioEstadoModel(
                    siniestro_id=claim.id,
                    secuencia=1,
                    estado="reportado",
                    ocurrido_en=datetime.fromisoformat(created),
                    visible_cliente=True,
                )
            )
            return claim_record(claim), 201

    def start(
        self, owner: str, claim_id: str, operation: str, data: UploadInput, cid: str
    ) -> tuple[dict[str, Any], int]:
        payload = data.model_dump(mode="json")
        digest = fingerprint({"siniestroId": claim_id, **payload})
        with self.write() as session:
            self.claim(session, owner, claim_id, lock=True)
            existing = session.scalar(
                select(Upload).where(Upload.owner == owner, Upload.operation == operation)
            )
            if existing is not None:
                if existing.fingerprint != digest:
                    raise Problem(409, "idempotencia_conflictiva")
                return upload_record(existing), 200
            if data.mimeType not in self.config.formats:
                raise Problem(415, "formato_no_permitido")
            if data.tamanoBytes > self.config.formats[data.mimeType]:
                raise Problem(413, "limite_tamano")
            if data.ubicacion is not None and not data.ubicacion.consentimiento:
                raise Problem(422, "ubicacion_no_autorizada")
            for timestamp in (data.fechaCaptura, data.ubicacion.fecha if data.ubicacion else None):
                if timestamp is not None and timestamp > datetime.now(UTC) + timedelta(minutes=5):
                    raise Problem(400, "solicitud_invalida")
            count = session.scalar(
                select(func.count())
                .select_from(Upload)
                .where(Upload.claim_id == claim_id, Upload.status != "cancelada")
            )
            if count is not None and count >= self.config.max_count:
                raise Problem(409, "limite_cantidad")
            upload = Upload(
                id=str(uuid4()),
                claim_id=claim_id,
                owner=owner,
                operation=operation,
                fingerprint=digest,
                mime=data.mimeType,
                size=data.tamanoBytes,
                digest=data.sha256,
                location=payload["ubicacion"],
                captured=payload["fechaCaptura"],
                created=now(),
                offset=0,
                status="pendiente",
            )
            session.add(upload)
            self.audit(session, owner, "iniciar_carga", "pendiente", cid)
            session.flush()
            return upload_record(upload), 201

    def upload(
        self, session: Session, owner: str, claim_id: str, upload_id: str, lock: bool = False
    ) -> Upload:
        self.claim(session, owner, claim_id, lock=lock)
        query = select(Upload).where(
            Upload.id == upload_id, Upload.claim_id == claim_id, Upload.owner == owner
        )
        upload = session.scalar(query.with_for_update() if lock else query)
        if upload is None:
            raise Problem(404, "carga_no_encontrada")
        return upload

    def get_upload(self, owner: str, claim_id: str, upload_id: str) -> dict[str, Any]:
        with self.write() as session:
            upload = self.upload(session, owner, claim_id, upload_id, lock=True)
            path = self.path(upload)
            if (
                upload.status == "pendiente"
                and (path.stat().st_size if path.exists() else 0) < upload.offset
            ):
                upload.offset = 0
                self.audit(session, owner, "recuperar_carga", "reinicio_requerido", str(uuid4()))
            return upload_record(upload)

    def path(self, upload: Upload) -> Path:
        path = self.config.storage_root / upload.id
        if path.is_symlink():
            raise OSError("Unexpected symbolic link in private storage.")
        return path

    def check_policy(self, upload: Upload) -> None:
        if upload.mime not in self.config.formats:
            raise Problem(415, "formato_no_permitido")
        if upload.size > self.config.formats[upload.mime]:
            raise Problem(413, "limite_tamano")

    def append(
        self,
        owner: str,
        claim_id: str,
        upload_id: str,
        offset: int,
        length: int,
        stream: IO[bytes],
        cid: str,
    ) -> dict[str, Any]:
        with self.write() as session:
            upload = self.upload(session, owner, claim_id, upload_id, lock=True)
            if upload.status != "pendiente":
                raise Problem(409, f"carga_{upload.status}")
            self.check_policy(upload)
            if length <= 0 or length > self.config.max_chunk or offset + length > upload.size:
                raise Problem(413, "limite_tamano")
            if offset != upload.offset:
                raise Problem(409, "offset_conflictivo")
            path = self.path(upload)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "r+b") as output:
                if output.seek(0, os.SEEK_END) < upload.offset:
                    raise OSError("Committed evidence bytes are missing.")
                # Discard only an uncommitted tail left by an interrupted request/process.
                output.truncate(upload.offset)
                output.seek(upload.offset)
                remaining = length
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise Problem(400, "carga_interrumpida")
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                directory_fd = os.open(self.config.storage_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            upload.offset += length
            self.audit(session, owner, "cargar_fragmento", "persistido", cid)
            return upload_record(upload)

    def confirm(self, owner: str, claim_id: str, upload_id: str, cid: str) -> dict[str, Any]:
        rejection: Problem | None = None
        with self.write() as session:
            upload = self.upload(session, owner, claim_id, upload_id, lock=True)
            if upload.status == "confirmada":
                return upload_record(upload)
            if upload.status == "cancelada":
                raise Problem(409, "carga_cancelada")
            self.check_policy(upload)
            if upload.offset != upload.size:
                raise Problem(409, "carga_incompleta")
            path = self.path(upload)
            try:
                with path.open("rb") as content:
                    digest = hashlib.file_digest(content, "sha256").hexdigest()
                if path.stat().st_size != upload.size or digest != upload.digest:
                    raise Problem(422, "integridad_invalida")
                media.validate(path, upload.mime, upload.location is not None, self.config)
            except Problem as error:
                # Invalid/unauthorized bytes are removed, not retained as hidden evidence.
                path.unlink(missing_ok=True)
                upload.offset = 0
                rejection = error
                self.audit(session, owner, "confirmar_carga", error.code, cid)
            else:
                upload.status = "confirmada"
                upload.confirmed = now()
                self.audit(session, owner, "confirmar_carga", "confirmada", cid)
            result = upload_record(upload)
        if rejection is not None:
            raise rejection
        return result

    def cancel(self, owner: str, claim_id: str, upload_id: str, cid: str) -> None:
        with self.write() as session:
            upload = self.upload(session, owner, claim_id, upload_id, lock=True)
            if upload.status == "confirmada":
                raise Problem(409, "carga_confirmada")
            upload.status = "cancelada"
            upload.offset = 0
            self.path(upload).unlink(missing_ok=True)
            self.audit(session, owner, "cancelar_carga", "cancelada", cid)

    def list_evidence(self, owner: str, claim_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            self.claim(session, owner, claim_id)
            return [
                evidence_record(upload)
                for upload in session.scalars(
                    select(Upload)
                    .where(Upload.claim_id == claim_id, Upload.status == "confirmada")
                    .order_by(Upload.confirmed, Upload.id)
                )
            ]

    def content(self, owner: str, claim_id: str, upload_id: str, cid: str) -> tuple[Path, str, int]:
        with self.write() as session:
            try:
                upload = self.upload(session, owner, claim_id, upload_id)
            except Problem as error:
                if error.code == "carga_no_encontrada":
                    raise Problem(404, "evidencia_no_encontrada") from error
                raise
            if upload.status != "confirmada":
                raise Problem(404, "evidencia_no_encontrada")
            path = self.path(upload)
            with path.open("rb") as content:
                digest = hashlib.file_digest(content, "sha256").hexdigest()
            if path.stat().st_size != upload.size or digest != upload.digest:
                raise Problem(503, "servicio_no_disponible")
            self.audit(session, owner, "consultar_contenido", "autorizado", cid)
            return path, upload.mime, upload.size
