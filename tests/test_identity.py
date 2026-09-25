import pytest
from starlette.requests import Request
from starlette.responses import Response

from xpoll.identity import VOTER_COOKIE, Identity, network_prefix


def request(headers=None, client=("203.0.113.9", 1234)):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": client})


def identity(**kw):
    defaults = {"trust_cloudflare": False, "secure": True}
    return Identity("s" * 40, "poll", **{**defaults, **kw})


def test_cookie_round_trip_and_flags():
    ident = identity()
    response = Response()
    voter_id = ident.ensure_cookie(request(), response)
    header = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in header
    value = header.split(";")[0].split("=", 1)[1]
    assert ident.voter_id(request({"cookie": f"{VOTER_COOKIE}={value}"})) == voter_id


def test_existing_cookie_is_reused():
    ident = identity()
    first = Response()
    voter_id = ident.ensure_cookie(request(), first)
    cookie = first.headers["set-cookie"].split(";")[0]
    second = Response()
    assert ident.ensure_cookie(request({"cookie": cookie}), second) == voter_id
    assert "set-cookie" not in second.headers


def test_tampered_cookie_rejected():
    assert identity().voter_id(request({"cookie": f"{VOTER_COOKIE}=abc.def"})) is None
    other = Identity("o" * 40, "poll", trust_cloudflare=False, secure=True)
    response = Response()
    other.ensure_cookie(request(), response)
    cookie = response.headers["set-cookie"].split(";")[0]
    assert identity().voter_id(request({"cookie": cookie})) is None


def test_insecure_cookie_in_development():
    response = Response()
    identity(secure=False).ensure_cookie(request(), response)
    assert "Secure" not in response.headers["set-cookie"]


def test_hashes_are_deterministic_and_scoped():
    a, b = identity(), Identity("s" * 40, "other-poll", trust_cloudflare=False, secure=True)
    assert a.voter_hash("v") == a.voter_hash("v")
    assert a.voter_hash("v") != b.voter_hash("v")
    assert a.voter_hash("v") != a.network_hash("v")
    assert "203.0.113.9" not in a.network_hash("203.0.113.9")


@pytest.mark.parametrize(
    ("ip", "prefix"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("garbage", "unknown"),
        ("", "unknown"),
    ],
)
def test_network_prefix(ip, prefix):
    assert network_prefix(ip) == prefix


def test_ipv6_rotation_within_64_shares_hash():
    ident = identity()
    assert ident.network_hash("2001:db8::1") == ident.network_hash("2001:db8::ffff")


def test_cloudflare_header_trusted_only_when_enabled():
    req = request({"cf-connecting-ip": "198.51.100.1"}, client=("127.0.0.1", 5))
    assert identity().client_ip(req) == "127.0.0.1"
    assert identity(trust_cloudflare=True).client_ip(req) == "198.51.100.1"
    assert identity(trust_cloudflare=True).client_ip(request()) == "203.0.113.9"
    assert identity().client_ip(request(client=None)) == ""
