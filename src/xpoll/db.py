import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from xpoll.poll_config import PollConfig


class SchemaVersionError(RuntimeError):
    pass


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _migrations() -> list[str]:
    folder = resources.files("xpoll") / "migrations"
    names = sorted(p.name for p in folder.iterdir() if p.name.endswith(".sql"))
    return [(folder / name).read_text(encoding="utf-8") for name in names]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    migrations = _migrations()
    current = schema_version(conn)
    if current > len(migrations):
        raise SchemaVersionError(
            f"database schema version {current} is newer than this XPoll ({len(migrations)})"
        )
    for version, sql in enumerate(migrations[current:], start=current + 1):
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
            )
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(migrations)


def sync_poll(conn: sqlite3.Connection, config: PollConfig, now: datetime) -> int:
    """Upsert the poll and its options from config. Status is never changed here."""
    poll = config.poll
    window = (
        to_iso(poll.opens_at) if poll.opens_at else None,
        to_iso(poll.closes_at) if poll.closes_at else None,
    )
    with immediate(conn):
        conn.execute(
            """
            INSERT INTO polls
                (slug, title, min_choices, max_choices, opens_at, closes_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (slug) DO UPDATE SET
                title = excluded.title,
                min_choices = excluded.min_choices,
                max_choices = excluded.max_choices,
                opens_at = excluded.opens_at,
                closes_at = excluded.closes_at
            """,
            (poll.slug, poll.title, poll.min_choices, poll.max_choices, *window, to_iso(now)),
        )
        poll_id = conn.execute("SELECT id FROM polls WHERE slug = ?", (poll.slug,)).fetchone()[0]
        for order, option in enumerate(config.options):
            conn.execute(
                """
                INSERT INTO options (poll_id, slug, name, url, description, sort_order, active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (poll_id, slug) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    description = excluded.description,
                    sort_order = excluded.sort_order,
                    active = excluded.active
                """,
                (
                    poll_id,
                    option.slug,
                    option.name,
                    option.url,
                    option.description,
                    order,
                    int(option.active),
                ),
            )
        slugs = [o.slug for o in config.options]
        placeholders = ",".join("?" * len(slugs))
        conn.execute(
            f"UPDATE options SET active = 0 WHERE poll_id = ? AND slug NOT IN ({placeholders})",  # noqa: S608
            (poll_id, *slugs),
        )
    return poll_id


def get_poll(conn: sqlite3.Connection, poll_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()


def set_status(conn: sqlite3.Connection, poll_id: int, status: str) -> None:
    conn.execute("UPDATE polls SET status = ? WHERE id = ?", (status, poll_id))


def active_options(conn: sqlite3.Connection, poll_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM options WHERE poll_id = ? AND active = 1 ORDER BY sort_order, id",
        (poll_id,),
    ).fetchall()
