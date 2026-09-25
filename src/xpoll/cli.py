"""pollctl: operate an XPoll instance from the host. There is deliberately no HTTP admin API."""

import argparse
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from jinja2 import Environment

from xpoll import db
from xpoll.config import Settings, get_settings
from xpoll.poll_config import PollConfig, load_poll_config
from xpoll.services import backup, export
from xpoll.services.polls import voting_state

REVIEW_MARKER = "REVIEW"
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "stream": "ext://sys.stdout",
        }
    },
    "loggers": {
        "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "xpoll": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}


def _now() -> datetime:
    return datetime.now(UTC)


def _open_db(settings: Settings, config: PollConfig):
    conn = db.connect(settings.database_path)
    db.migrate(conn)
    poll_id = db.sync_poll(conn, config, _now())
    return conn, poll_id


def _write(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        sys.stdout.write(text)


def cmd_check(args, settings: Settings) -> int:
    config = load_poll_config(settings.poll_config)
    print(f"{settings.poll_config}: ok")
    print(f"  poll: {config.poll.slug} ({config.poll.title})")
    print(f"  choices: {config.poll.min_choices}-{config.poll.max_choices}")
    print(f"  options: {len(config.active_options)} active / {len(config.options)} total")
    markers = _review_lines(settings.poll_config)
    for number, line in markers:
        print(f"  unresolved {REVIEW_MARKER} (line {number}): {line}")
    return 1 if markers else 0


def _review_lines(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(n, line.strip()) for n, line in enumerate(lines, 1) if REVIEW_MARKER in line]


def cmd_init(args, settings: Settings) -> int:
    config = load_poll_config(settings.poll_config)
    conn, poll_id = _open_db(settings, config)
    options = len(db.active_options(conn, poll_id))
    status = db.get_poll(conn, poll_id)["status"]
    conn.close()
    print(
        f"database {settings.database_path}: poll {config.poll.slug} [{status}], {options} options"
    )
    return 0


def cmd_status(args, settings: Settings) -> int:
    config = load_poll_config(settings.poll_config)
    conn, poll_id = _open_db(settings, config)
    try:
        if args.new_status:
            if args.new_status == "open" and not args.force and _review_lines(settings.poll_config):
                print(
                    f"refusing to open: {settings.poll_config} has unresolved {REVIEW_MARKER} "
                    "markers (see `pollctl check`, or pass --force)",
                    file=sys.stderr,
                )
                return 1
            db.set_status(conn, poll_id, args.new_status)
        poll = db.get_poll(conn, poll_id)
        ballots = conn.execute(
            "SELECT COUNT(*) FROM ballots WHERE poll_id = ?", (poll_id,)
        ).fetchone()[0]
        suggestions = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE poll_id = ? AND status = 'pending'",
            (poll_id,),
        ).fetchone()[0]
        print(f"poll: {config.poll.slug}")
        print(f"status: {poll['status']} (effective: {voting_state(poll, _now())})")
        print(f"window: {poll['opens_at'] or '-'} -> {poll['closes_at'] or '-'}")
        print(f"ballots: {ballots}")
        print(f"pending suggestions: {suggestions}")
    finally:
        conn.close()
    return 0


def cmd_export(args, settings: Settings) -> int:
    config = load_poll_config(settings.poll_config)
    conn, poll_id = _open_db(settings, config)
    try:
        if args.what == "results":
            text = export.results_csv(conn, poll_id, config.poll.slug)
        else:
            text = export.suggestions_csv(conn, poll_id)
    finally:
        conn.close()
    _write(text, args.output)
    return 0


def cmd_backup(args, settings: Settings) -> int:
    path = backup.backup_database(
        settings.database_path, Path(args.dest_dir), now=_now(), keep=args.keep
    )
    print(f"backup ok: {path}")
    return 0


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def cmd_restore(args, settings: Settings) -> int:
    if _port_in_use(args.port):
        print(f"refusing to restore: something is listening on 127.0.0.1:{args.port}; stop the app")
        return 1
    backup.restore_database(Path(args.backup), Path(args.dest), force=args.force)
    print(f"restored {args.backup} -> {args.dest} (integrity ok)")
    return 0


def cmd_purge(args, settings: Settings) -> int:
    before = datetime.fromisoformat(args.before)
    if before.utcoffset() is None:
        print("--before must include a UTC offset, e.g. 2026-11-01T00:00:00Z", file=sys.stderr)
        return 2
    config = load_poll_config(settings.poll_config)
    conn, poll_id = _open_db(settings, config)
    try:
        ballots, suggestions = backup.purge_identifiers(conn, poll_id, before)
    finally:
        conn.close()
    print(f"purged identifiers from {ballots} ballots and {suggestions} suggestions")
    return 0


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    load_poll_config(settings.poll_config)  # fail fast before binding
    if args.caffeinate and sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())])  # noqa: S603
    uvicorn.run(
        "xpoll.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,  # uvicorn's access log includes client IPs
        proxy_headers=False,  # client IP comes only from CF-Connecting-IP when trusted
        server_header=False,
        log_config=LOG_CONFIG,
    )
    return 0


