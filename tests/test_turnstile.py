import httpx
import pytest

from xpoll.turnstile import SITEVERIFY_URL, CloudflareTurnstile


def verifier(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CloudflareTurnstile("secret", expected_hostname="poll.example.org", client=client, **kw)


def respond(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


GOOD = {"success": True, "hostname": "poll.example.org", "action": "vote"}


def test_success_posts_secret_and_token():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=GOOD)

    assert verifier(handler).verify("tok", action="vote").ok
    assert seen["url"] == SITEVERIFY_URL
    assert "secret=secret" in seen["body"]
    assert "response=tok" in seen["body"]


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (respond({"success": False, "error-codes": ["invalid-input-response"]}), "invalid-token"),
        (respond(["unexpected"]), "invalid-token"),
        (respond(GOOD, status=500), "provider-error"),
        (lambda r: httpx.Response(200, text="not json"), "provider-error"),
        (respond({**GOOD, "hostname": "evil.example"}), "hostname-mismatch"),
        (respond({**GOOD, "action": "suggest"}), "action-mismatch"),
    ],
)
def test_failures(handler, reason):
    result = verifier(handler).verify("tok", action="vote")
    assert not result.ok
    assert result.reason == reason


def test_timeout_fails_closed():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert verifier(handler).verify("tok", action="vote").reason == "provider-unavailable"


@pytest.mark.parametrize("token", ["", "x" * 5000])
def test_missing_or_oversized_token_is_not_sent(token):
    def handler(request):
        raise AssertionError("must not call provider")

    assert verifier(handler).verify(token, action="vote").reason == "missing-token"


def test_claims_skipped_for_test_keys():
    result = verifier(respond({"success": True, "hostname": "example.com"}), check_claims=False)
    assert result.verify("tok", action="vote").ok


def test_secret_never_logged(caplog):
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    verifier(handler).verify("tok-123", action="vote")
    assert "secret" not in caplog.text
    assert "tok-123" not in caplog.text
