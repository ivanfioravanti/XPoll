import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_TOKEN_LENGTH = 2048

logger = logging.getLogger("xpoll.turnstile")


@dataclass(frozen=True)
class TurnstileResult:
    ok: bool
    reason: str


class TurnstileVerifier(Protocol):
    def verify(self, token: str, *, action: str) -> TurnstileResult: ...


class CloudflareTurnstile:
    """Server-side Siteverify. Every failure mode, including provider outages, fails closed."""

    def __init__(
        self,
        secret: str,
        *,
        expected_hostname: str,
        check_claims: bool = True,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ):
        self._secret = secret
        self._hostname = expected_hostname
        self._check_claims = check_claims
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout))

    def verify(self, token: str, *, action: str) -> TurnstileResult:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return TurnstileResult(False, "missing-token")
        try:
            response = self._client.post(
                SITEVERIFY_URL, data={"secret": self._secret, "response": token}
            )
        except httpx.HTTPError as exc:
            logger.warning("turnstile provider unavailable: %s", type(exc).__name__)
            return TurnstileResult(False, "provider-unavailable")
        if response.status_code != 200:
            logger.warning("turnstile provider status %s", response.status_code)
            return TurnstileResult(False, "provider-error")
        try:
            data = response.json()
        except ValueError:
            return TurnstileResult(False, "provider-error")
        if not isinstance(data, dict) or data.get("success") is not True:
            return TurnstileResult(False, "invalid-token")
        if self._check_claims:
            if data.get("hostname") != self._hostname:
                return TurnstileResult(False, "hostname-mismatch")
            if data.get("action") != action:
                return TurnstileResult(False, "action-mismatch")
        return TurnstileResult(True, "ok")
