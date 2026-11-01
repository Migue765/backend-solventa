import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import fakeredis
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask
from identidad.infrastructure.persistence.models import (
    BiometricAuditModel,
    BiometricChallengeModel,
    BiometricCredentialModel,
    RefreshTokenModel,
    UsuarioModel,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

PASSWORD = "Solventa123!"


def b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def key_public(key: ec.EllipticCurvePrivateKey) -> str:
    return b64u(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def proof(challenge: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    message = base64.urlsafe_b64decode(challenge["mensaje"] + "==")
    return {
        "credencialId": challenge["credencialId"],
        "desafioId": challenge["desafioId"],
        "firma": b64u(key.sign(message, ec.ECDSA(hashes.SHA256()))),
    }


def grant(payload: dict[str, str]) -> dict[str, str]:
    return {**payload, "grant_type": "biometric_signature", "client_id": "movil"}


def register_login(client: Any, email: str = "ana@solventa.co") -> dict[str, Any]:
    created = client.post(
        "/v1/usuarios",
        json={
            "nombre": "Ana",
            "correo": email,
            "contrasena": PASSWORD,
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": "movil",
            "username": email,
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200
    return cast(dict[str, Any], login.get_json())


@pytest.fixture
def biometric_app(make_app: Any, monkeypatch: pytest.MonkeyPatch) -> Flask:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *args, **kwargs: redis)
    return cast(
        Flask,
        make_app(biometric_login_enabled=True, redis_url="redis://synthetic", max_attempts=50),
    )


def enroll(client: Any, key: ec.EllipticCurvePrivateKey, tokens: dict[str, Any]) -> str:
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    response = client.post(
        "/v1/biometria/enrolamientos/desafio",
        headers=headers,
        json={
            "contrasena": PASSWORD,
            "clavePublica": key_public(key),
        },
    )
    assert response.status_code == 200, response.get_json()
    challenge = response.get_json()
    response = client.post(
        "/v1/biometria/enrolamientos", headers=headers, json=proof(challenge, key)
    )
    assert response.status_code == 201, response.get_json()
    assert "access_token" not in response.get_json()
    return cast(str, challenge["credencialId"])


def login_challenge(client: Any, credential_id: str) -> dict[str, Any]:
    response = client.post("/v1/biometria/desafios", json={"credencialId": credential_id})
    assert response.status_code == 200, response.get_json()
    return cast(dict[str, Any], response.get_json())


def test_enroll_login_rotate_logout_revoke_and_audit(
    biometric_app: Flask, database_url: str
) -> None:
    client = biometric_app.test_client()
    conventional = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, conventional)
    challenge = login_challenge(client, credential_id)
    signed = grant(proof(challenge, key))
    response = client.post("/oauth/token", json=signed)
    assert response.status_code == 200
    tokens = response.get_json()
    assert tokens["usuario"] == conventional["usuario"]
    assert tokens["expires_in"] == 3600
    assert tokens["refresh_expires_in"] == 30 * 86400
    assert response.headers["Cache-Control"] == "no-store"
    assert client.post("/oauth/token", json=signed).status_code == 401
    renewed = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "movil",
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert renewed.status_code == 200
    headers = {"Authorization": "Bearer " + conventional["access_token"]}
    rows = client.get("/v1/biometria/credenciales", headers=headers).get_json()["credenciales"]
    assert rows[0]["estado"] == "activa" and rows[0]["ultimoUsoEn"] is not None
    pending = proof(login_challenge(client, credential_id), key)
    assert (
        client.delete(f"/v1/biometria/credenciales/{credential_id}", headers=headers).status_code
        == 204
    )
    assert client.post("/oauth/token", json=grant(pending)).status_code == 401
    assert (
        client.post(
            "/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "movil",
                "refresh_token": renewed.get_json()["refresh_token"],
            },
        ).status_code
        == 401
    )
    # Conventional family remains usable.
    assert (
        client.post(
            "/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "movil",
                "refresh_token": conventional["refresh_token"],
            },
        ).status_code
        == 200
    )
    with Session(create_engine(database_url)) as session:
        audits = session.scalars(select(BiometricAuditModel)).all()
        assert {"enrolar", "login", "revocar"} <= {row.operacion for row in audits}
        assert {"permitido", "credenciales_invalidas"} <= {row.resultado for row in audits}
        assert all(row.correlation_id and row.creada_en for row in audits)
        bound = session.scalars(
            select(RefreshTokenModel).where(RefreshTokenModel.credencial_biometrica_id.is_not(None))
        ).all()
        assert len(bound) == 2 and all(row.revocado_en for row in bound)
        serialized = str([row.__dict__ for row in audits])
        assert PASSWORD not in serialized and "access_token" not in serialized
        assert challenge["desafioId"] not in serialized and "ana@" not in serialized


def test_enrollment_requires_password_same_subject_and_key_possession(
    biometric_app: Flask, database_url: str
) -> None:
    client = biometric_app.test_client()
    first = register_login(client)
    second = register_login(client, "otra@solventa.co")
    key = ec.generate_private_key(ec.SECP256R1())
    request_body = {"contrasena": PASSWORD, "clavePublica": key_public(key)}
    assert client.post("/v1/biometria/enrolamientos/desafio", json=request_body).status_code == 401
    headers = {"Authorization": "Bearer " + first["access_token"]}
    assert (
        client.post(
            "/v1/biometria/enrolamientos/desafio",
            headers=headers,
            json={**request_body, "contrasena": "incorrecta"},
        ).status_code
        == 401
    )
    challenge = client.post(
        "/v1/biometria/enrolamientos/desafio", headers=headers, json=request_body
    ).get_json()
    signed = proof(challenge, key)
    other_headers = {"Authorization": "Bearer " + second["access_token"]}
    assert (
        client.post("/v1/biometria/enrolamientos", headers=other_headers, json=signed).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/biometria/enrolamientos",
            headers=headers,
            json=proof(challenge, ec.generate_private_key(ec.SECP256R1())),
        ).status_code
        == 401
    )
    assert (
        client.post("/v1/biometria/enrolamientos", headers=headers, json=signed).status_code == 201
    )
    assert (
        client.post("/v1/biometria/enrolamientos", headers=headers, json=signed).status_code == 401
    )
    duplicate = client.post(
        "/v1/biometria/enrolamientos/desafio", headers=headers, json=request_body
    ).get_json()
    assert (
        client.post(
            "/v1/biometria/enrolamientos", headers=headers, json=proof(duplicate, key)
        ).status_code
        == 409
    )
    # Deleting another user's credential neither discloses nor revokes it.
    assert (
        client.delete(
            "/v1/biometria/credenciales/" + challenge["credencialId"], headers=other_headers
        ).status_code
        == 204
    )
    assert client.get("/v1/biometria/credenciales", headers=other_headers).get_json() == {
        "credenciales": []
    }
    with Session(create_engine(database_url)) as session:
        row = session.scalar(select(BiometricCredentialModel))
        assert row is not None and row.revocada_en is None


