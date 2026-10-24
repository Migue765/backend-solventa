import base64
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import fakeredis
import pytest
from flask import Flask
from identidad.infrastructure.encrypted_login import PREFIX
from jwcrypto import jwe, jwk
from redis.exceptions import ConnectionError

EMAIL = "ana@solventa.co"
PASSWORD = "Solventa123!"


@pytest.fixture(scope="session")
def encryption_pem() -> str:
    return str(
        jwk.JWK.generate(kty="RSA", size=2048)
        .export_to_pem(private_key=True, password=None)
        .decode()
    )


@pytest.fixture
def redis_client(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *args, **kwargs: client)
    return client


@pytest.fixture
def encrypted_app(
    make_app: Callable[..., Flask], encryption_pem: str, redis_client: fakeredis.FakeRedis
) -> Flask:
    app = make_app(
        encrypted_login_enabled=True,
        login_encryption_private_key=encryption_pem,
        redis_url="redis://unused.test",
    )
    response = app.test_client().post(
        "/v1/usuarios", json={"nombre": "Ana", "correo": EMAIL, "contrasena": PASSWORD}
    )
    assert response.status_code == 201
    return app


def challenge(app: Flask) -> dict[str, Any]:
    response = app.test_client().get("/oauth/login-challenge")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result: dict[str, Any] = response.get_json()
    assert set(result) == {"desafioId", "clavePublica", "expiraEn"}
    assert set(result["clavePublica"]) == {"kty", "n", "e", "alg", "use", "kid"}
    assert result["expiraEn"].endswith("Z")
    return result


def encrypt(
    issued: dict[str, Any],
    password: str = PASSWORD,
    email: str = EMAIL,
    plaintext: str | None = None,
    **headers: str,
) -> str:
    header = {
        "alg": "RSA-OAEP-256",
        "enc": "A256GCM",
        "kid": issued["clavePublica"]["kid"],
        "typ": "solventa-login+jwe",
        **headers,
    }
    body = plaintext or json.dumps(
        {"desafioId": issued["desafioId"], "correo": email, "contrasena": password}
    )
    envelope = jwe.JWE(body.encode(), protected=json.dumps(header))
    envelope.add_recipient(jwk.JWK(**issued["clavePublica"]))
    return str(envelope.serialize(compact=True))


def body(compact: str, **overrides: object) -> dict[str, object]:
    return {
        "grant_type": "encrypted_password",
        "client_id": "web",
        "username": EMAIL,
        "credencialCifrada": compact,
        **overrides,
    }


def test_valid_login_replay_refresh_and_revoke(encrypted_app: Flask) -> None:
    client = encrypted_app.test_client()
    compact = encrypt(challenge(encrypted_app))
    response = client.post("/oauth/token", json=body(compact))
    assert response.status_code == 200
    assert response.get_json()["usuario"]["correo"] == EMAIL
    assert client.post("/oauth/token", json=body(compact)).status_code == 400
    refreshed = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "web",
            "refresh_token": response.get_json()["refresh_token"],
        },
    )
    assert refreshed.status_code == 200
    assert (
        client.post(
            "/oauth/revoke", json={"refresh_token": refreshed.get_json()["refresh_token"]}
        ).status_code
        == 204
    )


def test_partner_encrypted_login_keeps_role_and_organizations(encrypted_app: Flask) -> None:
    result = encrypted_app.test_cli_runner().invoke(
        args=[
            "create-partner-user",
            "--name",
            "Socio sintético",
            "--email",
            "socio@example.com",
            "--partner-id",
            "BANK-ENCRYPTED",
            "--partner-name",
            "Banco sintético",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    compact = encrypt(challenge(encrypted_app), email="socio@example.com")
    response = encrypted_app.test_client().post(
        "/oauth/token", json=body(compact, username="socio@example.com")
    )
    assert response.status_code == 200
    assert response.get_json()["usuario"]["rol"] == "socio"
    assert response.get_json()["usuario"]["socios"] == ["BANK-ENCRYPTED"]
    refreshed = encrypted_app.test_client().post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "web",
            "refresh_token": response.get_json()["refresh_token"],
        },
    )
    assert refreshed.status_code == 200
    assert refreshed.get_json()["usuario"]["rol"] == "socio"
    assert refreshed.get_json()["usuario"]["socios"] == ["BANK-ENCRYPTED"]


