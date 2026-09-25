import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from xpoll import db
from xpoll.config import Settings
from xpoll.identity import Identity
from xpoll.poll_config import PollConfig
from xpoll.ratelimit import RateLimiter
from xpoll.services.polls import VotingState, voting_state
from xpoll.services.results import ResultsCache, Snapshot, compute_results
from xpoll.turnstile import TurnstileVerifier


@dataclass
class AppContext:
    settings: Settings
    config: PollConfig
    poll_id: int
    identity: Identity
    verifier: TurnstileVerifier
    ballot_limiter: RateLimiter
    suggestion_limiter: RateLimiter
    results: ResultsCache
    now: Callable[[], datetime]

    def state(self, conn: sqlite3.Connection) -> VotingState:
        return voting_state(db.get_poll(conn, self.poll_id), self.now())

    def results_visible(self, state: VotingState) -> bool:
        return self.config.poll.results_visibility == "live" or state == "closed"

    def snapshot(self, conn: sqlite3.Connection) -> Snapshot:
        return self.results.get(lambda: compute_results(conn, self.poll_id, self.config.poll.slug))


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(get_ctx(request).settings.database_path)
    try:
        yield conn
    finally:
        conn.close()
