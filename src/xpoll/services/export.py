import csv
import io
import sqlite3
from collections.abc import Iterable
from typing import Any

from xpoll.services.results import compute_breakdown, compute_results

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _to_csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([spreadsheet_safe(cell) for cell in row])
    return buffer.getvalue()


def results_csv(conn: sqlite3.Connection, poll_id: int, poll_slug: str) -> str:
    data = compute_results(conn, poll_id, poll_slug)
    return _to_csv(
        ["rank", "option", "url", "respondents", "percentage", "total_ballots"],
        (
            [rank, r["name"], r["url"], r["respondents"], r["percentage"], data["total_ballots"]]
            for rank, r in enumerate(data["results"], start=1)
        ),
    )


def suggestions_csv(conn: sqlite3.Connection, poll_id: int) -> str:
    rows = conn.execute(
        "SELECT created_at, name, url, notes, status FROM suggestions"
        " WHERE poll_id = ? ORDER BY created_at",
        (poll_id,),
    ).fetchall()
    return _to_csv(
        ["created_at", "name", "url", "notes", "status"],
        ([r["created_at"], r["name"], r["url"] or "", r["notes"], r["status"]] for r in rows),
    )


def breakdown_csv(conn: sqlite3.Connection, poll_id: int, questions) -> str:
    """Every option per answer group; groups under MIN_GROUP ballots are listed but not split."""
    rows = []
    for q in compute_breakdown(conn, poll_id, questions, top_n=None):
        for c in q["choices"]:
            if c["top"] is None:
                rows.append(
                    [q["slug"], c["label"], c["count"], c["percentage"], "(hidden: <5)", ""]
                )
            for t in c["top"] or []:
                rows.append(
                    [q["slug"], c["label"], c["count"], c["percentage"], t["name"], t["percentage"]]
                )
    return _to_csv(
        ["question", "answer", "ballots", "answer_pct", "option", "option_pct_in_group"], rows
    )