def test_wrong_password_consumes_challenge(encrypted_app: Flask) -> None:
    issued = challenge(encrypted_app)
    client = encrypted_app.test_client()
    response = client.post("/oauth/token", json=body(encrypt(issued, password="wrong")))
    assert response.status_code == 401
    assert response.get_json()["code"] == "credenciales_invalidas"
    assert client.post("/oauth/token", json=body(encrypt(issued))).status_code == 400


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("alg", "RSA1_5"),
        ("alg", "dir"),
        ("enc", "A128GCM"),
        ("kid", "another-key"),
        ("typ", "JWT"),
        ("zip", "DEF"),
        ("jku", "https://untrusted.invalid/key"),
    ],
)
def test_strict_header_allowlist(
    encrypted_app: Flask, name: str, value: str, redis_client: fakeredis.FakeRedis
) -> None:
    issued = challenge(encrypted_app)
    parts = encrypt(issued).split(".")
    protected = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    protected[name] = value
    parts[0] = base64.urlsafe_b64encode(json.dumps(protected).encode()).rstrip(b"=").decode()
    response = encrypted_app.test_client().post("/oauth/token", json=body(".".join(parts)))
    assert response.status_code == 400
    assert redis_client.exists(PREFIX + issued["desafioId"])


@pytest.mark.parametrize("compact", ["not-jwe", "a.b.c.d.e", "", "x" * 8193])
def test_malformed(encrypted_app: Flask, compact: str) -> None:
    response = encrypted_app.test_client().post("/oauth/token", json=body(compact))
    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"


def test_tampering_does_not_consume(encrypted_app: Flask) -> None:
    issued = challenge(encrypted_app)
    compact = encrypt(issued)
    parts = compact.split(".")
    parts[3] = ("A" if parts[3][0] != "A" else "B") + parts[3][1:]
    client = encrypted_app.test_client()
    assert client.post("/oauth/token", json=body(".".join(parts))).status_code == 400
    assert client.post("/oauth/token", json=body(compact)).status_code == 200


@pytest.mark.parametrize("mutation", ["missing_typ", "duplicate_alg", "wrong_private_key"])
def test_invalid_protection_does_not_consume(encrypted_app: Flask, mutation: str) -> None:
    issued = challenge(encrypted_app)
    original_public = dict(issued["clavePublica"])
    compact = encrypt(issued)
    if mutation == "wrong_private_key":
        wrong_key = jwk.JWK.generate(kty="RSA", size=2048)
        issued["clavePublica"].update(json.loads(wrong_key.export_public()))
        compact = encrypt(issued)
    else:
        parts = compact.split(".")
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        if mutation == "missing_typ":
            header.pop("typ")
            protected = json.dumps(header)
        else:
            protected = json.dumps(header)[:-1] + ',"alg":"RSA-OAEP-256"}'
        parts[0] = base64.urlsafe_b64encode(protected.encode()).rstrip(b"=").decode()
        compact = ".".join(parts)
    client = encrypted_app.test_client()
    assert client.post("/oauth/token", json=body(compact)).status_code == 400
    issued["clavePublica"] = original_public
    assert client.post("/oauth/token", json=body(encrypt(issued))).status_code == 200


def test_email_binding_does_not_consume(encrypted_app: Flask) -> None:
    issued = challenge(encrypted_app)
    client = encrypted_app.test_client()
    assert (
        client.post(
            "/oauth/token", json=body(encrypt(issued), username="other@solventa.co")
        ).status_code
        == 400
    )
    assert client.post("/oauth/token", json=body(encrypt(issued))).status_code == 200


@pytest.mark.parametrize(
    "plaintext",
    [
        "{}",
        "[]",
        '{"desafioId":"x","correo":"ana@solventa.co","contrasena":"x"}',
        '{"desafioId":"x","desafioId":"y","correo":"ana@solventa.co","contrasena":"x"}',
        "x" * 1025,
    ],
)
def test_invalid_envelope_does_not_consume(
    encrypted_app: Flask, redis_client: fakeredis.FakeRedis, plaintext: str
) -> None:
    issued = challenge(encrypted_app)
    response = encrypted_app.test_client().post(
        "/oauth/token", json=body(encrypt(issued, plaintext=plaintext))
    )
    assert response.status_code == 400
    assert redis_client.exists(PREFIX + issued["desafioId"])


def test_unknown_challenge(encrypted_app: Flask) -> None:
    issued = challenge(encrypted_app)
    issued["desafioId"] = "a" * 43
    assert (
        encrypted_app.test_client().post("/oauth/token", json=body(encrypt(issued))).status_code
        == 400
    )


