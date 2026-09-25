import hashlib
import hmac
import ipaddress
import secrets

from itsdangerous import BadSignature, Signer
from starlette.requests import Request
from starlette.responses import Response

VOTER_COOKIE = "xpoll_voter"
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetime at 400 days


class Identity:
    def __init__(self, secret: str, poll_slug: str, *, trust_cloudflare: bool, secure: bool):
        self._secret = secret.encode()
        self._slug = poll_slug
        self._signer = Signer(secret, salt="xpoll-voter-cookie")
        self._trust_cloudflare = trust_cloudflare
        self._secure = secure

    def _hmac(self, purpose: str, value: str) -> str:
        message = f"{purpose}:{self._slug}:{value}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def voter_id(self, request: Request) -> str | None:
        raw = request.cookies.get(VOTER_COOKIE)
        if not raw:
            return None
        try:
            return self._signer.unsign(raw).decode()
        except BadSignature:
            return None

    def ensure_cookie(self, request: Request, response: Response) -> str:
        existing = self.voter_id(request)
        if existing:
            return existing
        voter_id = secrets.token_urlsafe(32)
        response.set_cookie(
            VOTER_COOKIE,
            self._signer.sign(voter_id).decode(),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=self._secure,
            samesite="lax",
            path="/",
        )
        return voter_id

    def voter_hash(self, voter_id: str) -> str:
        return self._hmac("voter", voter_id)

    def client_ip(self, request: Request) -> str:
        if self._trust_cloudflare:
            forwarded = request.headers.get("cf-connecting-ip")
            if forwarded:
                return forwarded.strip()
        return request.client.host if request.client else ""

    def network_hash(self, ip: str) -> str:
        return self._hmac("network", network_prefix(ip))


def network_prefix(ip: str) -> str:
    """IPv4 is kept whole; IPv6 is reduced to its /64 so address rotation doesn't evade limits."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)
