import csv
import io

import pytest

from tests.conftest import NOW, option_ids
from xpoll import db
from xpoll.services.ballots import record_ballot
from xpoll.services.export import results_csv, spreadsheet_safe, suggestions_csv


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(1)", "'=HYPERLINK(1)"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM", "'@SUM"),
        ("\tx", "'\tx"),
        ("plain", "plain"),
        (5, 5),
    ],
)
def test_spreadsheet_safe(value, expected):
    assert spreadsheet_safe(value) == expected


def test_results_csv(conn, poll_id):
    record_ballot(
        conn,
        poll_id=poll_id,
        option_ids=option_ids(conn, "alpha"),
        voter_hash="secret-voter-hash",
        network_hash="secret-network-hash",
        now=NOW,
    )
    text = results_csv(conn, poll_id, "p")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["rank", "option", "url", "respondents", "percentage", "total_ballots"]
    assert rows[1] == ["1", "Alpha", "https://github.com/x/alpha", "1", "100.0", "1"]
    assert "secret" not in text


def test_suggestions_csv_escapes_and_omits_hashes(conn, poll_id):
    conn.execute(
        "INSERT INTO suggestions (id, poll_id, name, url, notes, network_hash, created_at)"
        " VALUES ('s', ?, '=cmd|calc', NULL, '@x', 'net-hash', ?)",
        (poll_id, db.to_iso(NOW)),
    )
    text = suggestions_csv(conn, poll_id)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["created_at", "name", "url", "notes", "status"]
    assert rows[1][1:] == ["'=cmd|calc", "", "'@x", "pending"]
    assert "net-hash" not in text