def test_expired(encrypted_app: Flask, clock: Any, redis_client: fakeredis.FakeRedis) -> None:
    issued = challenge(encrypted_app)
    assert 58 <= cast(int, redis_client.ttl(PREFIX + issued["desafioId"])) <= 60
    clock.advance(seconds=60)
    assert (
        encrypted_app.test_client().post("/oauth/token", json=body(encrypt(issued))).status_code
        == 400
    )


def test_duplicate_concurrent_attempt(encrypted_app: Flask) -> None:
    compact = encrypt(challenge(encrypted_app))

    def attempt(_: int) -> int:
        with encrypted_app.test_client() as client:
            return client.post("/oauth/token", json=body(compact)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 400]


def test_redis_unavailable(
    encrypted_app: Flask, redis_client: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    compact = encrypt(challenge(encrypted_app))

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("offline")

    monkeypatch.setattr(redis_client, "getdel", unavailable)
    assert encrypted_app.test_client().post("/oauth/token", json=body(compact)).status_code == 503
    monkeypatch.setattr(redis_client, "set", unavailable)
    response = encrypted_app.test_client().get("/oauth/login-challenge")
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


def test_rate_limit_preserved(encrypted_app: Flask) -> None:
    client = encrypted_app.test_client()
    for _ in range(3):
        issued = challenge(encrypted_app)
        assert (
            client.post("/oauth/token", json=body(encrypt(issued, password="bad"))).status_code
            == 401
        )
    compact = encrypt(challenge(encrypted_app))
    response = client.post("/oauth/token", json=body(compact))
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert client.post("/oauth/token", json=body(compact)).status_code == 400


def test_legacy_web_blocked_but_mobile_unchanged(encrypted_app: Flask) -> None:
    client = encrypted_app.test_client()
    legacy = {"grant_type": "password", "client_id": "web", "username": EMAIL, "password": PASSWORD}
    assert client.post("/oauth/token", json=legacy).status_code == 400
    assert client.post("/oauth/token", data=legacy).status_code == 400
    legacy["client_id"] = "movil"
    assert client.post("/oauth/token", json=legacy).status_code == 200


@pytest.mark.parametrize(
    "overrides", [{"client_id": "movil"}, {"password": PASSWORD}, {"extra": True}]
)
def test_strict_outer_fields(encrypted_app: Flask, overrides: dict[str, object]) -> None:
    compact = encrypt(challenge(encrypted_app))
    assert (
        encrypted_app.test_client()
        .post("/oauth/token", json=body(compact, **overrides))
        .status_code
        == 400
    )


def test_disabled_explicit_503(app: Flask) -> None:
    client = app.test_client()
    assert client.get("/oauth/login-challenge").status_code == 503
    assert client.post("/oauth/token", json=body("x")).status_code == 503


@pytest.mark.parametrize("key", ["", "invalid-key"])
def test_missing_or_bad_key_fails_startup(
    make_app: Callable[..., Flask], redis_client: fakeredis.FakeRedis, key: str
) -> None:
    with pytest.raises(ValueError):
        make_app(
            encrypted_login_enabled=True,
            redis_url="redis://unused",
            login_encryption_private_key=key,
        )


def test_shared_signing_key_rejected(
    make_app: Callable[..., Flask], private_key_pem: str, redis_client: fakeredis.FakeRedis
) -> None:
    with pytest.raises(ValueError):
        make_app(
            encrypted_login_enabled=True,
            redis_url="redis://unused",
            login_encryption_private_key=private_key_pem,
        )


def test_no_redis_fails_startup(make_app: Callable[..., Flask], encryption_pem: str) -> None:
    with pytest.raises(ValueError):
        make_app(encrypted_login_enabled=True, login_encryption_private_key=encryption_pem)


@pytest.mark.parametrize("kind", ["small_rsa", "public_only", "elliptic_curve"])
def test_invalid_key_type_fails_startup(
    make_app: Callable[..., Flask], redis_client: fakeredis.FakeRedis, kind: str
) -> None:
    if kind == "elliptic_curve":
        key = jwk.JWK.generate(kty="EC", crv="P-256")
    else:
        key = jwk.JWK.generate(kty="RSA", size=1024 if kind == "small_rsa" else 2048)
    pem = (
        key.export_to_pem()
        if kind == "public_only"
        else key.export_to_pem(private_key=True, password=None)
    ).decode()
    with pytest.raises(ValueError):
        make_app(
            encrypted_login_enabled=True,
            login_encryption_private_key=pem,
            redis_url="redis://unused",
        )


def test_bounded_body(encrypted_app: Flask) -> None:
    assert (
        encrypted_app.test_client().post("/oauth/token", json={"padding": "x" * 17000}).status_code
        == 400
    )
