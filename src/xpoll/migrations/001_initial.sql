CREATE TABLE polls (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'closed')),
    min_choices INTEGER NOT NULL DEFAULT 1 CHECK (min_choices >= 1),
    max_choices INTEGER NOT NULL DEFAULT 5 CHECK (max_choices >= min_choices),
    opens_at TEXT,
    closes_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE options (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE (poll_id, slug)
);

CREATE TABLE ballots (
    id TEXT PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    voter_hash TEXT NOT NULL,
    network_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (poll_id, voter_hash)
);

CREATE TABLE ballot_choices (
    ballot_id TEXT NOT NULL REFERENCES ballots(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES options(id),
    PRIMARY KEY (ballot_id, option_id)
);

CREATE TABLE suggestions (
    id TEXT PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    name TEXT NOT NULL,
    url TEXT,
    notes TEXT NOT NULL DEFAULT '',
    network_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected'))
);

CREATE INDEX ballots_poll_created_idx ON ballots(poll_id, created_at);
CREATE INDEX choices_option_idx ON ballot_choices(option_id);
CREATE INDEX suggestions_poll_created_idx ON suggestions(poll_id, created_at);
