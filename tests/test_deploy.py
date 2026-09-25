from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_publishes_no_host_ports():
    text = (ROOT / "deploy" / "compose.yaml").read_text()
    assert "ports:" not in text
    assert "TRUST_CLOUDFLARE_HEADERS" in text


def test_container_runs_unprivileged_single_worker():
    text = (ROOT / "deploy" / "Dockerfile").read_text()
    assert "USER xpoll" in text
    assert '"pollctl", "serve"' in text


def test_secrets_and_state_are_git_ignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    for entry in (".env", "data/", "backups/", "logs/", "poll.toml"):
        assert entry in ignored
