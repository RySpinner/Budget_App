from budget.auth import hash_password, make_token, token_is_valid, verify_password


def test_password_hash_round_trip():
    stored = hash_password("correct horse", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_same_password_gets_different_salts():
    assert hash_password("x", iterations=1000) != hash_password("x", iterations=1000)


def test_malformed_hash_never_verifies():
    assert not verify_password("x", "")
    assert not verify_password("x", "md5$1$a$b")
    assert not verify_password("x", "garbage")


def test_token_valid_until_expiry():
    pw = hash_password("pw", iterations=1000)
    token = make_token("secret", pw, days=30, now=1_000_000)
    assert token_is_valid(token, "secret", pw, now=1_000_000 + 29 * 86400)
    assert not token_is_valid(token, "secret", pw, now=1_000_000 + 31 * 86400)


def test_token_rejected_after_password_or_secret_change():
    pw = hash_password("pw", iterations=1000)
    token = make_token("secret", pw)
    assert not token_is_valid(token, "secret", hash_password("new", iterations=1000))
    assert not token_is_valid(token, "other-secret", pw)


def test_tampered_or_missing_tokens_rejected():
    pw = hash_password("pw", iterations=1000)
    expires, sig = make_token("secret", pw).split(".")
    assert not token_is_valid(f"{int(expires) + 999999}.{sig}", "secret", pw)
    for bad in [None, "", "abc", "123", "x.y"]:
        assert not token_is_valid(bad, "secret", pw)
