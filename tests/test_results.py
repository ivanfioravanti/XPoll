import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, open_poll, option_ids, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig
from xpoll.services.results import ResultsCache, compute_results


def add_ballot(conn, poll_id, n, *slugs):
    conn.execute(
        "INSERT INTO ballots VALUES (?, ?, ?, 'n', ?)",
        (f"b{n}", poll_id, f"v{n}", f"2026-10-02T12:00:{n:02d}.000000Z"),
    )
    for option_id in option_ids(conn, *slugs):
        conn.execute("INSERT INTO ballot_choices VALUES (?, ?)", (f"b{n}", option_id))


def test_zero_ballots(conn, poll_id):
    data = compute_results(conn, poll_id, "test-poll")
    assert data["total_ballots"] == 0
    assert data["updated_at"] is None
    assert [r["name"] for r in data["results"]] == ["Alpha", "beta", "Delta", "Gamma"]
    assert all(r["percentage"] == 0.0 for r in data["results"])


def test_overlapping_selections_ties_and_percentages(conn, poll_id):
    add_ballot(conn, poll_id, 1, "alpha", "beta")
    add_ballot(conn, poll_id, 2, "gamma", "beta")
    add_ballot(conn, poll_id, 3, "gamma")
    data = compute_results(conn, poll_id, "test-poll")
    rows = [(r["name"], r["respondents"], r["percentage"]) for r in data["results"]]
    assert rows == [
        ("beta", 2, 66.7),
        ("Gamma", 2, 66.7),
        ("Alpha", 1, 33.3),
        ("Delta", 0, 0.0),
    ]
    assert data["total_ballots"] == 3
    assert data["updated_at"] == "2026-10-02T12:00:03.000000Z"


def test_inactive_option_with_votes_is_kept(conn, poll_id):
    add_ballot(conn, poll_id, 1, "old")
    names = [r["name"] for r in compute_results(conn, poll_id, "p")["results"]]
    assert "Old" in names
    assert (
        next(r for r in compute_results(conn, poll_id, "p")["results"] if r["name"] == "Old")[
            "active"
        ]
        is False
    )


def test_cache_serves_snapshot_until_ttl_or_invalidate():
    now = [0.0]
    cache = ResultsCache(ttl=2.0, clock=lambda: now[0])
    loads = []

    def load():
        loads.append(1)
        return {"n": len(loads)}

    first = cache.get(load)
    assert cache.get(load) is first
    now[0] = 2.5
    second = cache.get(load)
    assert second.payload == {"n": 2}
    assert second.etag != first.etag
    cache.invalidate()
    assert cache.get(load).payload == {"n": 3}


def test_api_results_with_etag(app, client):
    response = client.get("/api/results")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    etag = response.headers["etag"]
    assert etag.startswith('W/"')
    again = client.get("/api/results", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag


def test_vote_invalidates_cached_results(app, voter):
    open_poll(app)
    assert voter.get("/api/results").json()["total_ballots"] == 0
    conn = db.connect(app.state.ctx.settings.database_path)
    alpha = option_ids(conn, "alpha")
    conn.close()
    voter.post("/api/ballots", json={"option_ids": alpha, "turnstile_token": "t"})
    assert voter.get("/api/results").json()["total_ballots"] == 1


@pytest.fixture
def hidden_app(make_app):
    return make_app(PollConfig.model_validate(poll_data(results_visibility="after_close")))


def test_results_hidden_until_close(hidden_app):
    client = TestClient(hidden_app, base_url=BASE_URL)
    response = client.get("/api/results")
    assert response.status_code == 403
    assert response.json()["code"] == "results-hidden"
    assert "published when the poll closes" in client.get("/results").text
    conn = db.connect(hidden_app.state.ctx.settings.database_path)
    db.set_status(conn, hidden_app.state.ctx.poll_id, "closed")
    conn.close()
    assert client.get("/api/results").status_code == 200
    assert "Final results" in client.get("/results").text
