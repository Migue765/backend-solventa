import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config as AlembicConfig
from siniestros.domain import ClaimInput, Config
from siniestros.infrastructure.repositorio import RepositorioSiniestros
from siniestros.repository import Repository, fingerprint, make_engine
from sqlalchemy import MetaData, Table, select, text


def test_upgrade_preserves_claims_replays_uploads_audit_and_private_bytes(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    migration = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    migration.set_main_option("sqlalchemy.url", url)
    command.upgrade(migration, "0001_siniestros")
    engine = make_engine(url)
    metadata = MetaData()
    claims = Table("siniestros", metadata, autoload_with=engine)
    uploads = Table("cargas_siniestros", metadata, autoload_with=engine)
    audits = Table("auditoria_siniestros", metadata, autoload_with=engine)
    owner, claim_id, operation, upload_id = (str(uuid4()) for _ in range(4))
    created = "2026-11-01T15:00:00+00:00"
    data = ClaimInput.model_validate(
        {
            "polizaId": str(uuid4()),
            "fechaEvento": "2026-11-01T14:00:00Z",
            "tipoEvento": "Accidente",
            "descripcion": "Aviso anterior a la migracion.",
            "lugar": "Lugar de prueba",
        }
    )
    payload = data.model_dump(mode="json")
    storage = tmp_path / "privado"
    storage.mkdir()
    content = b"evidencia ya confirmada"
    (storage / upload_id).write_bytes(content)
    with engine.begin() as connection:
        connection.execute(
            claims.insert().values(
                id=claim_id,
                owner=owner,
                operation=operation,
                fingerprint=fingerprint(payload),
                payload=payload,
                created=created,
            )
        )
        connection.execute(
            uploads.insert().values(
                id=upload_id,
                claim_id=claim_id,
                owner=owner,
                operation=str(uuid4()),
                fingerprint="a" * 64,
                mime="application/pdf",
                size=len(content),
                digest=hashlib.sha256(content).hexdigest(),
                offset=len(content),
                status="confirmada",
                created=created,
                confirmed=created,
            )
        )
        connection.execute(
            audits.insert().values(
                id=str(uuid4()),
                actor=owner,
                action="confirmar_evidencia",
                result="confirmada",
                correlation=str(uuid4()),
                created=created,
            )
        )
        before = [connection.execute(select(table)).all() for table in (claims, uploads, audits)]

    command.upgrade(migration, "head")

    with engine.connect() as connection:
        assert [
            connection.execute(select(table)).all() for table in (claims, uploads, audits)
        ] == before
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    config = Config(url, storage, "local-test-token", "http://127.0.0.1:9")
    repository = Repository(config)
    try:
        replay, status = repository.create_claim(owner, operation, data, str(uuid4()))
        assert status == 200 and replay["id"] == claim_id and replay["estado"] == "registrado"
        result = RepositorioSiniestros(repository.sessions).consultar(UUID(claim_id), UUID(owner))
        assert result is not None
        assert result["tipo"] is None
        assert result["estado"] == "reportado"
        assert result["actualizadoEn"] == "2026-11-01T15:00:00Z"
        assert result["historial"] == [
            {
                "estado": "reportado",
                "descripcion": "Recibimos el aviso de tu siniestro.",
                "ocurridoEn": "2026-11-01T15:00:00Z",
            }
        ]
        assert repository.get_upload(owner, claim_id, upload_id)["estado"] == "confirmada"
        assert (storage / upload_id).read_bytes() == content
    finally:
        repository.engine.dispose()
        engine.dispose()
