import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

import jwt
from jwt.algorithms import RSAAlgorithm

from bff_web.domain.auth import AccessClaims, User
from bff_web.domain.errors import IdentityUnavailableError, InvalidAccessTokenError

ALGORITHM = "RS256"
LEEWAY_SECONDS = 10
REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss", "aud"]

JwksFetcher = Callable[[], dict[str, Any]]


def http_jwks_fetcher(url: str, timeout_seconds: float = 5) -> JwksFetcher:
    def fetch() -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                document = json.loads(response.read())
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise IdentityUnavailableError() from error
        if not isinstance(document, dict):
            raise IdentityUnavailableError()
        return document

    return fetch


class JwksKeyStore:
    """Caché de claves públicas con recarga acotada ante un `kid` desconocido."""

    def __init__(
        self,
        fetch: JwksFetcher,
        ttl_seconds: float = 600,
        min_reload_interval_seconds: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._min_reload_interval = min_reload_interval_seconds
        self._monotonic = monotonic
        self._keys: dict[str, Any] = {}
        self._loaded_at: float | None = None
        self._lock = threading.Lock()

    def get(self, kid: str) -> Any:
        with self._lock:
            now = self._monotonic()
            if self._loaded_at is None or now - self._loaded_at >= self._ttl:
                self._reload(now)
            if kid not in self._keys and self._can_reload(now):
                self._reload(now)
            key = self._keys.get(kid)
        if key is None:
            raise InvalidAccessTokenError()
        return key

    def _can_reload(self, now: float) -> bool:
        return self._loaded_at is None or now - self._loaded_at >= self._min_reload_interval

    def _reload(self, now: float) -> None:
        document = self._fetch()
        entries = document.get("keys")
        if not isinstance(entries, list):
            raise IdentityUnavailableError()
        keys: dict[str, Any] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kty") != "RSA":
                continue
            if entry.get("alg", ALGORITHM) != ALGORITHM or not isinstance(entry.get("kid"), str):
                continue
            try:
                keys[entry["kid"]] = RSAAlgorithm.from_jwk(json.dumps(entry))
            except (jwt.PyJWTError, ValueError):
                continue
        self._keys = keys
        self._loaded_at = now


class JwtVerifier:
    def __init__(self, keys: JwksKeyStore, issuer: str, audience: str, client_id: str) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._client_id = client_id

    def verify(self, token: str) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise InvalidAccessTokenError() from error

        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str):
            raise InvalidAccessTokenError()

        key = self._keys.get(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
            if claims.get("client_id") != self._client_id:
                raise InvalidAccessTokenError()
            return AccessClaims(
                user=User(
                    UUID(claims["sub"]),
                    str(claims["nombre"]),
                    str(claims["correo"]),
                    str(claims["rol"]),
                ),
                client_id=self._client_id,
                scope=str(claims.get("scope", "")),
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise InvalidAccessTokenError() from error