def render_launchd(label: str, workdir: Path, pollctl: Path, port: int, interval: int) -> dict:
    env = Environment(autoescape=True, keep_trailing_newline=True)
    folder = resources.files("xpoll") / "deploy_templates"
    values = {
        "label": label,
        "workdir": str(workdir),
        "pollctl": str(pollctl),
        "port": port,
        "interval": interval,
        "logdir": str(workdir / "logs"),
    }
    return {
        f"{label}.plist": env.from_string((folder / "app.plist.j2").read_text()).render(values),
        f"{label}.backup.plist": env.from_string((folder / "backup.plist.j2").read_text()).render(
            values
        ),
    }


def cmd_install_launchd(args, settings: Settings) -> int:
    config = load_poll_config(settings.poll_config)
    label = args.label or f"io.xpoll.{config.poll.slug}"
    workdir = Path.cwd().resolve()
    pollctl = Path(sys.executable).parent / "pollctl"
    files = render_launchd(label, workdir, pollctl, args.port, args.backup_interval)
    if args.dry_run:
        for name, content in files.items():
            print(f"==> {name}\n{content}")
        return 0
    (workdir / "logs").mkdir(mode=0o700, exist_ok=True)
    target = Path(args.target).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = target / name
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")
    print("\nReview the files, then load them with:")
    for name in files:
        print(f"  launchctl bootstrap gui/$(id -u) {target / name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pollctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="validate poll.toml and list unresolved REVIEW markers")
    sub.add_parser("init", help="create/migrate the database and sync poll.toml into it")
    sub.add_parser("sync", help="alias of init")

    status = sub.add_parser("status", help="show or change poll status")
    status.add_argument("new_status", nargs="?", choices=["draft", "open", "closed"])
    status.add_argument("--force", action="store_true", help="open despite REVIEW markers")

    exp = sub.add_parser("export", help="export aggregate results or suggestions as CSV")
    exp.add_argument("what", choices=["results", "suggestions"])
    exp.add_argument("-o", "--output")

    bak = sub.add_parser("backup", help="verified online backup of the database")
    bak.add_argument("--dest-dir", default="backups")
    bak.add_argument("--keep", type=int, default=48)

    res = sub.add_parser("restore", help="restore a backup to an explicit path (app stopped)")
    res.add_argument("backup")
    res.add_argument("--dest", required=True)
    res.add_argument("--force", action="store_true", help="overwrite an existing database")
    res.add_argument("--port", type=int, default=8787)

    purge = sub.add_parser("purge", help="drop voter/network identifiers older than a date")
    purge.add_argument("--before", required=True, help="ISO timestamp with offset")

    serve = sub.add_parser("serve", help="run the app (single worker, no access log)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--caffeinate", action="store_true", help="keep macOS awake while serving")

    launchd = sub.add_parser("install-launchd", help="write macOS LaunchAgent plists")
    launchd.add_argument("--label")
    launchd.add_argument("--port", type=int, default=8787)
    launchd.add_argument("--backup-interval", type=int, default=3600)
    launchd.add_argument("--target", default="~/Library/LaunchAgents")
    launchd.add_argument("--dry-run", action="store_true")
    return parser


COMMANDS = {
    "check": cmd_check,
    "init": cmd_init,
    "sync": cmd_init,
    "status": cmd_status,
    "export": cmd_export,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "purge": cmd_purge,
    "serve": cmd_serve,
    "install-launchd": cmd_install_launchd,
}


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args, settings or get_settings())


if __name__ == "__main__":
    raise SystemExit(main())
