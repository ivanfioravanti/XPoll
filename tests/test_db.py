import sqlite3
from datetime import timedelta

import pytest

from tests.conftest import NOW, option_ids, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig
from xpoll.services.polls import voting_state


def test_schema_and_pragmas(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "polls",
        "options",
        "ballots",
        "ballot_choices",
        "suggestions",
        "ballot_answers",
    } <= tables
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ballots_poll_created_idx", "choices_option_idx"} <= indexes
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db.schema_version(conn) == 2


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == 2
    assert db.schema_version(conn) == 2


def test_refuses_newer_schema(conn):
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(db.SchemaVersionError):
        db.migrate(conn)


def test_sync_creates_draft_poll_and_options(conn, poll_id):
    poll = db.get_poll(conn, poll_id)
    assert poll["status"] == "draft"
    assert (poll["min_choices"], poll["max_choices"]) == (1, 3)
    assert [o["slug"] for o in db.active_options(conn, poll_id)] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
    ]


def test_sync_is_idempotent_and_keeps_status(conn, poll_id, poll_config):
    db.set_status(conn, poll_id, "open")
    db.sync_poll(conn, poll_config, NOW)
    assert conn.execute("SELECT COUNT(*) FROM options").fetchone()[0] == 5
    assert db.get_poll(conn, poll_id)["status"] == "open"


def test_sync_deactivates_removed_options_and_keeps_votes(conn, poll_id):
    (alpha,) = option_ids(conn, "alpha")
    conn.execute("INSERT INTO ballots VALUES ('b1', ?, 'v', 'n', ?)", (poll_id, db.to_iso(NOW)))
    conn.execute("INSERT INTO ballot_choices VALUES ('b1', ?)", (alpha,))
    data = poll_data()
    data["options"] = [o for o in data["options"] if o["slug"] != "alpha"]
    db.sync_poll(conn, PollConfig.model_validate(data), NOW)
    row = conn.execute("SELECT active FROM options WHERE id = ?", (alpha,)).fetchone()
    assert row["active"] == 0
    assert conn.execute("SELECT COUNT(*) FROM ballot_choices").fetchone()[0] == 1


def test_unique_voter_per_poll(conn, poll_id):
    conn.execute("INSERT INTO ballots VALUES ('b1', ?, 'v', 'n', 'x')", (poll_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ballots VALUES ('b2', ?, 'v', 'n', 'x')", (poll_id,))


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ballot_choices VALUES ('missing', 1)")


def test_immediate_rolls_back_on_error(conn, poll_id):
    with pytest.raises(RuntimeError), db.immediate(conn):
        conn.execute("INSERT INTO ballots VALUES ('b1', ?, 'v', 'n', 'x')", (poll_id,))
        raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0


def test_voting_state(conn, poll_id, poll_config):
    assert voting_state(db.get_poll(conn, poll_id), NOW) == "draft"
    db.set_status(conn, poll_id, "open")
    assert voting_state(db.get_poll(conn, poll_id), NOW) == "open"
    data = poll_data(
        opens_at=(NOW + timedelta(hours=1)).isoformat(),
        closes_at=(NOW + timedelta(hours=2)).isoformat(),
    )
    db.sync_poll(conn, PollConfig.model_validate(data), NOW)
    poll = db.get_poll(conn, poll_id)
    assert voting_state(poll, NOW) == "scheduled"
    assert voting_state(poll, NOW + timedelta(minutes=90)) == "open"
    assert voting_state(poll, NOW + timedelta(hours=2)) == "closed"
    db.set_status(conn, poll_id, "closed")
    assert voting_state(db.get_poll(conn, poll_id), NOW + timedelta(minutes=90)) == "closed"
