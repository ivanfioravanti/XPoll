import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from xpoll.db import immediate, to_iso

if TYPE_CHECKING:
    from xpoll.poll_config import Question


class BallotRejected(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def validate_choices(
    option_ids: list[int], *, min_choices: int, max_choices: int, valid_ids: Iterable[int]
) -> list[int]:
    if len(set(option_ids)) != len(option_ids):
        raise BallotRejected(422, "duplicate-choice", "Each option can be selected only once.")
    if not min_choices <= len(option_ids) <= max_choices:
        raise BallotRejected(
            422,
            "choice-count",
            f"Select between {min_choices} and {max_choices} options.",
        )
    if set(option_ids) - set(valid_ids):
        raise BallotRejected(422, "unknown-option", "One of the selected options is not available.")
    return option_ids


def validate_answers(
    answers: dict[str, str], questions: Iterable["Question"]
) -> list[tuple[str, str]]:
    """Optional answers: blanks are skipped; anything not offered in poll.toml is rejected."""
    allowed = {q.slug: set(q.choices) for q in questions}
    cleaned = []
    for slug, choice in answers.items():
        if choice == "":
            continue
        if slug not in allowed or choice not in allowed[slug]:
            raise BallotRejected(422, "invalid-answer", "One of the optional answers is not valid.")
        cleaned.append((slug, choice))
    return cleaned


def has_voted(conn: sqlite3.Connection, poll_id: int, voter_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ballots WHERE poll_id = ? AND voter_hash = ?", (poll_id, voter_hash)
    ).fetchone()
    return row is not None


def record_ballot(
    conn: sqlite3.Connection,
    *,
    poll_id: int,
    option_ids: list[int],
    voter_hash: str,
    network_hash: str,
    now: datetime,
    answers: list[tuple[str, str]] = (),
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """Insert the ballot and all its choices in one transaction; returns (ballot_id, total)."""
    ballot_id = str(uuid.uuid4())
    for attempt in range(retries + 1):
        try:
            with immediate(conn):
                conn.execute(
                    "INSERT INTO ballots (id, poll_id, voter_hash, network_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (ballot_id, poll_id, voter_hash, network_hash, to_iso(now)),
                )
                conn.executemany(
                    "INSERT INTO ballot_choices (ballot_id, option_id) VALUES (?, ?)",
                    [(ballot_id, option_id) for option_id in option_ids],
                )
                conn.executemany(
                    "INSERT INTO ballot_answers (ballot_id, question, choice) VALUES (?, ?, ?)",
                    [(ballot_id, slug, choice) for slug, choice in answers],
                )
                total = conn.execute(
                    "SELECT COUNT(*) FROM ballots WHERE poll_id = ?", (poll_id,)
                ).fetchone()[0]
            return ballot_id, total
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
            raise BallotRejected(409, "already-voted", "This browser has already voted.") from exc
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            if attempt == retries:
                raise BallotRejected(
                    503, "busy", "The poll is busy right now. Please try again."
                ) from exc
            sleep(0.05 * 2**attempt)
    raise AssertionError("unreachable")
