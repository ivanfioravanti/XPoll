import logging
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from xpoll import db
from xpoll.config import Settings, get_settings
from xpoll.context import AppContext
from xpoll.identity import Identity
from xpoll.poll_config import PollConfig, load_poll_config
from xpoll.ratelimit import RateLimiter
from xpoll.routers import api, pages
from xpoll.security import AccessLogMiddleware, SecurityMiddleware
from xpoll.services.results import ResultsCache
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
        ),
        verifier=verifier,
        ballot_limiter=RateLimiter(20, 600),
        suggestion_limiter=RateLimiter(5, 3600),
        results=ResultsCache(ttl=2.0),
        now=now,
    )

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo request input (it may contain tokens) back to the client.
        return JSONResponse(
            {"detail": "The request was not valid.", "code": "invalid-request"},
            status_code=422,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api.router)
    app.include_router(pages.router)
    static_dir = resources.files("xpoll") / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.add_middleware(
        SecurityMiddleware, allowed_origin=settings.base_origin, hsts=settings.is_production
    )
    app.add_middleware(AccessLogMiddleware)
    logger.info("xpoll ready: poll=%s env=%s", config.poll.slug, settings.app_env)
    return app
