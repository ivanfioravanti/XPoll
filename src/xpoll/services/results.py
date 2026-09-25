import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    payload: dict[str, Any]
    etag: str


def compute_results(conn: sqlite3.Connection, poll_id: int, poll_slug: str) -> dict[str, Any]:
    total, latest = conn.execute(
        "SELECT COUNT(*), MAX(created_at) FROM ballots WHERE poll_id = ?", (poll_id,)
    ).fetchone()
    rows = conn.execute(
        """
        SELECT o.id, o.slug, o.name, o.url, o.active, COUNT(bc.ballot_id) AS respondents
        FROM options o
        LEFT JOIN ballot_choices bc ON bc.option_id = o.id
        WHERE o.poll_id = ?
        GROUP BY o.id
        HAVING o.active = 1 OR respondents > 0
        ORDER BY respondents DESC, lower(o.name) ASC, o.id ASC
        """,
        (poll_id,),
    ).fetchall()
    return {
        "poll": poll_slug,
        "total_ballots": total,
        "updated_at": latest,
        "results": [
            {
                "option_id": row["id"],
                "slug": row["slug"],
                "name": row["name"],
                "url": row["url"],
                "active": bool(row["active"]),
                "respondents": row["respondents"],
                "percentage": round(row["respondents"] * 100 / total, 1) if total else 0.0,
            }
            for row in rows
        ],
    }


class ResultsCache:
    """Serve one aggregate query per TTL window no matter how many tabs are polling."""

    def __init__(self, ttl: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._expires = 0.0

    def get(self, load: Callable[[], dict[str, Any]]) -> Snapshot:
        with self._lock:
            if self._snapshot is None or self.clock() >= self._expires:
                payload = load()
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[
                    :20
                ]
                self._snapshot = Snapshot(payload, f'W/"{digest}"')
                self._expires = self.clock() + self.ttl
            return self._snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None
