import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID, uuid4

import jwt
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import TokenSet, User
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError
from bff_web.domain.quote_errors import SimulatedPaymentNotFoundError
from bff_web.domain.quotes import (
    InsuranceProduct,
    InsuranceProductDetail,
    IssuanceSaga,
    OfferSelection,
    OffersResult,
    ProductCoverage,
    QuoteRequestDraft,
    QuoteRequestRecord,
    SimulatedPayment,
    Subscription,
    SubscriptionDecision,
    TravelDestination,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://identidad.solventa.test"
AUDIENCE = "solventa-api"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
USER = User(
    UUID("2923bf51-1899-4a3e-9547-b8ccb9c2c84f"), "Ana Cliente", "ana@solventa.co", "cliente"
)
PASSWORD = "Solventa123!"
ACCESS_TTL = 3600
REFRESH_TTL = 2_592_000


@dataclass
class KeyPair:
    kid: str
    private_key: rsa.RSAPrivateKey

    @property
    def private_pem(self) -> bytes:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    @property
    def public_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def jwk(self) -> dict[str, Any]:
        document = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        return {**document, "kid": self.kid, "use": "sig", "alg": "RS256"}


def make_keypair(kid: str) -> KeyPair:
    return KeyPair(kid, rsa.generate_private_key(public_exponent=65537, key_size=2048))


TokenFactory = Callable[..., str]


def sign_token(
    key: KeyPair,
    *,
    client_id: str = "web",
    expires_in: int = ACCESS_TTL,
    user: User = USER,
    algorithm: str = "RS256",
    kid: str | None = None,
    **overrides: Any,
) -> str:
    issued_at = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": str(user.user_id),
        "aud": AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + expires_in,
        "jti": str(uuid4()),
        "scope": "cotizaciones",
        "rol": user.role,
        "nombre": user.name,
        "correo": user.email,
        "client_id": client_id,
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key.private_pem, algorithm=algorithm, headers={"kid": kid or key.kid})


