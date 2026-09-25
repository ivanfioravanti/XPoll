import logging
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from xpoll import db
from xpoll.assets import VersionedStaticFiles, asset_versions
from xpoll.config import Settings, get_settings
from xpoll.context import AppContext
from xpoll.identity import Identity
from xpoll.poll_config import PollConfig, load_poll_config
from xpoll.ratelimit import RateLimiter
from xpoll.routers import api, pages
from xpoll.security import AccessLogMiddleware, SecurityMiddleware
from xpoll.services.results import ResultsCache
from xpoll.social import load_social_image
from xpoll.turnstile import CloudflareTurnstile, TurnstileVerifier

logger = logging.getLogger("xpoll")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    config: PollConfig | None = None,
    verifier: TurnstileVerifier | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    settings = settings or get_settings()
    config = config or load_poll_config(settings.poll_config)
    conn = db.connect(settings.database_path)
    try:
        db.migrate(conn)
        poll_id = db.sync_poll(conn, config, now())
    finally:
        conn.close()

    if verifier is None:
        verifier = CloudflareTurnstile(
            settings.turnstile_secret_key.get_secret_value(),
            expected_hostname=settings.base_hostname,
            # Cloudflare's test secrets return placeholder claims, so only real keys are checked.
            check_claims=not settings.uses_turnstile_test_keys,
        )

    static_dir = Path(str(resources.files("xpoll") / "static"))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = AppContext(
        settings=settings,
        config=config,
        poll_id=poll_id,
        identity=Identity(
            settings.voter_secret.get_secret_value(),
            config.poll.slug,
            trust_cloudflare=settings.trust_cloudflare_headers,
            secure=settings.is_production,
            cookie_path=settings.base_path or "/",
        ),
        verifier=verifier,
        ballot_limiter=RateLimiter(20, 600),
        suggestion_limiter=RateLimiter(5, 3600),
        results=ResultsCache(ttl=2.0),
        now=now,
        asset_versions=asset_versions(static_dir),
        social_image=load_social_image(config.poll.social_image, settings.poll_config.parent),
    )

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo request input (it may contain tokens) back to the client.
        return JSONResponse(
            {"detail": "The request was not valid.", "code": "invalid-request"},
            status_code=422,
        )

    app.add_api_route("/healthz", _healthz)
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", VersionedStaticFiles(directory=str(static_dir)), name="static")

    root = app
    if settings.base_path:
        # The tunnel forwards /<prefix>/... unchanged, so the whole app lives under the prefix.
        root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        root.state.ctx = app.state.ctx
        root.add_api_route("/healthz", _healthz)

        @root.get(settings.base_path, include_in_schema=False)
        def _to_prefix() -> RedirectResponse:
            # Relative Location: behind the tunnel the app only sees http, never the public https.
            return RedirectResponse(settings.base_path + "/", status_code=308)

        root.mount(settings.base_path, app)

    root.add_middleware(
        SecurityMiddleware, allowed_origin=settings.base_origin, hsts=settings.is_production
    )
    root.add_middleware(AccessLogMiddleware)
    logger.info(
        "xpoll ready: poll=%s env=%s path=%s",
        config.poll.slug,
        settings.app_env,
        settings.base_path or "/",
    )
    return root


def _healthz() -> dict[str, str]:
    return {"status": "ok"}
