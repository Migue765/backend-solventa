from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from perfilamiento.app import Config, create_app
from perfilamiento.domain import Consentimiento, ErrorPerfilamiento
from perfilamiento.exceptions import (
    AbrirExcepcionOpenFinance,
    GestionarExcepcionesOpenFinance,
    ResolverExcepcionOpenFinance,
)
from perfilamiento.persistence import (
    EventoExcepcionOpenFinanceModel,
    ExcepcionOpenFinanceModel,
    RepositorioPerfiles,
)
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

AHORA = datetime(2026, 10, 16, tzinfo=UTC)
TOKEN = "Bearer interno-test"


class ConsentimientosExcepcion:
    def __init__(self, client_id: UUID, ahora: datetime = AHORA) -> None:
        self.actual = Consentimiento(
            consentimientoId=uuid4(),
            clienteId=client_id,
            version=3,
            estado="autorizado",
            finalidad="cotizacion",
            alcances=("open_finance", "open_data"),
            fuentes=("open_finance", "open_data"),
            datos=("indicador_riesgo",),
            actorId=client_id,
            versionTerminos="1.0",
            efectivaEn=ahora - timedelta(days=1),
            vigenteHasta=ahora + timedelta(days=1),
        )

    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento:
        if self.actual is None:
            raise ErrorPerfilamiento("consentimiento_ausente", 403)
        return self.actual


def _command(
    consent: ConsentimientosExcepcion,
    *,
    operation_id: UUID | None = None,
    blocked: bool = True,
    reason: str = "La regla requiere validacion manual",
) -> AbrirExcepcionOpenFinance:
    return AbrirExcepcionOpenFinance(
        operationId=operation_id or uuid4(),
        socioId="BANCO-001",
        clienteId=consent.actual.cliente_id,
        consentimientoId=consent.actual.consentimiento_id,
        consentimientoVersion=consent.actual.version,
        codigo="regla_no_aplicable",
        motivoBloqueo=reason,
        bloqueaProcesamiento=blocked,
    )


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioPerfiles:
    repository = RepositorioPerfiles(f"sqlite:///{tmp_path / 'excepciones.db'}")
    repository.inicializar()
    return repository


def test_registration_list_and_detail_are_persisted_idempotently(
    repo: RepositorioPerfiles,
) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    command = _command(consentimientos)

    opened = service.abrir(command, "corr-open")
    repeated = service.abrir(command, "corr-retry")
    pending = service.listar_pendientes("BANCO-001")
    detail = service.obtener(opened.excepcion_id, "BANCO-001")

    assert opened.estado == "pendiente"
    assert opened.alcance == "open_finance"
    assert opened.finalidad == "cotizacion"
    assert opened.datos == ("indicador_riesgo",)
    assert opened.consentimiento.version == 3
    assert repeated.excepcion_id == opened.excepcion_id
    assert repeated.idempotente
    assert len(pending) == 1
    assert detail.motivo_bloqueo == command.motivo_bloqueo
    with Session(repo.engine) as session:
        assert session.scalar(select(func.count()).select_from(ExcepcionOpenFinanceModel)) == 1
        assert (
            session.scalar(select(func.count()).select_from(EventoExcepcionOpenFinanceModel)) == 1
        )


def test_processing_must_be_blocked_and_conflicting_replay_is_rejected(
    repo: RepositorioPerfiles,
) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    operation_id = uuid4()
    opened = service.abrir(_command(consentimientos, operation_id=operation_id), "corr-first")

    with pytest.raises(ValueError):
        _command(consentimientos, operation_id=uuid4(), blocked=False)
    with pytest.raises(ErrorPerfilamiento, match="conflicto_idempotencia"):
        service.abrir(
            _command(
                consentimientos,
                operation_id=operation_id,
                reason="Causa diferente para la misma operacion",
            ),
            "corr-conflict",
        )
    assert service.obtener(opened.excepcion_id, "BANCO-001").estado == "pendiente"