@pytest.mark.parametrize("mutation", ["key", "challenge", "credential", "channel", "purpose"])
def test_invalid_proofs_never_create_sessions(
    biometric_app: Flask, database_url: str, mutation: str
) -> None:
    client = biometric_app.test_client()
    tokens = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, tokens)
    challenge = login_challenge(client, credential_id)
    if mutation == "key":
        payload = proof(challenge, ec.generate_private_key(ec.SECP256R1()))
    else:
        payload = proof(challenge, key)
    body = grant(payload)
    if mutation == "challenge":
        body["desafioId"] = "A" * 43
    if mutation == "credential":
        body["credencialId"] = str(uuid4())
    if mutation == "channel":
        body["client_id"] = "web"
    if mutation == "purpose":
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        return_response = client.post("/v1/biometria/enrolamientos", headers=headers, json=payload)
    else:
        return_response = client.post("/oauth/token", json=body)
    assert return_response.status_code in {400, 401}
    assert "access_token" not in return_response.get_json()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1


def test_expiration_boundary_and_concurrent_replay(
    biometric_app: Flask, clock: Any, database_url: str
) -> None:
    client = biometric_app.test_client()
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, register_login(client))
    expired = grant(proof(login_challenge(client, credential_id), key))
    clock.advance(seconds=60)
    assert client.post("/oauth/token", json=expired).status_code == 401
    payload = grant(proof(login_challenge(client, credential_id), key))

    def submit(_: int) -> int:
        with biometric_app.test_client() as concurrent_client:
            return concurrent_client.post("/oauth/token", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [200, 401]
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 2


def test_dependencies_fail_closed_and_quota_is_shared(
    make_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *args, **kwargs: redis)
    first = make_app(biometric_login_enabled=True, redis_url="redis://synthetic", max_attempts=2)
    tokens = register_login(first.test_client())
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    body = {
        "contrasena": PASSWORD,
        "clavePublica": key_public(ec.generate_private_key(ec.SECP256R1())),
    }
    assert (
        first.test_client()
        .post("/v1/biometria/enrolamientos/desafio", headers=headers, json=body)
        .status_code
        == 200
    )
    second = make_app(biometric_login_enabled=True, redis_url="redis://synthetic", max_attempts=2)
    assert (
        second.test_client()
        .post("/v1/biometria/enrolamientos/desafio", headers=headers, json=body)
        .status_code
        == 200
    )
    limited = first.test_client().post(
        "/v1/biometria/enrolamientos/desafio", headers=headers, json=body
    )
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    monkeypatch.setattr(redis, "eval", lambda *args: (_ for _ in ()).throw(RedisConnectionError()))
    assert (
        first.test_client()
        .post("/v1/biometria/enrolamientos/desafio", headers=headers, json=body)
        .status_code
        == 503
    )


def test_disabled_and_strict_requests(make_app: Any, biometric_app: Flask) -> None:
    assert (
        make_app()
        .test_client()
        .post("/v1/biometria/desafios", json={"credencialId": str(uuid4())})
        .status_code
        == 503
    )
    client = biometric_app.test_client()
    for payload in [
        {"credencialId": str(uuid4()), "biometriaOK": True},
        {"credencialId": 123},
        {"credencialId": str(uuid4()).upper()},
        [],
    ]:
        assert client.post("/v1/biometria/desafios", json=payload).status_code == 400
    assert (
        client.post(
            "/v1/biometria/desafios", data=" " * 8193, content_type="application/json"
        ).status_code
        == 400
    )


def test_audit_failure_rolls_back_session(
    biometric_app: Flask, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    client = biometric_app.test_client()
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, register_login(client))
    challenge = login_challenge(client, credential_id)
    repository = biometric_app.extensions["biometric_service"].repository

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("synthetic", {}, Exception("synthetic"))

    monkeypatch.setattr(repository, "_audit", fail)
    response = client.post("/oauth/token", json=grant(proof(challenge, key)))
    assert response.status_code == 503 and "access_token" not in response.get_json()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1
        row = session.get(BiometricChallengeModel, challenge["desafioId"])
        assert row is not None and row.consumida_en is None


@pytest.mark.parametrize("operation", ["enrolar", "login", "refresh"])
def test_expiration_is_rechecked_after_waiting_for_database_lock(
    biometric_app: Flask, database_url: str, clock: Any, operation: str
) -> None:
    client = biometric_app.test_client()
    conventional = register_login(client)
    headers = {"Authorization": "Bearer " + conventional["access_token"]}
    key = ec.generate_private_key(ec.SECP256R1())
    if operation == "enrolar":
        challenge = client.post(
            "/v1/biometria/enrolamientos/desafio",
            headers=headers,
            json={"contrasena": PASSWORD, "clavePublica": key_public(key)},
        ).get_json()
        endpoint, body = "/v1/biometria/enrolamientos", proof(challenge, key)
    else:
        credential_id = enroll(client, key, conventional)
        challenge = login_challenge(client, credential_id)
        endpoint, body = "/oauth/token", grant(proof(challenge, key))
        if operation == "refresh":
            refresh_token = client.post(endpoint, json=body).get_json()["refresh_token"]
            body = {
                "grant_type": "refresh_token",
                "client_id": "movil",
                "refresh_token": refresh_token,
            }
    repository = biometric_app.extensions["biometric_service"].repository
    engine = repository._sessions.kw["bind"]
    attempted = threading.Event()

    def before_update(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("UPDATE "):
            attempted.set()

    event.listen(engine, "before_cursor_execute", before_update)
    blocker_engine = create_engine(database_url)
    before = 2 if operation == "refresh" else 1

    def submit() -> int:
        with biometric_app.test_client() as concurrent:
            return concurrent.post(endpoint, headers=headers, json=body).status_code

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with blocker_engine.begin() as blocker:
                blocker.execute(
                    update(BiometricChallengeModel)
                    .where(BiometricChallengeModel.desafio_id == challenge["desafioId"])
                    .values(mensaje=BiometricChallengeModel.mensaje)
                )
                future = pool.submit(submit)
                assert attempted.wait(3), "No se alcanzo el punto de espera SQL"
                clock.advance(seconds=60 if operation != "refresh" else 30 * 86400)
            assert future.result(timeout=5) == 401
    finally:
        event.remove(engine, "before_cursor_execute", before_update)
        blocker_engine.dispose()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == before
        if operation != "refresh":
            success = session.scalar(
                select(func.count())
                .select_from(BiometricAuditModel)
                .where(
                    BiometricAuditModel.operacion == operation,
                    BiometricAuditModel.resultado == "permitido",
                )
            )
            assert success == 0


def test_role_change_denies_proof_management_and_bound_refresh(
    biometric_app: Flask, database_url: str
) -> None:
    client = biometric_app.test_client()
    conventional = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, conventional)
    challenge = login_challenge(client, credential_id)
    tokens = client.post("/oauth/token", json=grant(proof(challenge, key))).get_json()
    pending = grant(proof(login_challenge(client, credential_id), key))
    with Session(create_engine(database_url)) as session:
        session.execute(update(UsuarioModel).values(rol="asesor"))
        session.commit()
    assert client.post("/oauth/token", json=pending).status_code == 403
    assert (
        client.get(
            "/v1/biometria/credenciales",
            headers={"Authorization": "Bearer " + conventional["access_token"]},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "movil",
                "refresh_token": tokens["refresh_token"],
            },
        ).status_code
        == 401
    )


