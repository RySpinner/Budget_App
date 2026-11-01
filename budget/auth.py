"""Shared-password login (SPEC section 8: a simple shared password).

- The password is never stored, only a salted PBKDF2 hash (Python's standard
  library), kept in config/secrets.toml under [app].
- After logging in, the browser gets a signed "remember me" token valid for
  30 days. The signature uses a random secret (cookie_secret) and the current
  password hash, so changing the password logs everyone out.

Set or change the password (run on the Pi, or wherever the app runs):
    python -m budget.auth
It prints an [app] block to paste into config/secrets.toml.
"""

import getpass
import hashlib
import hmac
import secrets
import time

ITERATIONS = 600_000
TOKEN_DAYS = 30


def hash_password(password: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    """Return "pbkdf2_sha256$<iterations>$<salt>$<hash>"."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt, _ = stored_hash.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(hash_password(password, salt, int(iterations)), stored_hash)


def _signature(expires: int, cookie_secret: str, password_hash: str) -> str:
    message = f"{expires}|{password_hash}".encode()
    return hmac.new(cookie_secret.encode(), message, hashlib.sha256).hexdigest()


def make_token(cookie_secret: str, password_hash: str, days: int = TOKEN_DAYS, now: float | None = None) -> str:
    expires = int((now or time.time()) + days * 86400)
    return f"{expires}.{_signature(expires, cookie_secret, password_hash)}"


def token_is_valid(token: str | None, cookie_secret: str, password_hash: str, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    if not expires_text.isdigit() or int(expires_text) < (now or time.time()):
        return False
    return hmac.compare_digest(signature, _signature(int(expires_text), cookie_secret, password_hash))


def main() -> None:
    password = getpass.getpass("New shared password: ")
    if len(password) < 8:
        raise SystemExit("Use at least 8 characters.")
    if getpass.getpass("Repeat it: ") != password:
        raise SystemExit("The two entries didn't match.")
    print("\nPut this in config/secrets.toml (replacing any existing [app] block):\n")
    print("[app]")
    print(f'password_hash = "{hash_password(password)}"')
    print(f'cookie_secret = "{secrets.token_hex(32)}"')
    print("\nThen restart the app. Everyone will need to log in again.")


if __name__ == "__main__":
    main()
