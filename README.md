# XPoll

A small, self-hosted "pick up to N" community poll with live results. It's built to run from a home machine behind a Cloudflare Tunnel and be shared on social media.

- **One poll per instance, fully described by a `poll.toml`.** You set the title, question, options, min/max choices, voting window and privacy text.
- **Anonymous and low-friction.** There are no accounts. A signed first-party cookie prevents casual double voting, [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/) blocks bots, and network-level rate limits use HMACs. **No raw IP address is ever stored or logged.**
- **Live, transparent results.** Results are rendered on the server and then refresh every 10 s. Percentages are the share of ballots, so they can add up to more than 100%. You can also hide results until the poll closes.
- **Boring, robust stack.** It's FastAPI with SQLite in WAL mode behind a single worker, vanilla JS, no build step and no inline scripts. The CSP is strict and allows only Turnstile.
- **No web admin.** Everything operational goes through the `pollctl` CLI on the host, so there's no admin surface on the internet.

Non-goals: multiple polls in one UI, a web admin, accounts or OAuth, ranked choice, i18n, horizontal scaling. XPoll is a low-stakes community survey tool, not an election system.

The reference deployment is the **MLX engine community poll** ([`examples/mlx-engines.toml`](examples/mlx-engines.toml)).

## Quick start (local)

```bash
uv sync
cp poll.example.toml poll.toml   # edit it
uv run pollctl check             # validate the config
uv run pollctl init              # create data/poll.db, poll starts as "draft"
uv run pollctl status open
uv run pollctl serve             # http://127.0.0.1:8787
```

Development defaults to Cloudflare's always-pass Turnstile **test keys**, so you can vote locally without an account. Production refuses to start with test keys.

## Configuration

### `poll.toml`

See [`poll.example.toml`](poll.example.toml) for a complete, commented example.

| Section | Keys |
|---|---|
| `[poll]` | `slug` (a stable ID; changing it starts a new poll), `title`, `question`, `description` (the inclusion rule), `min_choices`, `max_choices`, `opens_at`/`closes_at` (optional, must include a UTC offset), `results_visibility` (`live` or `after_close`), `option_noun`, `accent_color` |
| `[operator]` | `name`, `contact`, `retention`, `disclaimer`. These appear on the privacy page. |
| `[suggestions]` | `enabled`, `allowed_hosts` (default `["github.com"]`) |
| `[[options]]` | `slug`, `name`, `url` (https), `description`, `active` |

On startup the config is synced into the database:

- Options removed from the file are **deactivated, not deleted**, so their historical votes stay in the results.
- Status (`draft`/`open`/`closed`) lives only in the database and changes only through `pollctl status`.
- Voting is accepted only when the status is `open` **and** the current time is inside the configured window, so the poll closes on time even if nobody is watching.

Any line containing `REVIEW` marks an unresolved decision. `pollctl check` lists these lines, and `pollctl status open` refuses to run while any remain (pass `--force` to override).

### Environment (`.env`)

See [`.env.example`](.env.example).

| Variable | Notes |
|---|---|
| `APP_ENV` | `development` or `production`. Production requires strong secrets, real Turnstile keys and an https `APP_BASE_URL`. |
| `APP_BASE_URL` | Public origin. Write requests are accepted only from this origin. |
| `VOTER_SECRET` | Signs cookies and keys the HMACs. At least 32 random characters. Rotating it lets every browser vote again. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | From the Cloudflare dashboard. Restrict the widget to your hostname. |
| `TRUST_CLOUDFLARE_HEADERS` | `true` only if the app is reachable **exclusively** through Cloudflare. It makes the app read the client IP from `CF-Connecting-IP`. |
| `DATABASE_PATH`, `POLL_CONFIG`, `BACKUP_DIR` | File locations. |
| `TUNNEL_TOKEN` | Docker only. Token for the remotely managed tunnel. |

Generate a secret with `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Deploy

Both paths publish the app only through a Cloudflare Tunnel. No router port forwarding is needed, and the app is never reachable from your LAN.

### Option A: Docker compose (Linux or macOS)

This runs on Linux natively, and on macOS with Docker Desktop or OrbStack.

1. In the Cloudflare dashboard, go to **Zero Trust → Networks → Tunnels** and create a tunnel. Add a public hostname whose service is `http://app:8787`. Copy the tunnel token into `.env` as `TUNNEL_TOKEN`.
2. Create `.env` (from `.env.example`) and `poll.toml` in the repository root.
3. Run:

   ```bash
   docker compose -f deploy/compose.yaml up -d --build
   docker compose -f deploy/compose.yaml exec app pollctl check
   docker compose -f deploy/compose.yaml exec app pollctl status open
   ```

The stack has two services, `app` and `cloudflared`. **No ports are published on the host.**

