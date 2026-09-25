import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_URL, poll_data
from xpoll.poll_config import PollConfig
from xpoll.social import load_social_image


def write_png(path: Path, width: int = 1200, height: int = 630) -> Path:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


@pytest.fixture
def card_app(make_app, settings, tmp_path):
    write_png(tmp_path / "social.png")
    config = PollConfig.model_validate(poll_data(social_image="social.png"))
    prefixed = settings.model_copy(
        update={"poll_config": tmp_path / "poll.toml", "app_base_url": BASE_URL + "/p"}
    )
    return make_app(config, settings=prefixed)


def test_png_dimensions_are_read(tmp_path):
    image = load_social_image("s.png", tmp_path) or None
    assert image is None  # missing file disables the card instead of crashing
    write_png(tmp_path / "s.png", 800, 418)
    image = load_social_image("s.png", tmp_path)
    assert (image.media_type, image.width, image.height) == ("image/png", 800, 418)


def test_non_image_is_ignored(tmp_path):
    (tmp_path / "fake.png").write_text("not an image")
    assert load_social_image("fake.png", tmp_path) is None


def test_oversized_image_is_ignored(tmp_path, monkeypatch):
    write_png(tmp_path / "big.png")
    monkeypatch.setattr("xpoll.social.MAX_BYTES", 10)
    assert load_social_image("big.png", tmp_path) is None


def test_jpeg_supported_without_dimensions(tmp_path):
    (tmp_path / "s.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 40)
    image = load_social_image("s.jpg", tmp_path)
    assert (image.media_type, image.width) == ("image/jpeg", None)


def test_config_rejects_other_file_types():
    with pytest.raises(ValueError, match="pattern"):
        PollConfig.model_validate(poll_data(social_image="card.svg"))


def test_card_meta_tags_use_absolute_urls(card_app):
    html = TestClient(card_app, base_url=BASE_URL).get("/p/").text
    image_url = f"{BASE_URL}/p/social-image"
    assert f'<meta property="og:image" content="{image_url}">' in html
    assert f'<meta name="twitter:image" content="{image_url}">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta property="og:image:width" content="1200">' in html
    assert '<meta property="og:image:height" content="630">' in html
    assert f'<meta property="og:url" content="{BASE_URL}/p/">' in html
    assert '<meta property="og:title" content="Test poll">' in html


def test_social_image_is_served(card_app):
    response = TestClient(card_app, base_url=BASE_URL).get("/p/social-image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content.startswith(b"\x89PNG")


def test_without_image_uses_small_card(client):
    html = client.get("/").text
    assert '<meta name="twitter:card" content="summary">' in html
    assert "og:image" not in html
    assert client.get("/social-image").status_code == 404


def test_theme_toggle_and_prepaint_script(client):
    html = client.get("/").text
    head = html[: html.index("</head>")]
    assert '<script src="/static/theme.js?v=' in head
    assert head.index("/static/theme.js") < head.index("/static/styles.css")
    assert 'id="theme-toggle"' in html
    assert 'class="theme-toggle" id="theme-toggle" aria-label="Switch color theme" hidden' in html
    assert client.get("/static/theme.js").status_code == 200
