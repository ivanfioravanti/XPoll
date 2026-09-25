import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xpoll.poll_config import Question


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


MIN_GROUP = 5  # smaller groups could expose an individual's ballot, so no cross-tab for them
TOP_N = 3


def compute_breakdown(
    conn: sqlite3.Connection,
    poll_id: int,
    questions: Iterable["Question"],
    top_n: int | None = TOP_N,
) -> list[dict[str, Any]]:
    """Per optional question: how many picked each answer, and top options per large group."""
    breakdown = []
    for question in questions:
        rows = conn.execute(
            """
            SELECT a.choice, COUNT(*) AS n
            FROM ballot_answers a JOIN ballots b ON b.id = a.ballot_id
            WHERE b.poll_id = ? AND a.question = ?
            GROUP BY a.choice
            """,
            (poll_id, question.slug),
        ).fetchall()
        answered = sum(row["n"] for row in rows)
        order = {choice: i for i, choice in enumerate(question.choices)}
        choices = []
        for row in sorted(rows, key=lambda r: (-r["n"], order.get(r["choice"], len(order)))):
            top = None
            if row["n"] >= MIN_GROUP:
                top = [
                    {"name": t["name"], "percentage": round(t["r"] * 100 / row["n"], 1)}
                    for t in conn.execute(
                        """
                        SELECT o.name, COUNT(*) AS r
                        FROM ballot_answers a
                        JOIN ballots b ON b.id = a.ballot_id
                        JOIN ballot_choices bc ON bc.ballot_id = a.ballot_id
                        JOIN options o ON o.id = bc.option_id
                        WHERE b.poll_id = ? AND a.question = ? AND a.choice = ?
                        GROUP BY o.id
                        ORDER BY r DESC, lower(o.name) ASC
                        LIMIT ?
                        """,
                        (poll_id, question.slug, row["choice"], -1 if top_n is None else top_n),
                    )
                ]
            choices.append(
                {
                    "label": row["choice"],
                    "count": row["n"],
                    "percentage": round(row["n"] * 100 / answered, 1),
                    "top": top,
                }
            )
        breakdown.append(
            {
                "slug": question.slug,
                "label": question.label,
                "answered": answered,
                "choices": choices,
            }
        )
    return breakdown


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
