import plistlib
import shutil
import socket
import sys
from pathlib import Path

import pytest

from tests.conftest import poll_data
from xpoll import cli
from xpoll.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def write_config(path: Path, review: bool = False) -> Path:
    data = poll_data()
    lines = [
        "[poll]",
        *(f"{k} = {v!r}".replace("'", '"') for k, v in data["poll"].items()),
        "[operator]",
        *(f'{k} = "{v}"' for k, v in data["operator"].items()),
    ]
    for option in data["options"]:
        lines.append("[[options]]")
        for key, value in option.items():
            lines.append(
                f"{key} = {str(value).lower() if isinstance(value, bool) else repr(value)}"
            )
    text = "\n".join(lines).replace("'", '"')
    if review:
        text = "# REVIEW: something\n" + text
    path.write_text(text)
    return path


@pytest.fixture
def cli_settings(tmp_path):
    config = write_config(tmp_path / "poll.toml")
    return Settings(_env_file=None, database_path=tmp_path / "db" / "poll.db", poll_config=config)


def run(settings, *argv):
    return cli.main(list(argv), settings=settings)


def test_check_and_init(cli_settings, capsys):
    assert run(cli_settings, "check") == 0
    assert "options: 4 active / 5 total" in capsys.readouterr().out
    assert run(cli_settings, "init") == 0
    assert "[draft], 4 options" in capsys.readouterr().out


def test_check_reports_review_markers(cli_settings, capsys):
    write_config(cli_settings.poll_config, review=True)
    assert run(cli_settings, "check") == 1
    assert "unresolved REVIEW (line 1)" in capsys.readouterr().out


def test_status_transitions(cli_settings, capsys):
    assert run(cli_settings, "status") == 0
    assert "status: draft" in capsys.readouterr().out
    assert run(cli_settings, "status", "open") == 0
    out = capsys.readouterr().out
    assert "status: open (effective: open)" in out
    assert "ballots: 0" in out
    assert run(cli_settings, "status", "closed") == 0
    assert "status: closed" in capsys.readouterr().out


def test_open_refuses_review_markers_unless_forced(cli_settings, capsys):
    write_config(cli_settings.poll_config, review=True)
    assert run(cli_settings, "status", "open") == 1
    assert "refusing to open" in capsys.readouterr().err
    assert run(cli_settings, "status", "open", "--force") == 0


def test_export_to_stdout_and_file(cli_settings, capsys, tmp_path):
    assert run(cli_settings, "export", "results") == 0
    assert capsys.readouterr().out.startswith("rank,option")
    out = tmp_path / "s.csv"
    assert run(cli_settings, "export", "suggestions", "-o", str(out)) == 0
    assert out.read_text().startswith("created_at,name")


def test_backup_restore_and_purge(cli_settings, capsys, tmp_path, monkeypatch):
    run(cli_settings, "init")
    dest = tmp_path / "bk"
    assert run(cli_settings, "backup", "--dest-dir", str(dest)) == 0
    backup = next(dest.glob("poll-*.db"))
    monkeypatch.setattr(cli, "_port_in_use", lambda port: False)
    target = tmp_path / "restored.db"
    assert run(cli_settings, "restore", str(backup), "--dest", str(target)) == 0
    assert target.exists()
    assert run(cli_settings, "purge", "--before", "2026-01-01T00:00:00Z") == 0
    assert "purged identifiers from 0 ballots" in capsys.readouterr().out
    assert run(cli_settings, "purge", "--before", "2026-01-01T00:00:00") == 2


def test_restore_refuses_while_port_is_listening(cli_settings, tmp_path, capsys):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        result = run(
            cli_settings, "restore", "x.db", "--dest", str(tmp_path / "o.db"), "--port", str(port)
        )
    assert result == 1
    assert "refusing to restore" in capsys.readouterr().out


def test_install_launchd_dry_run_renders_valid_plists(cli_settings, capsys):
    assert run(cli_settings, "install-launchd", "--dry-run", "--label", "org.example.poll") == 0
    out = capsys.readouterr().out
    assert "==> org.example.poll.plist" in out
    assert "==> org.example.poll.backup.plist" in out
    files = cli.render_launchd("a&b", Path("/srv/x"), Path("/srv/x/.venv/bin/pollctl"), 8787, 600)
    app = plistlib.loads(files["a&b.plist"].encode())
    assert app["Label"] == "a&b"
    assert app["ProgramArguments"][:4] == [
        "/srv/x/.venv/bin/pollctl",
        "serve",
        "--host",
        "127.0.0.1",
    ]
    assert "--caffeinate" in app["ProgramArguments"]
    assert app["WorkingDirectory"] == "/srv/x"
    assert app["KeepAlive"] is True
    backup = plistlib.loads(files["a&b.backup.plist"].encode())
    assert backup["StartInterval"] == 600
    assert backup["ProgramArguments"][1] == "backup"


def test_install_launchd_writes_files(cli_settings, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "agents"
    assert run(cli_settings, "install-launchd", "--target", str(target)) == 0
    assert (target / "io.xpoll.test-poll.plist").exists()
    assert (tmp_path / "logs").is_dir()
    assert "launchctl bootstrap" in capsys.readouterr().out


def test_serve_invokes_uvicorn_safely(cli_settings, monkeypatch):
    calls = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.update(kw))
    monkeypatch.setattr(sys, "platform", "linux")
    assert run(cli_settings, "serve", "--port", "9999", "--caffeinate") == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9999
    assert calls["workers"] == 1
    assert calls["access_log"] is False
    assert calls["proxy_headers"] is False
    assert calls["factory"] is True


def test_shipped_example_passes_check_except_review(tmp_path, capsys):
    config = tmp_path / "poll.toml"
    shutil.copy(ROOT / "poll.example.toml", config)
    settings = Settings(_env_file=None, database_path=tmp_path / "p.db", poll_config=config)
    assert run(settings, "check") == 0


def test_backup_defaults_to_backup_dir_setting(cli_settings, tmp_path, capsys):
    settings = cli_settings.model_copy(update={"backup_dir": tmp_path / "configured"})
    run(settings, "init")
    assert run(settings, "backup") == 0
    assert list((tmp_path / "configured").glob("poll-*.db"))


def test_invalid_env_prints_friendly_error(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://poll.example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    assert cli.main(["check"]) == 2
    err = capsys.readouterr().err
    assert "configuration error (.env)" in err
    assert "Traceback" not in err


def test_review_marker_needs_colon(cli_settings, capsys):
    text = cli_settings.poll_config.read_text()
    cli_settings.poll_config.write_text("# Explains the REVIEW convention\n" + text)
    assert run(cli_settings, "check") == 0


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("# REVIEW: decide this", True),
        ('contact = "REVIEW: add an address"', True),
        ('# Resolve every "REVIEW:" line before opening', False),
        ("# Explains the REVIEW convention", False),
        ('name = "Review board"', False),
    ],
)
def test_review_marker_rule(tmp_path, line, flagged):
    path = tmp_path / "poll.toml"
    path.write_text(line + "\n")
    assert bool(cli._review_lines(path)) is flagged
