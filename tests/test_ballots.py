import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, FakeVerifier, open_poll, option_ids, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig
from xpoll.services.ballots import BallotRejected, record_ballot, validate_choices


def ids(app, *slugs):
    conn = db.connect(app.state.ctx.settings.database_path)
    try:
        return option_ids(conn, *slugs)
    finally:
        conn.close()


def vote(client, option_ids, token="tok"):
    return client.post("/api/ballots", json={"option_ids": option_ids, "turnstile_token": token})


def count(app, table="ballots"):
    conn = db.connect(app.state.ctx.settings.database_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


@pytest.fixture
def opened(app):
    open_poll(app)
    return app


@pytest.mark.parametrize("n", [1, 3])
def test_valid_ballot_accepted(opened, voter, verifier, n):
    chosen = ids(opened, "alpha", "beta", "gamma")[:n]
    response = vote(voter, chosen)
    assert response.status_code == 201
    body = response.json()
    assert body["accepted_choices"] == n
    assert body["total_ballots"] == 1
    assert verifier.calls == [("tok", "vote")]
    assert count(opened, "ballot_choices") == n


@pytest.mark.parametrize(
    ("slugs", "code"),
    [
        ((), "choice-count"),
        (("alpha", "beta", "gamma", "delta"), "choice-count"),
        (("alpha", "alpha"), "duplicate-choice"),
        (("old",), "unknown-option"),
    ],
)
def test_invalid_choices_rejected(opened, voter, slugs, code):
    response = vote(voter, ids(opened, *slugs))
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert count(opened) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"option_ids": ["1"], "turnstile_token": "t"},
        {"option_ids": [1.5], "turnstile_token": "t"},
        {"option_ids": [True], "turnstile_token": "t"},
        {"option_ids": [1]},
        {"option_ids": [1], "turnstile_token": "t", "extra": 1},
        {"option_ids": 1, "turnstile_token": "t"},
    ],
)
def test_malformed_payload_rejected(opened, voter, payload):
    assert voter.post("/api/ballots", json=payload).status_code == 422


def test_unknown_option_id_rejected(opened, voter):
    assert vote(voter, [999_999]).json()["code"] == "unknown-option"


def test_repeat_voter_gets_409_without_changing_totals(opened, voter, verifier):
    assert vote(voter, ids(opened, "alpha")).status_code == 201
    response = vote(voter, ids(opened, "beta"))
    assert response.status_code == 409
    assert count(opened) == 1
    assert len(verifier.calls) == 1  # duplicate is rejected before spending a Turnstile check


def test_two_browsers_on_same_network_can_both_vote(opened):
    for _ in range(2):
        client = TestClient(opened, base_url=BASE_URL, headers={"Origin": BASE_URL})
        client.get("/")
        assert vote(client, ids(opened, "alpha")).status_code == 201
    assert count(opened) == 2


def test_missing_cookie_rejected(opened):
    client = TestClient(opened, base_url=BASE_URL, headers={"Origin": BASE_URL})
    response = vote(client, ids(opened, "alpha"))
    assert response.status_code == 400
    assert response.json()["code"] == "cookies-required"


def test_turnstile_failure_rejected(make_app):
    app = make_app(verifier=FakeVerifier(ok=False))
    open_poll(app)
    client = TestClient(app, base_url=BASE_URL, headers={"Origin": BASE_URL})
    client.get("/")
    response = vote(client, ids(app, "alpha"))
    assert response.status_code == 403
    assert response.json()["code"] == "verification-failed"
    assert count(app) == 0


def test_draft_poll_rejects_votes(app, voter):
    response = vote(voter, ids(app, "alpha"))
    assert response.status_code == 403
    assert response.json()["code"] == "poll-not-open"


def test_closed_poll_rejects_votes(opened, voter, clock):
    conn = db.connect(opened.state.ctx.settings.database_path)
    db.set_status(conn, opened.state.ctx.poll_id, "closed")
    conn.close()
    assert vote(voter, ids(opened, "alpha")).json()["code"] == "poll-closed"


def test_window_is_enforced(make_app, clock):
    data = poll_data(
        opens_at=(clock.now + timedelta(hours=1)).isoformat(),
        closes_at=(clock.now + timedelta(hours=2)).isoformat(),
    )
    app = make_app(PollConfig.model_validate(data))
    open_poll(app)
    client = TestClient(app, base_url=BASE_URL, headers={"Origin": BASE_URL})
    client.get("/")
    alpha = ids(app, "alpha")
    assert vote(client, alpha).json()["code"] == "poll-not-open"
    clock.now += timedelta(minutes=90)
    assert vote(client, alpha).status_code == 201
    clock.now += timedelta(hours=1)
    assert vote(client, alpha).json()["code"] == "poll-closed"


def test_rate_limit_counts_only_failed_attempts(opened, voter):
    alpha = ids(opened, "alpha")
    assert vote(voter, alpha).status_code == 201
    statuses = [vote(voter, alpha).status_code for _ in range(21)]
    assert statuses[:20] == [409] * 20
    assert statuses[20] == 429


def test_rate_limit_returns_retry_after(opened, voter):
    limiter = opened.state.ctx.ballot_limiter
    limiter.acquire = lambda key: 42
    response = vote(voter, ids(opened, "alpha"))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"


def test_successful_ballots_do_not_consume_rate_limit(opened):
    for _ in range(25):
        client = TestClient(opened, base_url=BASE_URL, headers={"Origin": BASE_URL})
        client.get("/")
        assert vote(client, ids(opened, "alpha")).status_code == 201


# --- service level ---------------------------------------------------------


def test_validate_choices_boundaries():
    assert validate_choices([1, 2], min_choices=1, max_choices=2, valid_ids=[1, 2]) == [1, 2]
    with pytest.raises(BallotRejected):
        validate_choices([1, 2, 3], min_choices=1, max_choices=2, valid_ids=[1, 2, 3])


def test_record_ballot_is_atomic(conn, poll_id, clock):
    with pytest.raises(sqlite3.IntegrityError):
        record_ballot(
            conn,
            poll_id=poll_id,
            option_ids=[*option_ids(conn, "alpha"), 999_999],
            voter_hash="v",
            network_hash="n",
            now=clock.now,
        )
    assert conn.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ballot_choices").fetchone()[0] == 0


class LockedConn:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.in_transaction = False

    def execute(self, sql, *args):
        if sql == "BEGIN IMMEDIATE" and self.fail_times:
            self.fail_times -= 1
            raise sqlite3.OperationalError("database is locked")
        return self

    def executemany(self, *args):
        return self

    def fetchone(self):
        return (7,)


def test_record_ballot_retries_contention(clock):
    sleeps = []
    _ballot_id, total = record_ballot(
        LockedConn(2),
        poll_id=1,
        option_ids=[1],
        voter_hash="v",
        network_hash="n",
        now=clock.now,
        sleep=sleeps.append,
    )
    assert total == 7
    assert sleeps == [0.05, 0.1]


def test_record_ballot_gives_up_with_503(clock):
    with pytest.raises(BallotRejected) as info:
        record_ballot(
            LockedConn(10),
            poll_id=1,
            option_ids=[1],
            voter_hash="v",
            network_hash="n",
            now=clock.now,
            sleep=lambda s: None,
        )
    assert info.value.status == 503


def test_record_ballot_reraises_other_operational_errors(clock):
    class Broken(LockedConn):
        def execute(self, sql, *args):
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError):
        record_ballot(
            Broken(0),
            poll_id=1,
            option_ids=[1],
            voter_hash="v",
            network_hash="n",
            now=clock.now,
        )
