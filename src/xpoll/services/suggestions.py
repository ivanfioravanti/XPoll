import sqlite3
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import urlsplit

from xpoll.db import to_iso

NAME_MIN, NAME_MAX = 2, 80
URL_MAX = 300
NOTES_MAX = 500


class SuggestionRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _has_control_chars(text: str, allow_newlines: bool = False) -> bool:
    for char in text:
        if allow_newlines and char in "\n\r":
            continue
        if unicodedata.category(char) in {"Cc", "Cf"}:
            return True
    return False


def validate_suggestion(
    name: str, url: str | None, notes: str, *, allowed_hosts: Iterable[str]
) -> tuple[str, str | None, str]:
    name = name.strip()
    if not NAME_MIN <= len(name) <= NAME_MAX or _has_control_chars(name):
        raise SuggestionRejected("invalid-name", f"Name must be {NAME_MIN}-{NAME_MAX} characters.")
    url = (url or "").strip() or None
    if url is not None:
        parts = urlsplit(url)
        allowed = {h.lower() for h in allowed_hosts}
        if (
            len(url) > URL_MAX
            or parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port
            or (parts.hostname or "").lower() not in allowed
            or _has_control_chars(url)
        ):
            hosts = ", ".join(sorted(allowed))
            raise SuggestionRejected("invalid-url", f"Repository URL must be https on: {hosts}.")
    notes = notes.strip()
    if len(notes) > NOTES_MAX or _has_control_chars(notes, allow_newlines=True):
        raise SuggestionRejected("invalid-notes", f"Notes must be at most {NOTES_MAX} characters.")
    return name, url, notes


def store_suggestion(
    conn: sqlite3.Connection,
    *,
    poll_id: int,
    name: str,
    url: str | None,
    notes: str,
    network_hash: str,
    now: datetime,
) -> str:
    suggestion_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO suggestions (id, poll_id, name, url, notes, network_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (suggestion_id, poll_id, name, url, notes, network_hash, to_iso(now)),
    )
    return suggestion_id
