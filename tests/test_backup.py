import sqlite3
from datetime import timedelta

import pytest

from tests.conftest import NOW, option_ids
from xpoll import db
from xpoll.services.backup import (
    BackupError,
    backup_database,
    integrity_check,
    purge_identifiers,
    restore_database,
)
from xpoll.services.ballots import record_ballot
from xpoll.services.results import compute_results


def seed_ballots(conn, poll_id, n=5):
    slugs = ["alpha", "beta", "gamma"]
    for i in range(n):
        record_ballot(
            conn,
            poll_id=poll_id,
            option_ids=option_ids(conn, *slugs[: 1 + i % 3]),
            voter_hash=f"v{i}",
            network_hash="n",
            now=NOW + timedelta(minutes=i),
        )


def test_round_trip_preserves_aggregates(conn, poll_id, db_path, tmp_path):
    seed_ballots(conn, poll_id)
    before = compute_results(conn, poll_id, "p")
    backup = backup_database(db_path, tmp_path / "backups", now=NOW)
    assert backup.name == "poll-20261002T120000000000Z.db"
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    restored = tmp_path / "restored" / "poll.db"
    restore_database(backup, restored)
    other = db.connect(restored)
    assert compute_results(other, poll_id, "p") == before
    assert integrity_check(restored) == "ok"
    other.close()


def test_backup_keeps_bounded_history(conn, db_path, tmp_path):
    dest = tmp_path / "backups"
    for minute in range(5):
        backup_database(db_path, dest, now=NOW + timedelta(minutes=minute), keep=3)
    names = sorted(p.name for p in dest.glob("poll-*.db"))
    assert len(names) == 3
    assert names[0].startswith("poll-20261002T120200")
    assert not list(dest.glob("*.tmp"))


def test_backup_missing_database(tmp_path):
    with pytest.raises(BackupError, match="not found"):
        backup_database(tmp_path / "nope.db", tmp_path, now=NOW)


def test_restore_refuses_existing_destination(conn, db_path, tmp_path):
    backup = backup_database(db_path, tmp_path / "b", now=NOW)
    with pytest.raises(BackupError, match="already exists"):
        restore_database(backup, db_path)
    target = tmp_path / "t.db"
    target.write_bytes(b"")
    restore_database(backup, target, force=True)
    assert integrity_check(target) == "ok"


def test_restore_rejects_corrupt_backup(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises((BackupError, sqlite3.DatabaseError)):
        restore_database(bad, tmp_path / "out.db")


def test_purge_identifiers_keeps_results(conn, poll_id):
    seed_ballots(conn, poll_id)
    conn.execute(
        "INSERT INTO suggestions (id, poll_id, name, network_hash, created_at)"
        " VALUES ('s1', ?, 'X', 'hash', ?)",
        (poll_id, db.to_iso(NOW)),
    )
    before = compute_results(conn, poll_id, "p")
    assert purge_identifiers(conn, poll_id, NOW + timedelta(minutes=3)) == (3, 1)
    assert compute_results(conn, poll_id, "p") == before
    hashes = [r[0] for r in conn.execute("SELECT voter_hash FROM ballots ORDER BY created_at")]
    assert all(h.startswith("purged:") for h in hashes[:3])
    assert hashes[3:] == ["v3", "v4"]
    assert purge_identifiers(conn, poll_id, NOW + timedelta(minutes=3)) == (0, 0)
