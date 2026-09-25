import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, FakeVerifier, open_poll, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig
from xpoll.services.suggestions import SuggestionRejected, validate_suggestion

HOSTS = ("github.com",)


def suggest(client, **fields):
    payload = {"name": "New Engine", "turnstile_token": "tok", **fields}
    return client.post("/api/suggestions", json=payload)


def stored(app):
    conn = db.connect(app.state.ctx.settings.database_path)
    try:
        return conn.execute("SELECT * FROM suggestions").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("name", "url", "notes"),
    [
        ("  Ok  ", None, ""),
        ("Ab", "https://github.com/a/b", "why\nbecause"),
        ("x" * 80, "", "n" * 500),
        ("Name", "https://GitHub.com/a/b", ""),
    ],
)
def test_valid_suggestions(name, url, notes):
    clean_name, clean_url, _ = validate_suggestion(name, url, notes, allowed_hosts=HOSTS)
    assert clean_name == name.strip()
    assert clean_url == ((url or "").strip() or None)


@pytest.mark.parametrize(
    ("name", "url", "notes", "code"),
    [
        ("a", None, "", "invalid-name"),
        ("x" * 81, None, "", "invalid-name"),
        ("bad\x00name", None, "", "invalid-name"),
        ("Name", "http://github.com/a/b", "", "invalid-url"),
        ("Name", "https://gitlab.com/a/b", "", "invalid-url"),
        ("Name", "https://github.com.evil.io/a", "", "invalid-url"),
        ("Name", "https://user:pw@github.com/a", "", "invalid-url"),
        ("Name", "https://github.com:8443/a", "", "invalid-url"),
        ("Name", "https://github.com/" + "a" * 300, "", "invalid-url"),
        ("Name", "javascript:alert(1)", "", "invalid-url"),
        ("Name", None, "n" * 501, "invalid-notes"),
        ("Name", None, "bad‮text", "invalid-notes"),
    ],
)
def test_invalid_suggestions(name, url, notes, code):
    with pytest.raises(SuggestionRejected) as info:
        validate_suggestion(name, url, notes, allowed_hosts=HOSTS)
    assert info.value.code == code


def test_api_stores_suggestion_privately(app, client, verifier):
    open_poll(app)
    response = suggest(client, url="https://github.com/a/b", notes="=cmd")
    assert response.status_code == 201
    rows = stored(app)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["network_hash"] != "testclient"
    assert verifier.calls[-1] == ("tok", "suggest")
    assert "New Engine" not in client.get("/results").text
    assert "New Engine" not in client.get("/api/results").text


def test_api_validation_error(app, client):
    open_poll(app)
    response = suggest(client, url="https://evil.example/x")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid-url"


def test_api_requires_open_or_scheduled_poll(app, client):
    assert suggest(client).status_code == 403


def test_api_turnstile_failure(make_app):
    app = make_app(verifier=FakeVerifier(ok=False))
    open_poll(app)
    client = TestClient(app, base_url=BASE_URL, headers={"Origin": BASE_URL})
    assert suggest(client).status_code == 403
    assert stored(app) == []


def test_api_rate_limited_after_five_per_hour(app, client):
    open_poll(app)
    statuses = [suggest(client, name=f"Engine {i}").status_code for i in range(6)]
    assert statuses == [201] * 5 + [429]
    assert suggest(client).headers["retry-after"].isdigit()


def test_disabled_suggestions(make_app):
    data = poll_data()
    data["suggestions"] = {"enabled": False}
    app = make_app(PollConfig.model_validate(data))
    open_poll(app)
    client = TestClient(app, base_url=BASE_URL, headers={"Origin": BASE_URL})
    assert suggest(client).status_code == 404
    assert "Suggest a missing tool" not in client.get("/").text
