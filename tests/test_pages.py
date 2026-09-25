from datetime import timedelta

from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, open_poll, option_ids, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig


def set_status(app, status):
    conn = db.connect(app.state.ctx.settings.database_path)
    db.set_status(conn, app.state.ctx.poll_id, status)
    conn.close()


def test_ballot_page_contract(app, client):
    open_poll(app)
    response = client.get("/")
    html = response.text
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "xpoll_voter=" in response.headers["set-cookie"]
    assert "<title>Test poll</title>" in html
    for name in ("Alpha", "beta", "Gamma", "Delta"):
        assert name in html
    assert "Old" not in html
    assert html.index("Alpha") < html.index("beta") < html.index("Gamma") < html.index("Delta")
    assert "/3 selected" in html
    assert html.count('<span class="slot"></span>') == 3
    assert "Pick up to 3" in html
    assert 'id="bar-hint"' in html
    assert 'data-min="1" data-max="3"' in html
    assert 'href="/privacy"' in html
    assert 'data-sitekey="1x00000000000000000000AA"' in html
    assert 'data-action="vote"' in html
    assert 'id="results-slot"' in html
    assert 'role="alert"' in html
    assert "checked" not in html
    assert "<script>" not in html
    assert " style=" not in html
    assert "Suggest a missing tool" in html
    assert 'rel="noopener noreferrer"' in html


def test_repo_links_are_outside_labels(app, client):
    open_poll(app)
    html = client.get("/").text
    label = html[html.index('<label for="option-') : html.index("</label>")]
    assert "href=" not in label


def test_draft_poll_page(client):
    html = client.get("/").text
    assert "not open yet" in html
    assert 'id="ballot"' not in html
    assert "Suggest a missing tool" not in html


def test_scheduled_poll_page(make_app, clock):
    data = poll_data(opens_at=(clock.now + timedelta(days=1)).isoformat())
    app = make_app(PollConfig.model_validate(data))
    open_poll(app)
    html = TestClient(app, base_url=BASE_URL).get("/").text
    assert "Voting opens" in html
    assert 'class="localtime"' in html
    assert "Suggest a missing tool" in html


def test_closed_poll_page_shows_final_results(app, client):
    set_status(app, "closed")
    html = client.get("/").text
    assert "Voting has closed" in html
    assert "Final results" in html
    assert 'data-live="false"' in html


def test_already_voted_page_shows_results(app, voter):
    open_poll(app)
    conn = db.connect(app.state.ctx.settings.database_path)
    alpha = option_ids(conn, "alpha")
    conn.close()
    voter.post("/api/ballots", json={"option_ids": alpha, "turnstile_token": "t"})
    html = voter.get("/").text
    assert "already voted" in html
    assert 'id="ballot"' not in html
    slot = html[html.index('id="results-slot"') :]
    assert slot.split(">", 1)[0].count("hidden") == 0


def test_results_page_contract(app, voter):
    open_poll(app)
    conn = db.connect(app.state.ctx.settings.database_path)
    ids = option_ids(conn, "alpha", "beta")
    conn.close()
    voter.post("/api/ballots", json={"option_ids": ids, "turnstile_token": "t"})
    html = voter.get("/results").text
    assert 'id="total-ballots">1<' in html
    assert 'id="refreshed-at"' in html
    assert '<progress class="result-bar" max="100" value="100.0"' in html
    assert '<span class="result-pct">100.0%</span>' in html
    assert '<span class="result-count">1 vote</span>' in html
    assert 'class="result is-leader"' in html
    assert "may total more than 100%" in html
    assert 'data-live="true"' in html
    assert "Cast your vote" not in html
    assert "Cast your vote" in TestClient(app, base_url=BASE_URL).get("/results").text


def test_privacy_page_discloses_required_items(client):
    html = client.get("/privacy").text
    for phrase in (
        "independent community survey",
        "xpoll_voter",
        "HMAC",
        "aggregate results",
        "30 days",
        "t@example.org",
        "Clearing cookies",
        "Cloudflare Turnstile",
    ):
        assert phrase in html


def test_theme_css_uses_configured_accent(make_app):
    app = make_app(PollConfig.model_validate(poll_data(accent_color="#ff5500")))
    response = TestClient(app, base_url=BASE_URL).get("/theme.css")
    assert response.headers["content-type"].startswith("text/css")
    assert "--accent: #ff5500" in response.text


def test_static_assets_served(client):
    assert 'rel="icon" href="/static/favicon.svg"' in client.get("/").text
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_user_content_is_escaped(make_app):
    data = poll_data(title="<script>alert(1)</script>")
    app = make_app(PollConfig.model_validate(data))
    html = TestClient(app, base_url=BASE_URL).get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_tied_options_share_a_rank(app, client):
    from tests.test_results import add_ballot

    conn = db.connect(app.state.ctx.settings.database_path)
    add_ballot(conn, app.state.ctx.poll_id, 1, "alpha", "beta")
    add_ballot(conn, app.state.ctx.poll_id, 2, "gamma")
    conn.close()
    app.state.ctx.results.invalidate()
    html = client.get("/results").text
    ranks = [chunk.split("<", 1)[0] for chunk in html.split('class="result-rank">')[1:]]
    assert ranks == ["1", "1", "1", "4"]
    assert html.count("is-leader") == 3
    assert html.count("is-zero") == 1


def test_nav_marks_current_page(client):
    assert '<a href="/results" aria-current="page">' in client.get("/results").text
    assert '<a href="/" aria-current="page">' in client.get("/").text


def test_theme_defaults_to_auto(client):
    html = client.get("/").text
    assert '<html lang="en" data-theme="auto">' in html
    assert '<meta name="color-scheme" content="light dark">' in html


def test_forced_dark_theme(make_app):
    app = make_app(PollConfig.model_validate(poll_data(theme="dark")))
    open_poll(app)
    html = TestClient(app, base_url=BASE_URL).get("/").text
    assert '<html lang="en" data-theme="dark">' in html
    assert '<meta name="color-scheme" content="dark">' in html
    assert 'data-action="vote" data-theme="dark"' in html