def test_signer_failure_does_not_create_session(
    biometric_app: Flask, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jwt

    client = biometric_app.test_client()
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, register_login(client))
    challenge = login_challenge(client, credential_id)

    def fail(*args: Any, **kwargs: Any) -> str:
        raise jwt.PyJWTError("synthetic")

    monkeypatch.setattr("identidad.infrastructure.jwt_issuer.jwt.encode", fail)
    response = client.post("/oauth/token", json=grant(proof(challenge, key)))
    assert response.status_code == 503 and "access_token" not in response.get_json()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1


def test_invalid_session_expired_and_wrong_channel_cannot_enroll(
    biometric_app: Flask,
) -> None:
    from datetime import timedelta

    from identidad.domain.auth import User

    client = biometric_app.test_client()
    conventional = register_login(client)
    user_json = conventional["usuario"]
    from uuid import UUID

    user = User(UUID(user_json["id"]), user_json["nombre"], user_json["correo"], "cliente")
    now = datetime.now(UTC)
    issuer = biometric_app.extensions["jwt_issuer"]
    for bearer in [
        "invalid",
        issuer.issue(user, "web", now, now + timedelta(hours=1)),
        issuer.issue(user, "movil", now - timedelta(hours=2), now - timedelta(hours=1)),
    ]:
        response = client.get(
            "/v1/biometria/credenciales", headers={"Authorization": "Bearer " + bearer}
        )
        assert response.status_code == 401
        assert response.get_json()["code"] == "sesion_invalida"