def forge_hs256_token(public_pem: bytes, kid: str) -> str:
    """Ataque de confusión de algoritmo: HS256 firmado con la clave pública como secreto."""

    def encode(data: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")

    issued_at = int(time.time())
    signing_input = (
        encode({"alg": "HS256", "typ": "JWT", "kid": kid})
        + b"."
        + encode(
            {
                "iss": ISSUER,
                "sub": str(USER.user_id),
                "aud": AUDIENCE,
                "iat": issued_at,
                "exp": issued_at + ACCESS_TTL,
                "client_id": "web",
                "nombre": USER.name,
                "correo": USER.email,
                "rol": USER.role,
            }
        )
    )
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def forge_unsigned_token(kid: str) -> str:
    def encode(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    issued_at = int(time.time())
    return (
        encode({"alg": "none", "typ": "JWT", "kid": kid})
        + "."
        + encode(
            {
                "iss": ISSUER,
                "sub": str(USER.user_id),
                "aud": AUDIENCE,
                "iat": issued_at,
                "exp": issued_at + ACCESS_TTL,
                "client_id": "web",
                "nombre": USER.name,
                "correo": USER.email,
                "rol": USER.role,
            }
        )
        + "."
    )


class ManualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeJwks:
    keys: list[KeyPair]
    calls: int = 0
    failing: bool = False

    def fetch(self) -> dict[str, Any]:
        self.calls += 1
        if self.failing:
            raise IdentityUnavailableError()
        return {"keys": [key.jwk() for key in self.keys]}


@dataclass
class FakeIdentity:
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    error: Exception | None = None

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error

    def _tokens(self) -> TokenSet:
        return TokenSet("access-jwt", "refresh-opaque", ACCESS_TTL, REFRESH_TTL, USER)

    def register(self, name: str, email: str, password: str, correlation_id: str) -> User:
        self._record("register", name, email, password, correlation_id)
        return User(USER.user_id, name, email.casefold(), "cliente")

    def login(self, email: str, password: str, correlation_id: str) -> TokenSet:
        self._record("login", email, password, correlation_id)
        return self._tokens()

    def refresh(self, refresh_token: str, correlation_id: str) -> TokenSet:
        self._record("refresh", refresh_token, correlation_id)
        return self._tokens()

    def revoke(self, refresh_token: str, correlation_id: str) -> None:
        self._record("revoke", refresh_token, correlation_id)


def rejected(status: int, code: str, retry_after: int | None = None) -> IdentityRejectedError:
    return IdentityRejectedError(status, code, retry_after)


class StubQuoteGateway(QuoteGateway):
    def __init__(self) -> None:
        self.record: QuoteRequestRecord | None = None
        self.subscription: Subscription | None = None
        self.user_ids: list[UUID] = []

    def list_products(self) -> tuple[InsuranceProduct, ...]:
        return (
            InsuranceProduct(
                "automotive",
                "Seguro Automotriz Total",
                "Protección para el vehículo",
                True,
                ("Daños al vehículo", "Responsabilidad civil"),
            ),
        )

    def get_product(self, product_id: str) -> InsuranceProductDetail:
        return InsuranceProductDetail(
            product_id=product_id,
            name="Seguro Automotriz Total",
            description="Protección para el vehículo",
            available=True,
            coverages=(
                ProductCoverage("Daños al vehículo", "Protección ante daños accidentales."),
                ProductCoverage("Responsabilidad civil", "Respaldo frente a daños a terceros."),
            ),
            conditions=("El vehículo debe cumplir las condiciones de asegurabilidad.",),
            insured_amount=Decimal("25000000"),
            deductible=Decimal("500000"),
            currency="COP",
            term="Anual con renovación",
        )

    def create_request(
        self, user_id: UUID, draft: QuoteRequestDraft, idempotency_key: UUID
    ) -> QuoteRequestRecord:
        self.user_ids.append(user_id)
        self.record = QuoteRequestRecord(
            "AQT-2026-ABCDEF1234", draft, "recibida", datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
        )
        return self.record

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord:
        self.user_ids.append(user_id)
        assert self.record is not None
        return self.record

    def list_travel_destinations(self) -> tuple[TravelDestination, ...]:
        return (TravelDestination("CO", "Colombia"),)

    def list_offers(self, user_id: UUID, request_id: str) -> OffersResult:
        raise NotImplementedError

    def get_selection(self, user_id: UUID, request_id: str) -> OfferSelection:
        raise NotImplementedError

    def select_offer(
        self,
        user_id: UUID,
        request_id: str,
        offer_id: UUID,
        version: int,
        idempotency_key: UUID,
    ) -> OfferSelection:
        raise NotImplementedError

    def start_subscription(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> Subscription:
        self.user_ids.append(user_id)
        now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
        self.subscription = Subscription(
            subscription_id=UUID("30000000-0000-4000-8000-000000000001"),
            request_id=request_id,
            offer_id=UUID("20000000-0000-4000-8000-000000000001"),
            offer_version=1,
            status="aprobada",
            rule_version="suscripcion-automatica-2026.1",
            created_at=now,
            updated_at=now,
            decision=SubscriptionDecision(
                result="aprobada",
                rule_version="suscripcion-automatica-2026.1",
                decision_hash="a" * 64,
                decided_at=now,
            ),
            saga=IssuanceSaga(status="pago_pendiente", version=1, updated_at=now),
        )
        return self.subscription

    def get_subscription(self, user_id: UUID, request_id: str) -> Subscription:
        self.user_ids.append(user_id)
        assert self.subscription is not None
        return self.subscription

    def confirm_simulated_payment(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> SimulatedPayment:
        raise NotImplementedError

    def get_simulated_payment(self, user_id: UUID, request_id: str) -> SimulatedPayment:
        self.user_ids.append(user_id)
        raise SimulatedPaymentNotFoundError


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


class IdentityServer:
    """Servidor HTTP local que simula a identidad con respuestas programables."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, bytes, dict[str, str], float]] = {}
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(raw) if raw else None,
                    }
                )
                status, body, headers, delay = outer.routes.get(
                    (self.command, self.path), (404, b"{}", {}, 0)
                )
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = _handle
            do_POST = _handle

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def respond(
        self,
        method: str,
        path: str,
        status: int,
        body: object = None,
        headers: dict[str, str] | None = None,
        delay: float = 0,
    ) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.routes[(method, path)] = (status, payload, headers or {}, delay)

    def respond_raw(self, method: str, path: str, status: int, body: bytes) -> None:
        self.routes[(method, path)] = (status, body, {}, 0)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def token_response(access_token: str = "access-jwt") -> dict[str, Any]:
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": "refresh-opaque",
        "refresh_expires_in": REFRESH_TTL,
        "scope": "cotizaciones",
        "usuario": {
            "id": str(USER.user_id),
            "nombre": USER.name,
            "correo": USER.email,
            "rol": USER.role,
        },
    }
