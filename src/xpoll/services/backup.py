import os
import sqlite3
from datetime import datetime
from pathlib import Path

from xpoll.db import immediate, to_iso


class BackupError(RuntimeError):
    pass


def integrity_check(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def _copy(source: Path, target: Path) -> None:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()


def backup_database(db_path: Path, dest_dir: Path, *, now: datetime, keep: int = 48) -> Path:
    """Online backup to a temp file, verify integrity, atomically rename, prune old copies."""
    if not db_path.exists():
        raise BackupError(f"database not found: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = dest_dir / f"poll-{now.strftime('%Y%m%dT%H%M%S%fZ')}.db"
    tmp = final.with_name(final.name + ".tmp")
    try:
        _copy(db_path, tmp)
        result = integrity_check(tmp)
        if result != "ok":
            raise BackupError(f"integrity check failed: {result}")
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    backups = sorted(dest_dir.glob("poll-*.db"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()
    return final


def restore_database(backup: Path, dest: Path, *, force: bool = False) -> None:
    result = integrity_check(backup)
    if result != "ok":
        raise BackupError(f"backup failed integrity check: {result}")
    sidecars = [dest.with_name(dest.name + suffix) for suffix in ("-wal", "-shm")]
    if (dest.exists() or any(p.exists() for p in sidecars)) and not force:
        raise BackupError(f"{dest} already exists; pass --force to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".restore")
    try:
        _copy(backup, tmp)
        for sidecar in sidecars:
            sidecar.unlink(missing_ok=True)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def purge_identifiers(conn: sqlite3.Connection, poll_id: int, before: datetime) -> tuple[int, int]:
    """Drop voter/network hashes older than `before`; aggregate results are unchanged."""
    cutoff = to_iso(before)
    with immediate(conn):
        ballots = conn.execute(
            "UPDATE ballots SET voter_hash = 'purged:' || id, network_hash = 'purged'"
            " WHERE poll_id = ? AND created_at < ? AND network_hash != 'purged'",
            (poll_id, cutoff),
        ).rowcount
        suggestions = conn.execute(
            "UPDATE suggestions SET network_hash = 'purged'"
            " WHERE poll_id = ? AND created_at < ? AND network_hash != 'purged'",
            (poll_id, cutoff),
        ).rowcount
    return ballots, suggestions
