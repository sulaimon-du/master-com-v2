from app.core.security import hash_password, hash_token, new_token, verify_password


def test_password_hash_is_argon2id_and_verifies() -> None:
    stored = hash_password("correct horse 9")
    assert stored.startswith("$argon2id$")
    assert "correct horse 9" not in stored
    assert verify_password(stored, "correct horse 9")
    assert not verify_password(stored, "wrong horse 9")


def test_unknown_account_is_refused_without_a_hash() -> None:
    assert verify_password(None, "anything at all") is False


def test_tokens_are_random_and_only_their_hash_is_stored() -> None:
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 43  # 256 bits, URL-safe base64
    assert hash_token(a) != a
    assert len(hash_token(a)) == 64
