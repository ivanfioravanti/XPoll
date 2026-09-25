from pathlib import Path

import pytest
from pydantic import ValidationError

from xpoll.poll_config import PollConfig, load_poll_config

ROOT = Path(__file__).resolve().parents[1]


def base(**poll_overrides):
    return {
        "poll": {"slug": "p", "title": "T", "question": "Q?", "max_choices": 2, **poll_overrides},
        "operator": {"name": "Op", "contact": "op@example.org", "retention": "30 days"},
        "options": [
            {"slug": "a", "name": "Alpha", "url": "https://github.com/x/a"},
            {"slug": "b", "name": "Beta", "url": "https://github.com/x/b"},
            {"slug": "c", "name": "Gamma", "url": "https://github.com/x/c"},
        ],
    }


@pytest.mark.parametrize("path", ["poll.example.toml", "examples/mlx-engines.toml"])
def test_shipped_configs_validate(path):
    config = load_poll_config(ROOT / path)
    assert config.options
    assert config.poll.max_choices <= len(config.active_options)


def test_defaults():
    config = PollConfig.model_validate(base())
    assert config.poll.min_choices == 1
    assert config.poll.results_visibility == "live"
    assert config.suggestions.allowed_hosts == ("github.com",)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["poll"].update(min_choices=3), "min_choices"),
        (lambda d: d["poll"].update(max_choices=4), "max_choices exceeds"),
        (lambda d: d["options"][1].update(slug="a"), "duplicate option slug"),
        (lambda d: d["options"][1].update(name="ALPHA"), "duplicate option name"),
        (lambda d: d["options"][1].update(url="https://github.com/X/A"), "duplicate option url"),
        (lambda d: d["options"][0].update(url="http://github.com/x/a"), "https"),
        (lambda d: d["options"][0].update(url="https://u:p@github.com/x"), "https"),
        (lambda d: d["options"][0].update(slug="Bad Slug"), "pattern"),
        (lambda d: d["poll"].update(accent_color="red"), "pattern"),
        (lambda d: d["poll"].update(theme="sepia"), "auto"),
        (lambda d: d["poll"].update(unknown=1), "Extra inputs"),
        (lambda d: d["poll"].update(opens_at="2026-10-01T08:00:00"), "UTC offset"),
        (
            lambda d: d["poll"].update(
                opens_at="2026-10-02T00:00:00Z", closes_at="2026-10-01T00:00:00Z"
            ),
            "before closes_at",
        ),
        (lambda d: d.update(options=[]), "at least 1"),
    ],
)
def test_invalid_configs(mutate, message):
    data = base()
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        PollConfig.model_validate(data)


def test_inactive_options_do_not_count_towards_max():
    data = base()
    data["options"][2]["active"] = False
    with pytest.raises(ValidationError, match="max_choices exceeds"):
        PollConfig.model_validate({**data, "poll": {**data["poll"], "max_choices": 3}})
    assert len(PollConfig.model_validate(data).active_options) == 2
