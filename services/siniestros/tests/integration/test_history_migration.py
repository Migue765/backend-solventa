import hashlib
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config as AlembicConfig
from siniestros.domain import ClaimInput, Config
from siniestros.infrastructure.repositorio import RepositorioSiniestros
from siniestros.models import Audit, Upload
from siniestros.repository import Repository, fingerprint, make_engine
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_upload_offset_constraint_is_valid_for_postgresql() -> None:
    migration = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    output = StringIO()
    migration.output_buffer = output
    migration.set_main_option("sqlalchemy.url", "postgresql://localhost/siniestros")
    command.upgrade(migration, "0001_siniestros", sql=True)

    constraint = 'CHECK ("offset" >= 0 AND "offset" <= size)'
    assert constraint in output.getvalue()
    assert constraint in str(CreateTable(Upload.__table__).compile(dialect=postgresql.dialect()))


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
        with repository.sessions() as session:
            legacy = session.scalar(select(Audit))
            assert legacy is not None
            assert legacy.operation is legacy.fingerprint is legacy.http_status is None
        replay, status = repository.create_claim(owner, operation, data, str(uuid4()))
        assert status == 200 and replay["id"] == claim_id and replay["estado"] == "registrado"
        assert replay["ultimoResultado"] == {
            "operacionId": operation,
            "fecha": created,
            "httpStatus": 201,
            "code": None,
        }
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
        recovered = repository.get_upload(owner, claim_id, upload_id)
        assert recovered["estado"] == "confirmada"
        assert recovered["ultimoResultado"]["fecha"] == created
        assert recovered["ultimoResultado"]["httpStatus"] == 200
        assert (storage / upload_id).read_bytes() == content
    finally:
        repository.engine.dispose()
        engine.dispose()
