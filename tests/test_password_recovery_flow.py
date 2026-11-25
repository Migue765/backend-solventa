import hashlib
import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Any

import boto3
import fakeredis
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_movil.app import create_app as create_mobile
from bff_movil.config import Config as MobileConfig
from bff_web.app import create_app as create_web
from bff_web.config import Config as WebConfig
from botocore.stub import Stubber
from flask import Flask
from identidad.app import create_app
from identidad.application.password_recovery_service import RecoveryUnavailableError
from identidad.config import Config
from identidad.infrastructure.passwords import verify_password
from identidad.infrastructure.persistence.models import (
    BiometricCredentialModel,
    PasswordRecoveryModel,
    RefreshTokenModel,
    UsuarioModel,
)
from identidad.infrastructure.persistence.password_recovery_repository import (
    SqlPasswordRecoveryRepository,
)
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.infrastructure.ses_recovery_mail import SesRecoveryMailSender
from jwcrypto import jwe, jwk
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.serving import make_server

BASE = "/v1/recuperacion-contrasena"
EMAIL = "recovery@example.com"
OLD_PASSWORD = "Original123!"
NEW_PASSWORD = "NewPassword123!"


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


class RecordingMail:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.available = True
        self.fail_send = False

    def ensure_available(self) -> None:
        if not self.available:
            raise RecoveryUnavailableError()

    def send(self, email: str, token: str) -> None:
        if self.fail_send:
            raise RecoveryUnavailableError()
        self.messages.append((email, token))


