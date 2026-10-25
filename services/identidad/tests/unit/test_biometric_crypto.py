import json
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from identidad.application.biometric_service import signing_message
from identidad.domain.biometric import BiometricError
from identidad.infrastructure.biometric_crypto import P256BiometricCrypto, b64u, decode_b64u


def test_public_interoperability_vector_uses_exact_issuer_and_der(private_key_pem: str) -> None:
    data = json.loads((Path(__file__).parents[1] / "fixtures" / "biometric-v1.json").read_text())
    crypto = P256BiometricCrypto(private_key_pem, data["issuer"], "solventa-api")
    public = crypto.public_key(data["clavePublica"])
    args = (
        data["proposito"],
        data["desafioId"],
        UUID(data["credencialId"]),
        UUID(data["usuarioId"]),
        public,
        data["expEpoch"],
    )
    message = signing_message(data["issuer"], *args)
    assert message == data["mensajeTexto"].encode() == decode_b64u(data["mensaje"], 512)
    crypto.verify(public, message, data["firma"])
    assert not message.endswith(b"\n")
    for changed in [
        signing_message(data["issuer"] + "/", *args),
        message + b"\n",
        message.replace(b"login", b"enrolar"),
        message.replace(b"\n", b"\r\n"),
    ]:
        with pytest.raises(BiometricError):
            crypto.verify(public, changed, data["firma"])


@pytest.mark.parametrize("encoded", ["a", "====", "ab=", "AB", "a b", "a\nb", "á", "A" * 300])
def test_base64_requires_canonical_encoding(encoded: str) -> None:
    with pytest.raises(BiometricError, match="solicitud_invalida"):
        decode_b64u(encoded, 91)


def test_wrong_curve_and_trailing_bytes_are_rejected(private_key_pem: str) -> None:
    crypto = P256BiometricCrypto(private_key_pem, "https://identidad.solventa.test", "solventa-api")
    for curve, suffix in [(ec.SECP384R1(), b""), (ec.SECP256R1(), b"\x00")]:
        public = (
            ec.generate_private_key(curve)
            .public_key()
            .public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        with pytest.raises(BiometricError):
            crypto.public_key(b64u(public + suffix))
