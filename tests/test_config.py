import pytest
from pydantic import ValidationError

from xpoll.config import Settings

GOOD_PROD = {
    "app_env": "production",
    "app_base_url": "https://poll.example.org",
    "voter_secret": "x" * 48,
    "turnstile_site_key": "0x4AAAAAAAreal",
    "turnstile_secret_key": "0x4AAAAAAArealsecret",
}


def make(**overrides):
    return Settings(_env_file=None, **overrides)


def test_development_defaults():
    settings = make()
    assert not settings.is_production
    assert settings.uses_turnstile_test_keys
    assert settings.base_origin == "http://127.0.0.1:8787"
    assert settings.trust_cloudflare_headers is False


def test_valid_production_settings():
    settings = make(**GOOD_PROD)
    assert settings.is_production
    assert settings.base_hostname == "poll.example.org"
    assert not settings.uses_turnstile_test_keys


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"voter_secret": "short"}, "VOTER_SECRET"),
        ({"turnstile_site_key": "1x00000000000000000000AA"}, "test keys"),
        ({"turnstile_secret_key": "1x0000000000000000000000000000000AA"}, "test keys"),
        ({"turnstile_secret_key": ""}, "required"),
        ({"app_base_url": "http://poll.example.org"}, "https"),
    ],
)
def test_production_rejects_unsafe_values(override, message):
    with pytest.raises(ValidationError, match=message):
        make(**{**GOOD_PROD, **override})


def test_production_rejects_default_dev_secret():
    values = {**GOOD_PROD}
    del values["voter_secret"]
    with pytest.raises(ValidationError, match="VOTER_SECRET"):
        make(**values)


def test_rejects_relative_base_url():
    with pytest.raises(ValidationError, match="APP_BASE_URL"):
        make(app_base_url="/poll")


def test_base_origin_strips_path():
    assert make(app_base_url="https://example.org/poll/").base_origin == "https://example.org"
