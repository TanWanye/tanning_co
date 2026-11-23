"""Security helpers: password hashing, session tokens and simple rate limiting.

Uses only the Python standard library so no extra packages are needed.
"""
import hashlib
import hmac
import secrets
import threading
import time

# scrypt parameters (OWASP-recommended minimums)
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_secret(secret: str) -> str:
    """Hash a password or PIN. Returns 'scrypt$salt$hash' (hex)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode("utf-8"), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    if not stored or not secret:
        return False
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(secret.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    """A random, unguessable session token."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only the hash of a token is stored, so a leaked database can't be used to log in."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RateLimiter:
    """Counts failures per key and locks the key out for a while after too many.

    In-memory: resets if the server restarts, which is acceptable for a single
    small instance.
    """

    def __init__(self, max_failures: int, window_seconds: int, lockout_seconds: int):
        self.max_failures = max_failures
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._failures = {}   # key -> list of timestamps
        self._locked = {}     # key -> unlock time
        self._lock = threading.Lock()

    def is_locked(self, key: str) -> int:
        """Returns seconds remaining on a lockout, or 0."""
        with self._lock:
            until = self._locked.get(key, 0)
            now = time.time()
            if until > now:
                return int(until - now) + 1
            self._locked.pop(key, None)
            return 0

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.time()
            hits = [t for t in self._failures.get(key, []) if now - t < self.window]
            hits.append(now)
            self._failures[key] = hits
            if len(hits) >= self.max_failures:
                self._locked[key] = now + self.lockout
                self._failures.pop(key, None)

    def hit(self, key: str) -> bool:
        """Counts an attempt (success or not). Returns False if over the limit."""
        if self.is_locked(key):
            return False
        self.fail(key)
        return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked.pop(key, None)
