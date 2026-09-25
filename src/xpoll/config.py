from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloudflare's documented Turnstile test keys: always-pass / always-block / spent-token.
TURNSTILE_TEST_SITE_KEYS = frozenset(
    {
        "1x00000000000000000000AA",
        "2x00000000000000000000AB",
        "1x00000000000000000000BB",
        "2x00000000000000000000BB",
        "3x00000000000000000000FF",
    }
)
TURNSTILE_TEST_SECRET_KEYS = frozenset(
    {
        "1x0000000000000000000000000000000AA",
        "2x0000000000000000000000000000000AA",
        "3x0000000000000000000000000000000AA",
    }
)
DEV_VOTER_SECRET = "dev-only-voter-secret-do-not-use-in-production"  # noqa: S105 (rejected in prod)
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    app_base_url: str = "http://127.0.0.1:8787"
    database_path: Path = Path("data/poll.db")
    poll_config: Path = Path("poll.toml")
    backup_dir: Path = Path("backups")
    voter_secret: SecretStr = SecretStr(DEV_VOTER_SECRET)
    turnstile_site_key: str = "1x00000000000000000000AA"
    turnstile_secret_key: SecretStr = SecretStr("1x0000000000000000000000000000000AA")
    trust_cloudflare_headers: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def base_origin(self) -> str:
        parts = urlsplit(self.app_base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def base_hostname(self) -> str:
        return urlsplit(self.app_base_url).hostname or ""

    @property
    def uses_turnstile_test_keys(self) -> bool:
        return (
            self.turnstile_site_key in TURNSTILE_TEST_SITE_KEYS
            or self.turnstile_secret_key.get_secret_value() in TURNSTILE_TEST_SECRET_KEYS
        )

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        parts = urlsplit(self.app_base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("APP_BASE_URL must be an absolute http(s) URL")
        if not self.is_production:
            return self
        problems = []
        secret = self.voter_secret.get_secret_value()
        if secret == DEV_VOTER_SECRET or len(secret) < MIN_SECRET_LENGTH:
            problems.append(
                f"VOTER_SECRET must be a random value of at least {MIN_SECRET_LENGTH} chars"
            )
        if self.uses_turnstile_test_keys:
            problems.append("Turnstile test keys are not allowed in production")
        if not self.turnstile_site_key or not self.turnstile_secret_key.get_secret_value():
            problems.append("TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are required")
        if parts.scheme != "https":
            problems.append("APP_BASE_URL must use https in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