@pytest.mark.parametrize("operation", ["login", "refresh"])
def test_revocation_serializes_with_login_and_refresh(
    biometric_app: Flask, database_url: str, operation: str
) -> None:
    client = biometric_app.test_client()
    conventional = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, conventional)
    login = grant(proof(login_challenge(client, credential_id), key))
    if operation == "refresh":
        current = client.post("/oauth/token", json=login).get_json()
        payload = {
            "grant_type": "refresh_token",
            "client_id": "movil",
            "refresh_token": current["refresh_token"],
        }
    else:
        payload = login
    barrier = threading.Barrier(2)

    def authenticate() -> int:
        barrier.wait(timeout=3)
        with biometric_app.test_client() as concurrent:
            return concurrent.post("/oauth/token", json=payload).status_code

    def revoke_credential() -> int:
        barrier.wait(timeout=3)
        with biometric_app.test_client() as concurrent:
            return concurrent.delete(
                "/v1/biometria/credenciales/" + credential_id,
                headers={"Authorization": "Bearer " + conventional["access_token"]},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        authentication = pool.submit(authenticate)
        revocation = pool.submit(revoke_credential)
        assert revocation.result(timeout=5) == 204
        assert authentication.result(timeout=5) in {200, 401}
    with Session(create_engine(database_url)) as session:
        rows = session.scalars(
            select(RefreshTokenModel).where(RefreshTokenModel.credencial_biometrica_id.is_not(None))
        ).all()
        assert all(row.revocado_en is not None for row in rows)
    assert client.post("/oauth/token", json=payload).status_code == 401


def test_enrollment_rechecks_time_after_inserting_unique_key(
    biometric_app: Flask, database_url: str, clock: Any
) -> None:
    client = biometric_app.test_client()
    conventional = register_login(client)
    headers = {"Authorization": "Bearer " + conventional["access_token"]}
    key = ec.generate_private_key(ec.SECP256R1())
    challenge = client.post(
        "/v1/biometria/enrolamientos/desafio",
        headers=headers,
        json={"contrasena": PASSWORD, "clavePublica": key_public(key)},
    ).get_json()
    repository = biometric_app.extensions["biometric_service"].repository
    engine = repository._sessions.kw["bind"]

    def delayed_insert(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO credenciales_biometricas"):
            clock.advance(seconds=61)

    event.listen(engine, "after_cursor_execute", delayed_insert)
    try:
        response = client.post(
            "/v1/biometria/enrolamientos", headers=headers, json=proof(challenge, key)
        )
        assert response.status_code == 401
    finally:
        event.remove(engine, "after_cursor_execute", delayed_insert)
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(BiometricCredentialModel)) == 0
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1
        row = session.get(BiometricChallengeModel, challenge["desafioId"])
        assert row is not None and row.consumida_en is None


def test_biometric_session_has_same_lifetime_and_claims_as_conventional(
    biometric_app: Flask,
) -> None:
    import jwt

    client = biometric_app.test_client()
    conventional = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, conventional)
    biometric = client.post(
        "/oauth/token", json=grant(proof(login_challenge(client, credential_id), key))
    ).get_json()
    for field in ("token_type", "expires_in", "refresh_expires_in", "scope"):
        assert biometric[field] == conventional[field]
    assert biometric["usuario"] == conventional["usuario"]
    claims = jwt.decode(biometric["access_token"], options={"verify_signature": False})
    expected = jwt.decode(conventional["access_token"], options={"verify_signature": False})
    for claim in ("iss", "aud", "sub", "scope", "rol", "client_id"):
        assert claims[claim] == expected[claim]
    assert claims["exp"] - claims["iat"] == expected["exp"] - expected["iat"]


