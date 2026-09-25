-- Answers to optional [[questions]]; stored as text so edits to poll.toml never rewrite history.
CREATE TABLE ballot_answers (
    ballot_id TEXT NOT NULL REFERENCES ballots(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    choice TEXT NOT NULL,
    PRIMARY KEY (ballot_id, question)
);

CREATE INDEX answers_question_idx ON ballot_answers(question, choice);
