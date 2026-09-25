import sqlite3
from importlib import resources
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from xpoll import db
from xpoll.context import AppContext, get_conn, get_ctx
from xpoll.services.ballots import has_voted

router = APIRouter()
templates = Jinja2Templates(directory=str(resources.files("xpoll") / "templates"))

Ctx = Annotated[AppContext, Depends(get_ctx)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _base_context(ctx: AppContext, conn: sqlite3.Connection) -> dict:
    state = ctx.state(conn)
    visible = ctx.results_visible(state)
    return {
        "poll": ctx.config.poll,
        "operator": ctx.config.operator,
        "db_poll": db.get_poll(conn, ctx.poll_id),
        "state": state,
        "results_visible": visible,
        "snapshot": ctx.snapshot(conn).payload if visible else None,
    }


def _render(request: Request, ctx: AppContext, name: str, context: dict) -> HTMLResponse:
    response = templates.TemplateResponse(request, name, context)
    response.headers["Cache-Control"] = "no-store"
    ctx.identity.ensure_cookie(request, response)
    return response


@router.get("/", response_class=HTMLResponse)
def index(request: Request, ctx: Ctx, conn: Conn):
    context = _base_context(ctx, conn)
    voter_id = ctx.identity.voter_id(request)
    already_voted = bool(voter_id) and has_voted(
        conn, ctx.poll_id, ctx.identity.voter_hash(voter_id)
    )
    context.update(
        options=db.active_options(conn, ctx.poll_id),
        already_voted=already_voted,
        site_key=ctx.settings.turnstile_site_key,
        suggestions_enabled=ctx.config.suggestions.enabled
        and context["state"] in {"open", "scheduled"},
    )
    return _render(request, ctx, "index.html", context)


@router.get("/results", response_class=HTMLResponse)
def results_page(request: Request, ctx: Ctx, conn: Conn):
    return _render(request, ctx, "results.html", _base_context(ctx, conn))


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request, ctx: Ctx, conn: Conn):
    context = _base_context(ctx, conn)
    context["suggestions"] = ctx.config.suggestions
    return _render(request, ctx, "privacy.html", context)


@router.get("/theme.css")
def theme(ctx: Ctx) -> Response:
    css = f":root {{ --accent: {ctx.config.poll.accent_color}; }}\n"
    return Response(css, media_type="text/css", headers={"Cache-Control": "public, max-age=300"})
