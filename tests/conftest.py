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


class FakeVerifier:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def verify(self, token: str, *, action: str):
        from xpoll.turnstile import TurnstileResult

        self.calls.append((token, action))
        return TurnstileResult(self.ok, "ok" if self.ok else "invalid-token")


class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


BASE_URL = "https://poll.example.org"


@pytest.fixture
def settings(db_path):
    from xpoll.config import Settings

    return Settings(_env_file=None, app_base_url=BASE_URL, database_path=db_path)


@pytest.fixture
def verifier():
    return FakeVerifier()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_app(settings, verifier, clock):
    from xpoll.main import create_app

    def factory(config: PollConfig | None = None, **overrides):
        return create_app(
            settings=overrides.get("settings", settings),
            config=config or PollConfig.model_validate(poll_data()),
            verifier=overrides.get("verifier", verifier),
            now=clock,
        )

    return factory


@pytest.fixture
def app(make_app):
    return make_app()


def open_poll(app):
    ctx = app.state.ctx
    connection = db.connect(ctx.settings.database_path)
    db.set_status(connection, ctx.poll_id, "open")
    connection.close()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url=BASE_URL, headers={"Origin": BASE_URL}) as test_client:
        yield test_client


@pytest.fixture
def voter(client):
    """A client that has loaded the page and received its voter cookie."""
    assert client.get("/").status_code == 200
    return client
