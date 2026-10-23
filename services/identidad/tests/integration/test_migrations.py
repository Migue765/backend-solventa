from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from identidad.consent_outbox import ConsentOutbox
from identidad.domain.consent import Consent
from identidad.infrastructure.persistence.consent_repository import SqlConsentRepository
from identidad.infrastructure.persistence.models import ConsentModel, UsuarioModel
from sqlalchemy import MetaData, Table, create_engine, inspect
from sqlalchemy.orm import sessionmaker

SERVICE_DIR = Path(__file__).resolve().parents[2]


def test_migrations_create_both_tables_with_their_own_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DB_SCHEMA", "identidad")
    config = AlembicConfig(str(SERVICE_DIR / "alembic.ini"))

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert {
        "usuarios",
        "refresh_tokens",
        "socios",
        "asesores_socios",
        "alembic_version_identidad",
    } <= set(inspector.get_table_names())
    assert "alembic_version" not in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}
    assert columns == {
        "token_hash",
        "usuario_id",
        "family_id",
        "client_id",
        "creado_en",
        "expira_en",
        "revocado_en",
        "reemplazado_por",
    }

    command.downgrade(config, "base")
    remaining = set(inspect(create_engine(url)).get_table_names())
    assert not {"usuarios", "refresh_tokens", "socios", "asesores_socios"} & remaining


def test_upgrade_preserves_legacy_evidence_without_granting_new_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("DB_SCHEMA", raising=False)
    config = AlembicConfig(str(SERVICE_DIR / "alembic.ini"))
    command.upgrade(config, "0002")
    engine = create_engine(url)
    sessions = sessionmaker(engine)
    now = datetime.now(UTC)
    user_id, event_id = uuid4(), uuid4()
    old = Consent(
        consentimientoId=uuid4(),
        clienteId=user_id,
        version=1,
        estado="autorizado",
        efectivaEn=now,
        vigenteHasta=now + timedelta(days=30),
    ).model_dump(
        mode="json", by_alias=True, exclude={"actor_id", "fuentes", "datos", "version_terminos"}
    )
    legacy_outbox = Table("outbox_consentimientos", MetaData(), autoload_with=engine)
    with sessions.begin() as session:
        session.add(
            UsuarioModel(
                usuario_id=user_id,
                correo="legacy@solventa.co",
                nombre="Legacy",
                rol="cliente",
                password_hash="no-utilizable",
            )
        )
        session.flush()
        session.add(ConsentModel(cliente_id=user_id, version=1, contenido=old))
        session.execute(
            legacy_outbox.insert().values(
                evento_id=event_id.hex,
                tipo="ConsentimientoAutorizado",
                publicado=False,
                contenido={
                    **old,
                    "eventoId": str(event_id),
                    "tipo": "ConsentimientoAutorizado",
                    "correlationId": "legacy",
                },
            )
        )
    command.upgrade(config, "head")
    repository = SqlConsentRepository(sessions)
    consent = repository.get(user_id)
    assert consent is not None and consent.version == 1
    assert consent.actor_id is None and consent.fuentes == () and consent.datos == ()
    assert not consent.is_current(now)
    revoked = repository.record(user_id, False, 1, datetime.now(UTC), "revocar-legado")
    assert revoked.estado == "revocado"
    assert not revoked.is_current(datetime.now(UTC))

    def confirm(event: dict[str, object]) -> dict[str, object]:
        return {
            "eventoId": event["eventoId"],
            "versionAplicada": event["version"],
            "aplicadaEn": datetime.now(UTC).isoformat(),
        }

    assert ConsentOutbox(sessions, confirm).deliver() == (2, 0)
