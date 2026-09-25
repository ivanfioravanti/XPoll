import re

from xpoll.assets import IMMUTABLE, SHORT, asset_versions


def test_versions_change_with_content(tmp_path):
    (tmp_path / "a.js").write_text("one")
    first = asset_versions(tmp_path)["a.js"]
    (tmp_path / "a.js").write_text("two")
    assert asset_versions(tmp_path)["a.js"] != first
    assert re.fullmatch(r"[0-9a-f]{12}", first)


def test_pages_link_versioned_assets(client, app):
    html = client.get("/").text
    versions = app.state.ctx.asset_versions
    for name in ("app.js", "styles.css", "theme.js", "favicon.svg"):
        assert f"/static/{name}?v={versions[name]}" in html
    assert re.search(r'href="/theme\.css\?v=[0-9a-f]{6}"', html)


def test_versioned_assets_cached_long_unversioned_short(client, app):
    version = app.state.ctx.asset_versions["app.js"]
    assert client.get(f"/static/app.js?v={version}").headers["cache-control"] == IMMUTABLE
    assert client.get("/static/app.js").headers["cache-control"] == SHORT
    assert client.get("/static/missing.js?v=1").status_code == 404


def test_html_is_never_cached(client):
    assert client.get("/").headers["cache-control"] == "no-store"
