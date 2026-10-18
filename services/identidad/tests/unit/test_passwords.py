from identidad.infrastructure.passwords import hash_password, verify_password


def test_password_hash_can_be_verified() -> None:
    encoded_hash = hash_password("Solventa123!", salt=b"0123456789abcdef")

    assert verify_password("Solventa123!", encoded_hash)
    assert not verify_password("incorrecta", encoded_hash)


def test_invalid_password_hash_is_rejected() -> None:
    assert not verify_password("Solventa123!", "invalid")
