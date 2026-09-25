import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.conftest import BASE_URL, NOW, open_poll, option_ids, poll_data
from xpoll import db
from xpoll.poll_config import PollConfig
from xpoll.services.ballots import BallotRejected, record_ballot, validate_answers
from xpoll.services.export import breakdown_csv
from xpoll.services.results import MIN_GROUP, compute_breakdown

QUESTIONS = [
    {"slug": "chip", "label": "Chip", "choices": ["M1", "M3 Max", "M5 Ultra"]},
    {"slug": "memory", "label": "Unified memory", "choices": ["36 GB or less", "128 GB"]},
]


def config_with_questions() -> PollConfig:
    data = poll_data()
    data["questions"] = QUESTIONS
    return PollConfig.model_validate(data)


@pytest.fixture
def qapp(make_app):
    app = make_app(config_with_questions())
    open_poll(app)
    return app


@pytest.fixture
def qclient(qapp):
    with TestClient(qapp, base_url=BASE_URL, headers={"Origin": BASE_URL}) as client:
        client.get("/")
        yield client


def ids(app, *slugs):
    conn = db.connect(app.state.ctx.settings.database_path)
    try:
        return option_ids(conn, *slugs)
    finally:
        conn.close()


def answers_in_db(app):
    conn = db.connect(app.state.ctx.settings.database_path)
    try:
        return sorted(tuple(r) for r in conn.execute("SELECT question, choice FROM ballot_answers"))
    finally:
        conn.close()


# --- config ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([{**QUESTIONS[0], "choices": ["M1"]}], "at least 2"),
        ([{**QUESTIONS[0], "choices": ["M1", "m1"]}], "unique"),
        ([QUESTIONS[0], {**QUESTIONS[1], "slug": "chip"}], "duplicate question slug"),
        ([QUESTIONS[0]] * 6, "at most 5"),
    ],
)
def test_invalid_questions(questions, message):
    data = poll_data()
    data["questions"] = questions
    with pytest.raises(ValidationError, match=message):
        PollConfig.model_validate(data)


def test_mlx_example_questions():
    from pathlib import Path

    from xpoll.poll_config import load_poll_config

    config = load_poll_config(Path(__file__).resolve().parents[1] / "examples/mlx-engines.toml")
    chip, memory = config.questions
    assert "M5 Ultra" in chip.choices
    assert "M4 Ultra" not in chip.choices
    assert memory.choices[0] == "36 GB or less"


# --- validation service ------------------------------------------------------


def test_validate_answers_skips_blanks_and_rejects_unknown():
    questions = config_with_questions().questions
    assert validate_answers({"chip": "M1", "memory": ""}, questions) == [("chip", "M1")]
    assert validate_answers({}, questions) == []
    for bad in ({"chip": "M9"}, {"gpu": "M1"}, {"chip": "m1"}):
        with pytest.raises(BallotRejected) as info:
            validate_answers(bad, questions)
        assert info.value.code == "invalid-answer"


# --- API ---------------------------------------------------------------------


def vote(client, app, answers=None):
    body = {"option_ids": ids(app, "alpha"), "turnstile_token": "t"}
    if answers is not None:
        body["answers"] = answers
    return client.post("/api/ballots", json=body)


def test_vote_with_answers_is_stored(qapp, qclient):
    assert vote(qclient, qapp, {"chip": "M3 Max", "memory": "128 GB"}).status_code == 201
    assert answers_in_db(qapp) == [("chip", "M3 Max"), ("memory", "128 GB")]


def test_answers_are_optional(qapp, qclient):
    assert vote(qclient, qapp).status_code == 201
    assert answers_in_db(qapp) == []


@pytest.mark.parametrize(
    "answers", [{"chip": "M9"}, {"gpu": "x"}, {"chip": 3}, {str(i): "M1" for i in range(11)}]
)
def test_invalid_answers_reject_the_whole_ballot(qapp, qclient, answers):
    assert vote(qclient, qapp, answers).status_code == 422
    conn = db.connect(qapp.state.ctx.settings.database_path)
    assert conn.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0
    conn.close()


