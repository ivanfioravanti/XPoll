import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.conftest import BASE_URL, open_poll, option_ids
from xpoll import db
from xpoll.config import Settings

PREFIX = "/mlxengines"


@pytest.fixture
def prefixed(make_app, settings):
    app = make_app(settings=settings.model_copy(update={"app_base_url": BASE_URL + PREFIX}))
    open_poll(app)
    return app


@pytest.fixture
def pclient(prefixed):
    with TestClient(prefixed, base_url=BASE_URL, headers={"Origin": BASE_URL}) as client:
        yield client


def test_base_path_derivation():
    assert Settings(_env_file=None).base_path == ""
    assert Settings(_env_file=None, app_base_url="https://x.org/").base_path == ""
    assert Settings(_env_file=None, app_base_url="https://x.org/mlx/").base_path == "/mlx"
    assert Settings(_env_file=None, app_base_url="https://x.org/a/b").base_path == "/a/b"


@pytest.mark.parametrize(
    "url",
    ["https://x.org/mlx?x=1", "https://x.org/mlx#top", "https://x.org/ml x", "https://x.org/<a>"],
)
def test_rejects_unsafe_base_paths(url):
    with pytest.raises(ValidationError, match="APP_BASE_URL path"):
        Settings(_env_file=None, app_base_url=url)


def test_pages_and_assets_live_under_prefix(pclient):
    response = pclient.get(f"{PREFIX}/")
    html = response.text
    assert response.status_code == 200
    assert f'data-base="{PREFIX}"' in html
    for link in ("/static/styles.css", "/static/app.js", "/theme.css", "/results", "/privacy"):
        assert f'"{PREFIX}{link}"' in html
    assert 'href="/"' not in html
    assert pclient.get(f"{PREFIX}/static/app.js").status_code == 200
    assert pclient.get(f"{PREFIX}/theme.css").status_code == 200
    assert pclient.get(f"{PREFIX}/results").status_code == 200


def test_nothing_is_served_outside_prefix(pclient):
    assert pclient.get("/").status_code == 404
    assert pclient.get("/results").status_code == 404
    assert pclient.get("/api/results").status_code == 404


def test_bare_prefix_redirects_relatively(pclient):
    response = pclient.get(PREFIX, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{PREFIX}/"


def test_health_at_root_and_prefix(pclient):
    assert pclient.get("/healthz").json() == {"status": "ok"}
    assert pclient.get(f"{PREFIX}/healthz").json() == {"status": "ok"}


def test_cookie_scoped_to_prefix(pclient):
    cookie = pclient.get(f"{PREFIX}/").headers["set-cookie"]
    assert f"Path={PREFIX}" in cookie


def test_full_vote_flow_under_prefix(prefixed, pclient):
    pclient.get(f"{PREFIX}/")
    conn = db.connect(prefixed.state.ctx.settings.database_path)
    alpha = option_ids(conn, "alpha")
    conn.close()
    body = {"option_ids": alpha, "turnstile_token": "t"}
    assert pclient.post(f"{PREFIX}/api/ballots", json=body).status_code == 201
    assert pclient.post(f"{PREFIX}/api/ballots", json=body).status_code == 409
    assert pclient.get(f"{PREFIX}/api/results").json()["total_ballots"] == 1
    assert "already voted" in pclient.get(f"{PREFIX}/").text


def test_security_middleware_covers_prefix(prefixed):
    client = TestClient(prefixed, base_url=BASE_URL, headers={"Origin": "https://evil.example"})
    response = client.post(f"{PREFIX}/api/ballots", json={})
    assert response.status_code == 403
    assert "frame-ancestors 'none'" in client.get(f"{PREFIX}/").headers["content-security-policy"]


def test_static_requests_logged_as_static(pclient, caplog):
    import json
    import logging

    caplog.set_level(logging.INFO, logger="xpoll.access")
    pclient.get(f"{PREFIX}/static/app.js")
    assert json.loads(caplog.records[-1].getMessage())["route"] == "/static/*"
