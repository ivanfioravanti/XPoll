import json
import logging

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, open_poll, option_ids
from xpoll import db


def test_security_headers_on_every_response(client):
    for path in ("/", "/results", "/api/results", "/healthz", "/static/app.js", "/nope"):
        headers = client.get(path).headers
        csp = headers["content-security-policy"]
        assert "script-src 'self' https://challenges.cloudflare.com" in csp
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-eval" not in csp
        assert "unsafe-inline" not in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "camera=()" in headers["permissions-policy"]
        assert "strict-transport-security" not in headers


def test_hsts_only_in_production(make_app, settings):
    prod = settings.model_copy(
        update={
            "app_env": "production",
        }
    )
    app = make_app(settings=prod)
    response = TestClient(app, base_url=BASE_URL).get("/healthz")
    assert response.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://poll.example.org"},
        {},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_origin_writes_rejected(app, headers):
    client = TestClient(app, base_url=BASE_URL)
    response = client.post("/api/ballots", json={}, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "cross-origin request"


def test_sec_fetch_site_same_origin_accepted_without_origin(app):
    client = TestClient(app, base_url=BASE_URL)
    response = client.post("/api/ballots", json={}, headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code != 403 or response.json()["detail"] != "cross-origin request"


def test_oversized_declared_body_rejected(client):
    response = client.post(
        "/api/ballots", content=b"x" * 9000, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_oversized_streamed_body_rejected(client):
    def chunks():
        for _ in range(10):
            yield b"x" * 1000

    response = client.post(
        "/api/ballots", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_invalid_request_does_not_echo_input(client):
    response = client.post("/api/ballots", json={"option_ids": "SECRET-TOKEN-XYZ"})
    assert response.status_code == 422
    assert "SECRET-TOKEN-XYZ" not in response.text


def test_access_log_is_redacted(app, caplog):
    open_poll(app)
    client = TestClient(
        app,
        base_url=BASE_URL,
        headers={"Origin": BASE_URL, "CF-Connecting-IP": "198.51.100.77"},
    )
    caplog.set_level(logging.INFO, logger="xpoll.access")
    client.get("/")
    conn = db.connect(app.state.ctx.settings.database_path)
    ids = option_ids(conn, "alpha")
    conn.close()
    response = client.post(
        "/api/ballots?leak=QUERYSECRET",
        json={"option_ids": ids, "turnstile_token": "TOKEN-SHOULD-NOT-LOG"},
    )
    assert response.status_code == 201
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "xpoll.access"]
    assert {"method": "POST", "route": "/api/ballots", "status": 201}.items() <= lines[-1].items()
    assert response.headers["x-request-id"] == lines[-1]["request_id"]
    text = caplog.text
    for secret in ("198.51.100.77", "testclient", "TOKEN-SHOULD-NOT-LOG", "QUERYSECRET"):
        assert secret not in text
    assert "xpoll_voter" not in text


def test_access_log_records_unhandled_errors(app, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="xpoll.access")

    def boom(*args, **kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(app.state.ctx, "snapshot", boom)
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    assert client.get("/api/results").status_code == 500
    line = json.loads(caplog.records[-1].getMessage())
    assert line["error"] == "RuntimeError"
    assert line["status"] == 500
