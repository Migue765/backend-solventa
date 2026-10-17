import hashlib
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> str:
    password_salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        password_salt,
        iterations,
    )
    return f"{ALGORITHM}${iterations}${password_salt.hex()}${password_hash.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, expected_hash_hex = encoded_hash.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(expected_hash_hex)
    except (TypeError, ValueError):
        return False

    actual_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return secrets.compare_digest(actual_hash, expected_hash)