- The app container runs as a non-root user with a read-only filesystem and no Linux capabilities.
- SQLite lives on a named volume, not a bind mount, because WAL mode is unreliable on macOS file sharing.
- Take backups with `docker compose -f deploy/compose.yaml exec app pollctl backup`. They go to the `xpoll-backups` volume.

To test locally without a tunnel, run `docker compose -f deploy/compose.yaml up app`.

### Option B: native macOS (launchd)

1. Set up the instance:

   ```bash
   uv sync --frozen
   cp .env.example .env && chmod 600 .env    # fill in secrets
   uv run pollctl init
   uv run pollctl install-launchd --dry-run  # review the generated plists
   uv run pollctl install-launchd            # writes ~/Library/LaunchAgents/*.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.xpoll.<slug>.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.xpoll.<slug>.backup.plist
   lsof -nP -iTCP:8787 -sTCP:LISTEN          # must show 127.0.0.1:8787 only
   ```

   The app runs `.venv/bin/pollctl serve`, which is one uvicorn worker bound to `127.0.0.1` with the access log off. It holds a `caffeinate -s` assertion so the Mac doesn't sleep while on AC power. A second job runs a verified backup every hour.

2. Create and route the tunnel. Use [`deploy/cloudflared-config.example.yml`](deploy/cloudflared-config.example.yml) as `~/.cloudflared/config.yml`:

   ```bash
   brew install cloudflared
   cloudflared tunnel login
   cloudflared tunnel create xpoll
   cloudflared tunnel ingress validate
   cloudflared tunnel route dns xpoll <poll-hostname>
   cloudflared tunnel run xpoll          # foreground test first
   ```

3. Once the foreground test works, install `cloudflared` as a service. Cloudflare documents a login-time mode and a boot-time mode, so pick one deliberately.

> **FileVault caveat:** LaunchAgents start only after a user logs in. With FileVault enabled, a power cut or reboot leaves the poll offline until someone logs in. Set up an external uptime check.

### Cloudflare hardening (both options)

- Restrict the Turnstile widget's hostnames to your poll hostname. The server also checks the `hostname` and `action` Cloudflare returns.
- Add a WAF rate-limiting rule for `POST /api/*` as an outer layer. The in-app limiter is per process and resets on restart.
- Turn on Bot Fight Mode if your audience tolerates it. Test with common browsers and ad blockers first.

## Operations (`pollctl`)

| Command | Purpose |
|---|---|
| `pollctl check` | Validate `poll.toml` and list `REVIEW` markers |
| `pollctl init` | Create or migrate the database and sync the config |
| `pollctl status [draft\|open\|closed]` | Show or change the status (ballot count, effective state) |
| `pollctl export results\|suggestions [-o file]` | Aggregate CSV with spreadsheet-safe escaping. Never includes hashes. |
| `pollctl backup [--dest-dir D] [--keep N]` | Online SQLite backup that is integrity-checked, atomically renamed and pruned |
| `pollctl restore FILE --dest PATH [--force]` | Restore to an explicit path. Refuses while the app is listening. |
| `pollctl purge --before 2026-11-01T00:00:00Z` | Drop voter and network identifiers. Aggregates are unchanged. |
| `pollctl serve` | Run the app (single worker, no access log, no proxy-header trust) |
| `pollctl install-launchd` | Generate macOS LaunchAgent plists |

### Runbook

- **Launch:**
  1. Resolve every `REVIEW` marker.
  2. Confirm a backup exists.
  3. Check `/healthz` locally, then over HTTPS.
  4. Confirm the Turnstile widget renders on the public site.
  5. Run `pollctl status open`.
- **Close:**
  1. Run `pollctl status closed`. Results stay readable.
  2. Export results and suggestions, and take a final backup.
  3. Publish the methodology and the ballot count.
  4. Apply retention with `pollctl purge`.
- **Suspected bot burst:** Close the poll first and preserve the database and logs. Inspect only aggregate and HMAC evidence, then decide whether to reopen.
- **Database problem:**
  1. Stop the app.
  2. Restore the latest backup to a *new* path.
  3. Check its integrity.
  4. Point `DATABASE_PATH` at it.
- **Secret exposure:** Close the poll, rotate the secret, restart, and document the impact.

## Privacy model

- **Cookie:** `xpoll_voter` holds a random ID signed with `VOTER_SECRET`. The database stores only `HMAC(secret, poll + id)`.
- **Network key:** `HMAC(secret, poll + address)`, where the address is reduced to its /64 prefix for IPv6. It's used only for rate limiting and never for ballot uniqueness, so a shared network is never hard-blocked.
- **Logs:** One JSON line per request with the request ID, method, route template, status, duration and error class. Logs never include IPs, cookies, query strings, bodies or tokens.
- **Limits:** Clearing cookies or switching browsers makes it possible to vote again. The privacy page says so.

## Development

```bash
uv run ruff format --check . && uv run ruff check .
uv run pytest --cov=xpoll --cov-fail-under=90
```

## License

[MIT](LICENSE)
