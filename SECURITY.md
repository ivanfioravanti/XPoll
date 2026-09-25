# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Report a vulnerability** (Security Advisories) on this repository. Don't open a public issue. You should get an acknowledgement within a few days.

## Scope and threat model

XPoll is a low-stakes community survey. It is **not** an election system, and it can't stop a determined person from voting more than once (by clearing cookies or switching browsers, for example). The privacy page discloses this.

In scope:

- Leaking raw IP addresses, cookies, tokens or secrets, whether through responses, logs or exports.
- Bypassing the origin check, the body limit, Turnstile verification, the voting window or the one-ballot-per-cookie constraint.
- XSS, CSV/formula injection, and SQL injection.
- Weaknesses in the deployment templates, for example anything that exposes the origin beyond the tunnel.

## Hardening checklist for operators

- `APP_ENV=production`, with a random `VOTER_SECRET` and real Turnstile keys restricted to your hostname.
- Keep `.env` at mode 600. Never commit `.env`, `poll.toml`, databases, backups or tunnel credentials.
- Make the origin reachable only through the tunnel: bind to 127.0.0.1 natively, or publish no ports in Docker.
- Set `TRUST_CLOUDFLARE_HEADERS=true` only when that's true.
- Add a Cloudflare WAF rate-limit rule, and keep OS, Docker images and `cloudflared` up to date.