def test_answers_roll_back_with_the_ballot(conn, poll_id):
    # The duplicate answer violates the primary key; the whole ballot must roll back.
    with pytest.raises(BallotRejected):
        record_ballot(
            conn,
            poll_id=poll_id,
            option_ids=option_ids(conn, "alpha"),
            voter_hash="v",
            network_hash="n",
            now=NOW,
            answers=[("chip", "M1"), ("chip", "M3 Max")],  # duplicate key violates the PK
        )
    assert conn.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ballot_answers").fetchone()[0] == 0


# --- breakdown ---------------------------------------------------------------


def seed(conn, poll_id, chip, slugs, n, memory=None):
    for i in range(n):
        answers = [("chip", chip)] + ([("memory", memory)] if memory else [])
        record_ballot(
            conn,
            poll_id=poll_id,
            option_ids=option_ids(conn, *slugs),
            voter_hash=f"{chip}-{slugs}-{i}",
            network_hash="n",
            now=NOW + timedelta(seconds=i),
            answers=answers,
        )


def test_breakdown_counts_order_and_small_group_suppression(conn, poll_id):
    questions = config_with_questions().questions
    seed(conn, poll_id, "M3 Max", ("alpha", "beta"), 4)
    seed(conn, poll_id, "M3 Max", ("alpha",), 2)
    seed(conn, poll_id, "M1", ("gamma",), 1)
    chip, memory = compute_breakdown(conn, poll_id, questions)
    assert chip["answered"] == 7
    assert [c["label"] for c in chip["choices"]] == ["M3 Max", "M1"]
    big, small = chip["choices"]
    assert (big["count"], big["percentage"]) == (6, 85.7)
    assert big["top"] == [
        {"name": "Alpha", "percentage": 100.0},
        {"name": "beta", "percentage": 66.7},
    ]
    assert small["count"] == 1
    assert small["top"] is None  # a single M1 ballot must not reveal its picks
    assert memory == {"slug": "memory", "label": "Unified memory", "answered": 0, "choices": []}
    assert MIN_GROUP == 5


def test_results_api_and_pages_include_breakdown(qapp, qclient):
    for _ in range(5):
        client = TestClient(qapp, base_url=BASE_URL, headers={"Origin": BASE_URL})
        client.get("/")
        assert vote(client, qapp, {"chip": "M5 Ultra"}).status_code == 201
    data = qclient.get("/api/results").json()
    chip = data["questions"][0]
    assert chip["choices"][0]["label"] == "M5 Ultra"
    assert chip["choices"][0]["top"][0]["name"] == "Alpha"
    html = qclient.get("/results").text
    assert "By chip" in html
    assert "5 of 5 answered" in html
    assert "Top: Alpha 100%" in html
    assert "No answers yet." in html  # memory question


def test_ballot_page_has_optional_selects(qclient):
    html = qclient.get("/").text
    assert "About your setup" in html
    assert '<select id="question-chip" name="answer" data-question="chip">' in html
    assert '<option value="">Prefer not to say</option>' in html
    assert "<option>M5 Ultra</option>" in html
    assert html.index('id="question-chip"') < html.index("data-turnstile")


def test_privacy_page_discloses_optional_answers(qclient):
    html = qclient.get("/privacy").text
    assert "Optional answers." in html
    assert "chip, unified memory" in html
    assert "at least 5 ballots" in html


def test_polls_without_questions_show_nothing_extra(client):
    assert "About your setup" not in client.get("/").text
    assert "Optional answers." not in client.get("/privacy").text


def test_breakdown_csv_hides_small_groups(conn, poll_id):
    questions = config_with_questions().questions
    seed(conn, poll_id, "M3 Max", ("alpha", "beta"), 5)
    seed(conn, poll_id, "M1", ("gamma",), 1)
    rows = list(csv.reader(io.StringIO(breakdown_csv(conn, poll_id, questions))))
    assert rows[0] == [
        "question",
        "answer",
        "ballots",
        "answer_pct",
        "option",
        "option_pct_in_group",
    ]
    assert ["chip", "M3 Max", "5", "83.3", "Alpha", "100.0"] in rows
    assert ["chip", "M1", "1", "16.7", "(hidden: <5)", ""] in rows
    assert not any(r[4] == "gamma" for r in rows)
