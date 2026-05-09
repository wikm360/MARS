"""
Authentication helpers for the MARS server.
Validates client and admin tokens, manages per-connection rate limiting.
"""
from __future__ import annotations
import time
from collections import defaultdict, deque


class RateLimiter:
    """Token-bucket rate limiter per connection ID."""

    def __init__(self, rate: int) -> None:
        self._rate = rate  # max calls per second
        self._timestamps: dict[str, deque] = defaultdict(deque)

    def allow(self, conn_id: str) -> bool:
        now = time.monotonic()
        ts = self._timestamps[conn_id]
        while ts and ts[0] < now - 1.0:
            ts.popleft()
        if len(ts) >= self._rate:
            return False
        ts.append(now)
        return True

    def remove(self, conn_id: str) -> None:
        self._timestamps.pop(conn_id, None)


def verify_client_token(token: str, secret: str) -> bool:
    """
    Clients authenticate with a token derived from the shared secret.
    Simple HMAC check keeps it stateless.
    """
    import hmac, hashlib
    expected = hmac.new(secret.encode(), b"client", hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)


def verify_admin_token(token: str, admin_token: str) -> bool:
    import hmac
    return hmac.compare_digest(token, admin_token)


def client_token_from_secret(secret: str) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode(), b"client", hashlib.sha256).hexdigest()
