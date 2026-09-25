"""Render a 1200x630 link-preview card (Open Graph / X) for a poll.

    uv run --project ~/github/XPoll --with playwright python scripts/social_image.py \
        --config ~/xpoll/mlxengines/poll.toml --out ~/xpoll/mlxengines/social.png

Then set `social_image = "social.png"` in poll.toml and restart the app.
"""

import argparse
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from xpoll.poll_config import load_poll_config

TEMPLATE_DIR = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1200, 630
MAX_CHIPS = 14


def base_url_from_env(config_dir: Path) -> str | None:
    env = config_dir / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        if line.startswith("APP_BASE_URL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


def render_html(config_path: Path, url: str) -> str:
    config = load_poll_config(config_path)
    poll = config.poll
    names = [o.name for o in config.active_options]
    shown = names[:MAX_CHIPS]
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    return env.get_template("social_image.html.j2").render(
        accent=poll.accent_color,
        eyebrow=f"Community poll · {len(names)} {poll.option_noun}s · live results",
        title=poll.title,
        title_size=88 if len(poll.title) <= 34 else 72,
        question=poll.question,
        shown=shown,
        hidden_count=len(names) - len(shown),
        display_url=url.removeprefix("https://").removeprefix("http://").rstrip("/"),
        cta="Vote now →",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--url", help="public poll URL (default: APP_BASE_URL from .env)")
    args = parser.parse_args()
    url = args.url or base_url_from_env(args.config.parent) or "https://example.org"

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "card.html"
        page_file.write_text(render_html(args.config, url), encoding="utf-8")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome")  # installed Google Chrome
            except Exception:
                browser = p.chromium.launch()  # needs `playwright install chromium`
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            page.goto(page_file.as_uri())
            page.screenshot(path=str(args.out), type="png")
            browser.close()
    print(f"wrote {args.out} ({WIDTH}x{HEIGHT}) for {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