@dataclass
class Flow:
    identity: Flask
    web: Flask
    clock: Clock
    mail: RecordingMail
    sessions: sessionmaker[Session]
    mobile: Flask


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flow]:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *args, **kwargs: redis)
    signing = jwk.JWK.generate(kty="RSA", size=2048)
    encryption = jwk.JWK.generate(kty="RSA", size=2048)
    database = f"sqlite:///{tmp_path / 'recovery.db'}"
    engine = create_engine(database)
    migration = AlembicConfig(
        str(Path(__file__).resolve().parents[1] / "services" / "identidad" / "alembic.ini")
    )
    migration.set_main_option("sqlalchemy.url", database)
    monkeypatch.delenv("DB_RUNTIME_ROLE", raising=False)
    command.upgrade(migration, "head")
    clock = Clock(datetime.now(UTC))
    mail = RecordingMail()
    identity = create_app(
        Config(
            database_url=database,
            jwt_private_key=signing.export_to_pem(private_key=True, password=None).decode(),
            jwt_issuer="https://recovery.solventa.test",
            redis_url="redis://unused",
            encrypted_login_enabled=True,
            login_encryption_private_key=encryption.export_to_pem(
                private_key=True, password=None
            ).decode(),
            internal_api_token="test-internal-recovery",
            password_iterations=1000,
            max_attempts=100,
        ),
        clock,
        recovery_mail_sender=mail,
    )
    registered = identity.test_client().post(
        "/v1/usuarios", json={"nombre": "Recovery", "correo": EMAIL, "contrasena": OLD_PASSWORD}
    )
    assert registered.status_code == 201
    server = make_server("127.0.0.1", 0, identity, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    web = create_web(
        WebConfig(
            identity_url=f"http://127.0.0.1:{server.server_port}",
            jwt_issuer="https://recovery.solventa.test",
            internal_api_token="test-internal-recovery",
            encrypted_login_enabled=True,
        )
    )
    try:
        mobile = create_mobile(
            MobileConfig(
                identity_url=f"http://127.0.0.1:{server.server_port}",
                jwt_issuer="https://recovery.solventa.test",
                internal_api_token="test-internal-recovery",
            )
        )
        yield Flow(identity, web, clock, mail, sessionmaker(bind=engine), mobile)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engine.dispose()
        assert not thread.is_alive()


def encrypt(challenge: dict[str, Any], payload: dict[str, object], purpose: str = "reset") -> str:
    encrypted = jwe.JWE(
        json.dumps({"desafioId": challenge["desafioId"], **payload}).encode(),
        protected=json.dumps(
            {
                "alg": "RSA-OAEP-256",
                "enc": "A256GCM",
                "kid": challenge["clavePublica"]["kid"],
                "typ": f"solventa-{purpose}+jwe",
            }
        ),
    )
    encrypted.add_recipient(jwk.JWK(**challenge["clavePublica"]))
    return str(encrypted.serialize(compact=True))


def request_token(flow: Flow) -> str:
    response = flow.web.test_client().post(BASE, json={"correo": EMAIL})
    assert response.status_code == 202
    token = flow.mail.messages[-1][1]
    assert token not in json.dumps(response.get_json())
    assert len(token) == 64
    return token


def test_mobile_recovery_with_real_identity(flow: Flow) -> None:
    client = flow.mobile.test_client()
    base = "/movil" + BASE
    missing = client.post(base, json={"correo": "nobody@example.com"})
    accepted = client.post(base, json={"correo": EMAIL})
    assert accepted.status_code == missing.status_code == 202
    assert accepted.json == missing.json and set(accepted.get_json()) == {"mensaje"}
    token = flow.mail.messages[-1][1]
    assert (
        client.post(
            base + "/confirmar", json={"token": token, "contrasena": NEW_PASSWORD}
        ).status_code
        == 400
    )
    for status in (204, 400):
        challenge = client.get(base + "/desafio")
        assert challenge.status_code == 200 and challenge.headers["Cache-Control"] == "no-store"
        envelope = encrypt(challenge.get_json(), {"token": token, "contrasena": NEW_PASSWORD})
        result = client.post(
            base + "/confirmar", json={"token": token, "credencialCifrada": envelope}
        )
        assert result.status_code == status and result.headers["Cache-Control"] == "no-store"
        if status == 400:
            assert result.get_json()["code"] == "recuperacion_invalida"
    for password, expected in ((NEW_PASSWORD, 200), (OLD_PASSWORD, 401)):
        assert (
            client.post(
                "/movil/v1/sesiones", json={"correo": EMAIL, "contrasena": password}
            ).status_code
            == expected
        )


def confirm_body(flow: Flow, token: str, password: str = NEW_PASSWORD) -> dict[str, object]:
    issued = flow.web.test_client().get(BASE + "/desafio")
    assert issued.status_code == 200
    return {
        "token": token,
        "credencialCifrada": encrypt(issued.get_json(), {"token": token, "contrasena": password}),
    }


def login(flow: Flow, password: str) -> Any:
    client = flow.web.test_client()
    issued = client.get("/v1/sesiones/desafio")
    return client.post(
        "/v1/sesiones",
        json={
            "correo": EMAIL,
            "credencialCifrada": encrypt(
                issued.get_json(), {"correo": EMAIL, "contrasena": password}, "login"
            ),
        },
    )


def test_recovery_changes_only_its_account_revokes_sessions_and_never_returns_secrets(
    flow: Flow, caplog: pytest.LogCaptureFixture
) -> None:
    other = flow.identity.test_client().post(
        "/v1/usuarios",
        json={"nombre": "Other", "correo": "other@example.com", "contrasena": OLD_PASSWORD},
    )
    assert other.status_code == 201
    original = login(flow, OLD_PASSWORD).get_json()
    auth = {"Authorization": "Bearer " + original["accessToken"]}
    assert flow.web.test_client().get("/v1/sesiones/actual", headers=auth).status_code == 200
    token = request_token(flow)
    second = request_token(flow)
    with flow.sessions() as session:
        row = session.get(PasswordRecoveryModel, hashlib.sha256(token.encode()).hexdigest())
        assert row is not None and row.token_hash != token
        assert row.consumida_en is None
    response = flow.web.test_client().post(BASE + "/confirmar", json=confirm_body(flow, token))
    assert response.status_code == 204 and response.data == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert flow.web.test_client().get("/v1/sesiones/actual", headers=auth).status_code == 401
    renewal = flow.web.test_client().post(
        "/v1/sesiones/renovar", json={"refreshToken": original["refreshToken"]}
    )
    assert renewal.status_code == 401
    assert login(flow, OLD_PASSWORD).status_code == 401
    assert login(flow, NEW_PASSWORD).status_code == 200
    for invalid in (token, second):
        rejected = flow.web.test_client().post(
            BASE + "/confirmar", json=confirm_body(flow, invalid)
        )
        assert rejected.status_code == 400
        assert rejected.get_json()["code"] == "recuperacion_invalida"
    with flow.sessions() as session:
        other_user = session.scalar(
            select(UsuarioModel).where(UsuarioModel.correo == "other@example.com")
        )
        assert other_user is not None and verify_password(OLD_PASSWORD, other_user.password_hash)
    assert token not in caplog.text and OLD_PASSWORD not in caplog.text
    assert NEW_PASSWORD not in caplog.text


@pytest.mark.parametrize("kind", ["expired", "unknown", "weak", "mismatch", "wrong-purpose"])
def test_invalid_recovery_does_not_mutate_password_or_live_session(flow: Flow, kind: str) -> None:
    original = login(flow, OLD_PASSWORD).get_json()
    token = request_token(flow)
    if kind == "expired":
        flow.clock.now += timedelta(minutes=15)
    body = confirm_body(
        flow, "x" * 64 if kind == "unknown" else token, "short" if kind == "weak" else NEW_PASSWORD
    )
    if kind == "mismatch":
        body["token"] = "y" * 64
    if kind == "wrong-purpose":
        issued = flow.web.test_client().get("/v1/sesiones/desafio").get_json()
        body["credencialCifrada"] = encrypt(issued, {"token": token, "contrasena": NEW_PASSWORD})
    rejected = flow.web.test_client().post(BASE + "/confirmar", json=body)
    assert rejected.status_code == 400
    with flow.sessions() as session:
        user = session.scalar(select(UsuarioModel).where(UsuarioModel.correo == EMAIL))
        assert user is not None and verify_password(OLD_PASSWORD, user.password_hash)
        assert session.scalar(select(RefreshTokenModel.revocado_en)) is None
        stored = session.get(PasswordRecoveryModel, hashlib.sha256(token.encode()).hexdigest())
        assert stored is not None and stored.consumida_en is None
    assert (
        flow.web.test_client()
        .get("/v1/sesiones/actual", headers={"Authorization": "Bearer " + original["accessToken"]})
        .status_code
        == 200
    )


def test_request_is_generic_and_delivery_failure_never_reports_success(flow: Flow) -> None:
    client = flow.web.test_client()
    known = client.post(BASE, json={"correo": EMAIL})
    unknown = client.post(BASE, json={"correo": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.get_json() == unknown.get_json()
    assert len(flow.mail.messages) == 1
    flow.mail.available = False
    for email in (EMAIL, "unknown@example.com"):
        failed = client.post(BASE, json={"correo": email})
        assert failed.status_code == 503
        assert failed.get_json()["code"] == "servicio_no_disponible"
    flow.mail.available = True
    flow.mail.fail_send = True
    assert client.post(BASE, json={"correo": EMAIL}).status_code == 503
    with flow.sessions() as session:
        rows: list[PasswordRecoveryModel] = list(session.scalars(select(PasswordRecoveryModel)))
        assert len(rows) == 2 and sum(row.consumida_en is not None for row in rows) == 1


def test_password_and_token_consumption_roll_back_together(flow: Flow) -> None:
    token = request_token(flow)
    repository = SqlPasswordRecoveryRepository(flow.sessions)

    def fail_update(*args: Any) -> None:
        raise RuntimeError("synthetic persistence failure")

    event.listen(UsuarioModel, "before_update", fail_update)
    try:
        with pytest.raises(RuntimeError, match="synthetic persistence"):
            repository.consume(hashlib.sha256(token.encode()).hexdigest(), "new-hash", flow.clock())
    finally:
        event.remove(UsuarioModel, "before_update", fail_update)
    with flow.sessions() as session:
        row = session.get(PasswordRecoveryModel, hashlib.sha256(token.encode()).hexdigest())
        assert row is not None and row.consumida_en is None
        user = session.scalar(select(UsuarioModel).where(UsuarioModel.correo == EMAIL))
        assert user is not None and verify_password(OLD_PASSWORD, user.password_hash)
    assert (
        flow.web.test_client().post(BASE + "/confirmar", json=confirm_body(flow, token)).status_code
        == 204
    )


def test_token_consumption_has_only_one_winner(flow: Flow) -> None:
    token = request_token(flow)
    repository = SqlPasswordRecoveryRepository(flow.sessions)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                lambda _: repository.consume(token_hash, "new-hash", flow.clock()), range(2)
            )
        )
    assert sorted(results) == [False, True]


def test_expired_challenge_and_plaintext_or_account_override_are_rejected(flow: Flow) -> None:
    token = request_token(flow)
    body = confirm_body(flow, token)
    flow.clock.now += timedelta(seconds=60)
    assert flow.web.test_client().post(BASE + "/confirmar", json=body).status_code == 400
    for changes in ({"correo": "other@example.com"}, {"contrasena": NEW_PASSWORD}):
        payload = {**confirm_body(flow, token), **changes}
        assert flow.web.test_client().post(BASE + "/confirmar", json=payload).status_code == 400
    assert (
        flow.web.test_client()
        .post(BASE + "/confirmar", json={"token": token, "contrasena": NEW_PASSWORD})
        .status_code
        == 400
    )
    assert (
        flow.web.test_client().post(BASE + "/confirmar", json=confirm_body(flow, token)).status_code
        == 204
    )


def test_recovery_revokes_biometric_credentials(flow: Flow) -> None:
    from uuid import uuid4

    token = request_token(flow)
    credential_id = uuid4()
    with flow.sessions.begin() as session:
        user_id = session.scalar(
            select(UsuarioModel.usuario_id).where(UsuarioModel.correo == EMAIL)
        )
        session.add(
            BiometricCredentialModel(
                credencial_id=credential_id,
                usuario_id=user_id,
                clave_publica=b"synthetic",
                clave_hash="synthetic",
                creada_en=flow.clock(),
            )
        )
    assert (
        flow.web.test_client().post(BASE + "/confirmar", json=confirm_body(flow, token)).status_code
        == 204
    )
    with flow.sessions() as session:
        credential = session.get(BiometricCredentialModel, credential_id)
        assert credential is not None and credential.revocada_en is not None


def test_request_limiter_limits_unknown_accounts_without_sending_mail(flow: Flow) -> None:
    from identidad.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter

    flow.identity.extensions["recovery_limiter"] = InMemoryLoginAttemptLimiter(
        1, timedelta(minutes=5)
    )
    client = flow.web.test_client()
    assert client.post(BASE, json={"correo": "unknown@example.com"}).status_code == 202
    limited = client.post(BASE, json={"correo": "different@example.com"})
    assert limited.status_code == 429
    assert limited.get_json()["code"] == "demasiados_intentos"
    assert int(limited.headers["Retry-After"]) > 0
    assert flow.mail.messages == []


def test_forwarded_client_ip_requires_internal_authentication(flow: Flow) -> None:
    from identidad.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter

    flow.identity.extensions["recovery_limiter"] = InMemoryLoginAttemptLimiter(
        1, timedelta(minutes=5)
    )
    client = flow.identity.test_client()
    for number in (1, 2):
        accepted = client.post(
            BASE,
            json={"correo": f"unknown{number}@example.com"},
            headers={
                "Authorization": "Bearer test-internal-recovery",
                "X-Recovery-Client-IP": f"192.0.2.{number}",
            },
        )
        assert accepted.status_code == 202
    for number in (3, 4):
        response = client.post(
            BASE,
            json={"correo": f"unknown{number}@example.com"},
            headers={"X-Recovery-Client-IP": f"192.0.2.{number}"},
        )
        assert response.status_code == (202 if number == 3 else 429)


def test_signed_session_is_not_accepted_when_internal_state_is_unavailable(flow: Flow) -> None:
    original = login(flow, OLD_PASSWORD).get_json()
    config = flow.identity.extensions["identidad_config"]
    flow.identity.extensions["identidad_config"] = replace(
        config, internal_api_token="different-synthetic-token"
    )
    response = flow.web.test_client().get(
        "/v1/sesiones/actual", headers={"Authorization": "Bearer " + original["accessToken"]}
    )
    assert response.status_code == 503


@pytest.mark.parametrize("production,verified", [(False, True), (True, False)])
def test_ses_refuses_sandbox_or_unverified_sender(production: bool, verified: bool) -> None:
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    with Stubber(client) as stub:
        stub.add_response(
            "get_account", {"ProductionAccessEnabled": production, "SendingEnabled": True}
        )
        stub.add_response(
            "get_email_identity", {"VerifiedForSendingStatus": verified}, {"EmailIdentity": EMAIL}
        )
        sender = SesRecoveryMailSender("us-east-2", EMAIL, "https://web.example.com/app", client)
        with pytest.raises(RecoveryUnavailableError):
            sender.ensure_available()
        stub.assert_no_pending_responses()


def test_ses_uses_real_sdk_and_root_entry_link_without_token_in_query() -> None:
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    calls: list[dict[str, Any]] = []

    def capture(params: dict[str, Any], **kwargs: Any) -> None:
        calls.append(params)

    client.meta.events.register("before-parameter-build.sesv2.SendEmail", capture)
    with Stubber(client) as stub:
        stub.add_response("get_account", {"ProductionAccessEnabled": True, "SendingEnabled": True})
        stub.add_response("get_email_identity", {"VerifiedForSendingStatus": True})
        stub.add_response("send_email", {"MessageId": "accepted-by-ses"})
        sender = SesRecoveryMailSender("us-east-2", EMAIL, "https://web.example.com/app", client)
        sender.ensure_available()
        sender.send("recipient@example.com", "a" * 64)
        stub.assert_no_pending_responses()
    text = calls[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "https://web.example.com/app/?recuperacion=1#token=" + "a" * 64 in text
    assert calls[0]["Destination"] == {"ToAddresses": ["recipient@example.com"]}


def test_common_mail_redirect_preserves_original_user_and_recovery_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox = "redirect@example.com"
    monkeypatch.setenv("EMAIL_REDIRECT_TO", mailbox)
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    calls: list[dict[str, Any]] = []
    client.meta.events.register(
        "before-parameter-build.sesv2.SendEmail",
        lambda params, **kwargs: calls.append(params),
    )
    sender = SesRecoveryMailSender("us-east-2", EMAIL, "https://web.example.com", client)
    with Stubber(client) as stub:
        stub.add_response("get_account", {"SendingEnabled": True, "ProductionAccessEnabled": False})
        stub.add_response("get_email_identity", {"VerifiedForSendingStatus": True})
        stub.add_response(
            "get_email_identity", {"VerifiedForSendingStatus": True}, {"EmailIdentity": mailbox}
        )
        sender.ensure_available()
        stub.add_response("send_email", {"MessageId": "recovery-accepted"})
        sender.send("original@example.com", "x" * 64)
        stub.add_response("send_email", {"MessageId": "indemnity-accepted"})
        assert (
            sender.send_notification(
                "other@example.com",
                "synthetic-client",
                "Pago simulado",
                "Indemnizacion confirmada: 1725000.00 COP. Referencia: synthetic-payment",
            )
            == "indemnity-accepted"
        )
    assert len(calls) == 2
    for call in calls:
        assert call["Destination"] == {"ToAddresses": [mailbox]}
    recovery = calls[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "Destinatario original: original@example.com" in recovery
    assert "#token=" + "x" * 64 in recovery
    payment = calls[1]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "Usuario: synthetic-client" in payment
    assert "Destinatario original: other@example.com" in payment
    assert "1725000.00 COP" in payment


def test_ses_access_denied_is_an_explicit_unavailable_error() -> None:
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    with Stubber(client) as stub:
        stub.add_client_error("get_account", service_error_code="AccessDeniedException")
        with pytest.raises(RecoveryUnavailableError):
            SesRecoveryMailSender(
                "us-east-2", EMAIL, "https://web.example.com", client
            ).ensure_available()


ADMIN_MAILBOX = "academic-admin@example.com"


def install_academic_sender(
    flow: Flow,
    monkeypatch: pytest.MonkeyPatch,
    client: Any,
    admin_mailbox: str = ADMIN_MAILBOX,
) -> SqlUserRepository:
    monkeypatch.setattr(
        "identidad.infrastructure.ses_recovery_mail.boto3.client",
        lambda *args, **kwargs: client,
    )
    config = replace(
        flow.identity.extensions["identidad_config"],
        recovery_ses_region="us-east-2",
        recovery_email_sender=EMAIL,
        recovery_web_url="https://web.example.com/app",
        recovery_delivery_mode="academico_administrado",
        recovery_admin_mailbox=admin_mailbox,
    )
    configured = create_app(config, flow.clock)
    flow.identity.extensions["password_recovery_service"] = configured.extensions[
        "password_recovery_service"
    ]
    flow.identity.extensions["identidad_config"] = config
    repository: SqlUserRepository = configured.extensions["user_repository"]
    return repository


@pytest.mark.parametrize("admin_mailbox", [ADMIN_MAILBOX, EMAIL])
def test_academic_sandbox_routes_all_existing_accounts_only_to_verified_administrator(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, admin_mailbox: str
) -> None:
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    install_academic_sender(flow, monkeypatch, client, admin_mailbox)
    other_email = "other-academic@example.com"
    assert (
        flow.identity.test_client()
        .post(
            "/v1/usuarios",
            json={"nombre": "Other academic", "correo": other_email, "contrasena": OLD_PASSWORD},
        )
        .status_code
        == 201
    )
    messages: list[dict[str, Any]] = []

    def capture(params: dict[str, Any], **kwargs: Any) -> None:
        messages.append(params)

    client.meta.events.register("before-parameter-build.sesv2.SendEmail", capture)
    with Stubber(client) as stub:
        for _ in range(3):
            stub.add_response(
                "get_account", {"ProductionAccessEnabled": False, "SendingEnabled": True}
            )
            stub.add_response(
                "get_email_identity",
                {"VerifiedForSendingStatus": True},
                {"EmailIdentity": EMAIL},
            )
            if admin_mailbox != EMAIL:
                stub.add_response(
                    "get_email_identity",
                    {"VerifiedForSendingStatus": True},
                    {"EmailIdentity": admin_mailbox},
                )
            if _ < 2:
                stub.add_response("send_email", {"MessageId": "accepted-academic"})
        web = flow.web.test_client()
        responses = [
            web.post(BASE, json={"correo": email})
            for email in (EMAIL, other_email, "unknown-academic@example.com")
        ]
        assert all(response.status_code == 202 for response in responses)
        assert all(response.get_json() == responses[0].get_json() for response in responses)
        assert "el administrador recibira" in responses[0].get_json()["mensaje"]
        stub.assert_no_pending_responses()
    assert len(messages) == 2
    tokens = []
    for email, message in zip((EMAIL, other_email), messages, strict=True):
        assert message["Destination"] == {"ToAddresses": [admin_mailbox]}
        text = message["Content"]["Simple"]["Body"]["Text"]["Data"]
        assert f"Cuenta solicitante: {email}" in text
        match = re.search(r"#token=([A-Za-z0-9_-]{64})", text)
        assert match is not None
        tokens.append(match.group(1))
        assert match.group(1) not in json.dumps(responses[0].get_json())
    assert tokens[0] != tokens[1]
    rejected = web.post(
        BASE + "/confirmar", json={**confirm_body(flow, tokens[0]), "correo": other_email}
    )
    assert rejected.status_code == 400
    assert web.post(BASE + "/confirmar", json=confirm_body(flow, tokens[0])).status_code == 204
    with flow.sessions() as session:
        primary = session.scalar(select(UsuarioModel).where(UsuarioModel.correo == EMAIL))
        other = session.scalar(select(UsuarioModel).where(UsuarioModel.correo == other_email))
        assert primary is not None and verify_password(NEW_PASSWORD, primary.password_hash)
        assert other is not None and verify_password(OLD_PASSWORD, other.password_hash)
        pending = session.get(PasswordRecoveryModel, hashlib.sha256(tokens[1].encode()).hexdigest())
        assert pending is not None and pending.consumida_en is None


@pytest.mark.parametrize(
    "sending,sender_verified,admin_verified",
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_academic_transport_failure_precedes_any_account_lookup(
    flow: Flow,
    monkeypatch: pytest.MonkeyPatch,
    sending: bool,
    sender_verified: bool,
    admin_verified: bool,
) -> None:
    client = boto3.client(
        "sesv2",
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    repository = install_academic_sender(flow, monkeypatch, client)

    def forbid_lookup(email: str) -> None:
        raise AssertionError("Account lookup occurred before transport readiness")

    monkeypatch.setattr(repository, "find_credential", forbid_lookup)
    with Stubber(client) as stub:
        for _ in range(2):
            stub.add_response(
                "get_account", {"ProductionAccessEnabled": False, "SendingEnabled": sending}
            )
            stub.add_response(
                "get_email_identity",
                {"VerifiedForSendingStatus": sender_verified},
                {"EmailIdentity": EMAIL},
            )
            if sending and sender_verified:
                stub.add_response(
                    "get_email_identity",
                    {"VerifiedForSendingStatus": admin_verified},
                    {"EmailIdentity": ADMIN_MAILBOX},
                )
        responses = [
            flow.web.test_client().post(BASE, json={"correo": email})
            for email in (EMAIL, "unknown-academic@example.com")
        ]
        assert all(response.status_code == 503 for response in responses)
        assert all(
            response.get_json()["code"] == "servicio_no_disponible" for response in responses
        )
        stub.assert_no_pending_responses()
