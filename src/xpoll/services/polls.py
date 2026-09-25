import sqlite3
from datetime import datetime
from typing import Literal

from xpoll.db import from_iso

VotingState = Literal["draft", "scheduled", "open", "closed"]


def voting_state(poll: sqlite3.Row, now: datetime) -> VotingState:
    if poll["status"] == "draft":
        return "draft"
    if poll["status"] == "closed":
        return "closed"
    opens_at, closes_at = from_iso(poll["opens_at"]), from_iso(poll["closes_at"])
    if opens_at and now < opens_at:
        return "scheduled"
    if closes_at and now >= closes_at:
        return "closed"
    return "open"