@pytest.mark.parametrize("field", ["selfie", "plantilla", "template", "imagen", "livenessOK"])
def test_endpoints_reject_selfies_templates_and_client_verdicts(
    biometric_app: Flask, database_url: str, field: str
) -> None:
    client = biometric_app.test_client()
    tokens = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, tokens)
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    sensitive = {field: "dGVzdA"}
    enrollment = client.post(
        "/v1/biometria/enrolamientos/desafio",
        headers=headers,
        json={"contrasena": PASSWORD, "clavePublica": key_public(key), **sensitive},
    )
    challenge = login_challenge(client, credential_id)
    bodies = [
        client.post("/v1/biometria/desafios", json={"credencialId": credential_id, **sensitive}),
        client.post("/oauth/token", json={**grant(proof(challenge, key)), **sensitive}),
    ]
    for response in [enrollment, *bodies]:
        assert response.status_code == 400
        assert "access_token" not in response.get_json()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1
        row = session.get(BiometricChallengeModel, challenge["desafioId"])
        assert row is not None and row.consumida_en is None
        audits = session.scalars(select(BiometricAuditModel)).all()
        assert "dGVzdA" not in str([row.__dict__ for row in audits])
    # A clean retry still succeeds: a rejected attempt neither consumes the nonce nor locks out.
    assert client.post("/oauth/token", json=grant(proof(challenge, key))).status_code == 200


def test_failed_proof_is_audited_without_sensitive_data_and_allows_controlled_retry(
    biometric_app: Flask, database_url: str
) -> None:
    client = biometric_app.test_client()
    tokens = register_login(client)
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = enroll(client, key, tokens)
    challenge = login_challenge(client, credential_id)
    wrong = grant(proof(challenge, ec.generate_private_key(ec.SECP256R1())))
    response = client.post("/oauth/token", json=wrong)
    assert response.status_code == 401 and "access_token" not in response.get_json()
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(RefreshTokenModel)) == 1
        failures = session.scalars(
            select(BiometricAuditModel).where(
                BiometricAuditModel.operacion == "login",
                BiometricAuditModel.resultado == "credenciales_invalidas",
            )
        ).all()
        assert len(failures) == 1
        serialized = str([row.__dict__ for row in failures])
        assert wrong["firma"] not in serialized and challenge["desafioId"] not in serialized
    retry = client.post(
        "/oauth/token", json=grant(proof(login_challenge(client, credential_id), key))
    )
    assert retry.status_code == 200