def test_approval_is_audited_but_never_changes_consent(repo: RepositorioPerfiles) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    opened = service.abrir(_command(consentimientos), "corr-open")

    resolved = service.resolver(
        opened.excepcion_id,
        "BANCO-001",
        uuid4(),
        ResolverExcepcionOpenFinance(decision="aprobar", motivo="Revision humana aprobada"),
        "corr-resolved",
    )

    assert resolved.estado == "aprobada"
    assert resolved.estado_anterior == "pendiente"
    assert resolved.decision == "aprobar"
    assert resolved.motivo == "Revision humana aprobada"
    assert resolved.consentimiento.id == opened.consentimiento.id
    assert resolved.consentimiento.version == opened.consentimiento.version
    assert resolved.trazabilidad["alcance"] == "open_finance"
    with Session(repo.engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(EventoExcepcionOpenFinanceModel)) == 2
        )
        transition = session.scalars(
            select(EventoExcepcionOpenFinanceModel).where(
                EventoExcepcionOpenFinanceModel.contenido["tipo"].as_string()
                == "ExcepcionOpenFinanceResuelta"
            )
        ).first()
        assert transition is not None
        assert transition.contenido["actorId"] != ""
        assert transition.contenido["estadoAnterior"] == "pendiente"
        assert transition.contenido["estadoActual"] == "aprobada"


def test_approval_with_revoked_consent_does_not_resolve_exception(
    repo: RepositorioPerfiles,
) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    opened = service.abrir(_command(consentimientos), "corr-open")
    consentimientos.actual = consentimientos.actual.model_copy(
        update={"estado": "revocado", "version": consentimientos.actual.version + 1}
    )

    with pytest.raises(ErrorPerfilamiento, match="consentimiento_no_vigente"):
        service.resolver(
            opened.excepcion_id,
            "BANCO-001",
            uuid4(),
            ResolverExcepcionOpenFinance(decision="aprobar", motivo="Aprobacion no permitida"),
            "corr-denied",
        )
    assert service.obtener(opened.excepcion_id, "BANCO-001").estado == "pendiente"


def test_rejection_remains_available_after_consent_revocation(
    repo: RepositorioPerfiles,
) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    opened = service.abrir(_command(consentimientos), "corr-open")
    consentimientos.actual = consentimientos.actual.model_copy(update={"estado": "revocado"})

    resolved = service.resolver(
        opened.excepcion_id,
        "BANCO-001",
        uuid4(),
        ResolverExcepcionOpenFinance(decision="rechazar", motivo="Datos insuficientes"),
        "corr-rejected",
    )

    assert resolved.estado == "rechazada"
    assert resolved.consentimiento.estado == "autorizado"


def test_api_authentication_and_mandatory_resolution_reason(repo: RepositorioPerfiles) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4(), datetime.now(UTC))
    app = create_app(
        Config(internal_api_token="interno-test", database_url=str(repo.engine.url)),
        consentimientos=consentimientos,
    )
    client = app.test_client()
    command = _command(consentimientos).model_dump(mode="json", by_alias=True)
    path = "/v1/excepciones-open-finance"
    assert client.post(path, json=command).status_code == 401

    opened = client.post(path, json=command, headers={"Authorization": TOKEN})
    assert opened.status_code == 201
    exception_id = opened.get_json()["excepcionId"]
    response = client.post(
        f"{path}/{exception_id}/resolver?socioId=BANCO-001",
        json={"decision": "aprobar", "motivo": "  "},
        headers={"Authorization": TOKEN, "X-Actor-ID": str(uuid4())},
    )
    assert response.status_code == 400
    assert client.get(path, query_string={"socioId": "BANCO-001"}, headers={"Authorization": TOKEN})
    assert (
        client.get(
            f"{path}/{exception_id}?socioId=BANCO-999", headers={"Authorization": TOKEN}
        ).status_code
        == 404
    )


def test_resolution_and_audit_event_roll_back_together(repo: RepositorioPerfiles) -> None:
    consentimientos = ConsentimientosExcepcion(uuid4())
    service = GestionarExcepcionesOpenFinance(repo, consentimientos, lambda: AHORA)
    opened = service.abrir(_command(consentimientos), "corr-open")

    def fail_event(*args: object) -> None:
        raise RuntimeError("outbox_no_disponible")

    event.listen(EventoExcepcionOpenFinanceModel, "before_insert", fail_event)
    try:
        with pytest.raises(RuntimeError, match="outbox_no_disponible"):
            service.resolver(
                opened.excepcion_id,
                "BANCO-001",
                uuid4(),
                ResolverExcepcionOpenFinance(
                    decision="aprobar", motivo="Aprobacion con trazabilidad"
                ),
                "corr-atomic",
            )
    finally:
        event.remove(EventoExcepcionOpenFinanceModel, "before_insert", fail_event)

    assert service.obtener(opened.excepcion_id, "BANCO-001").estado == "pendiente"
    with Session(repo.engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(EventoExcepcionOpenFinanceModel)) == 1
        )
