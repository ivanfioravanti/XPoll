import tomllib
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_https(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError(f"URL must be https without credentials: {url!r}")
    return url


class PollOption(_Strict):
    slug: Slug
    name: ShortText
    url: str
    description: LongText = ""
    active: bool = True

    _https = field_validator("url")(_require_https)


class PollSection(_Strict):
    slug: Slug
    title: ShortText
    question: ShortText
    description: LongText = ""
    min_choices: int = Field(default=1, ge=1)
    max_choices: int = Field(default=5, ge=1)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    results_visibility: Literal["live", "after_close"] = "live"
    option_noun: ShortText = "option"
    accent_color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    theme: Literal["auto", "light", "dark"] = "auto"
    social_image: str | None = Field(default=None, pattern=r"(?i)^[^\\]+\.(png|jpe?g)$")

    @field_validator("opens_at", "closes_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset, e.g. 2026-10-01T08:00:00Z")
        return value


class OperatorSection(_Strict):
    name: ShortText
    contact: ShortText
    retention: LongText
    disclaimer: LongText = ""


class SuggestionsSection(_Strict):
    enabled: bool = True
    allowed_hosts: tuple[str, ...] = ("github.com",)


class PollConfig(_Strict):
    poll: PollSection
    operator: OperatorSection
    suggestions: SuggestionsSection = SuggestionsSection()
    options: tuple[PollOption, ...] = Field(min_length=1)

    @property
    def active_options(self) -> tuple[PollOption, ...]:
        return tuple(o for o in self.options if o.active)

    @model_validator(mode="after")
    def _check(self) -> "PollConfig":
        poll = self.poll
        if poll.min_choices > poll.max_choices:
            raise ValueError("min_choices must be <= max_choices")
        if poll.max_choices > len(self.active_options):
            raise ValueError("max_choices exceeds the number of active options")
        if poll.opens_at and poll.closes_at and poll.opens_at >= poll.closes_at:
            raise ValueError("opens_at must be before closes_at")
        for field in ("slug", "name", "url"):
            values = [getattr(o, field).lower() for o in self.options]
            dupes = sorted({v for v in values if values.count(v) > 1})
            if dupes:
                raise ValueError(f"duplicate option {field}: {', '.join(dupes)}")
        return self


def load_poll_config(path: Path) -> PollConfig:
    with path.open("rb") as fh:
        return PollConfig.model_validate(tomllib.load(fh))
