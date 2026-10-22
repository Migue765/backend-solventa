import base64
import hashlib
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from flask.testing import FlaskClient

ISSUER = "https://api.solventa.test"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_openid_configuration_matches_the_contract(client: FlaskClient) -> None:
    response = client.get("/.well-known/openid-configuration")

    assert response.status_code == 200
    assert response.get_json() == {
        "issuer": ISSUER,
        "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
        "token_endpoint": f"{ISSUER}/oauth/token",
        "response_types_supported": ["token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["cliente", "asesor"],
    }
    assert response.headers["Cache-Control"] == "public, max-age=300"


def test_jwks_publishes_the_signing_key(client: FlaskClient, private_key_pem: str) -> None:
    response = client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=300"
    keys: list[dict[str, Any]] = response.get_json()["keys"]
    assert len(keys) == 1
    jwk = keys[0]

    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    public_key = cast(RSAPublicKey, private_key.public_key())
    numbers = public_key.public_numbers()
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert jwk["kty"] == "RSA" and jwk["use"] == "sig" and jwk["alg"] == "RS256"
    assert jwk["kid"] == b64url(hashlib.sha256(der).digest())
    assert jwk["n"] == b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
    assert jwk["e"] == "AQAB"
    assert "d" not in jwk and "p" not in jwk


def test_discovery_endpoints_need_no_authentication(client: FlaskClient) -> None:
    for path in ("/.well-known/openid-configuration", "/.well-known/jwks.json", "/ping"):
        assert client.get(path).status_code == 200
