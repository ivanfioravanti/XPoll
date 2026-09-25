from datetime import UTC, datetime
from pathlib import Path

import pytest

from xpoll import db
from xpoll.poll_config import PollConfig

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def poll_data(**poll_overrides) -> dict:
    return {
        "poll": {
            "slug": "test-poll",
            "title": "Test poll",
            "question": "Which tools do you use?",
            "min_choices": 1,
            "max_choices": 3,
            "option_noun": "tool",
            **poll_overrides,
        },
        "operator": {"name": "Tester", "contact": "t@example.org", "retention": "30 days"},
        "options": [
            {"slug": "alpha", "name": "Alpha", "url": "https://github.com/x/alpha"},
            {"slug": "beta", "name": "beta", "url": "https://github.com/x/beta"},
            {"slug": "gamma", "name": "Gamma", "url": "https://github.com/x/gamma"},
            {"slug": "delta", "name": "Delta", "url": "https://github.com/x/delta"},
            {"slug": "old", "name": "Old", "url": "https://github.com/x/old", "active": False},
        ],
    }


@pytest.fixture
def poll_config() -> PollConfig:
    return PollConfig.model_validate(poll_data())


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "poll.db"


@pytest.fixture
def conn(db_path, poll_config):
    connection = db.connect(db_path)
    db.migrate(connection)
    db.sync_poll(connection, poll_config, NOW)
    yield connection
    connection.close()


@pytest.fixture
def poll_id(conn) -> int:
    return conn.execute("SELECT id FROM polls").fetchone()[0]


def option_ids(conn, *slugs: str) -> list[int]:
    rows = conn.execute("SELECT slug, id FROM options").fetchall()
    by_slug = {r["slug"]: r["id"] for r in rows}
    return [by_slug[s] for s in slugs]
