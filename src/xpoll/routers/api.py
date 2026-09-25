import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from xpoll import db
from xpoll.context import AppContext, get_conn, get_ctx
from xpoll.services.ballots import BallotRejected, has_voted, record_ballot, validate_choices
from xpoll.services.suggestions import SuggestionRejected, store_suggestion, validate_suggestion

router = APIRouter(prefix="/api")

Ctx = Annotated[AppContext, Depends(get_ctx)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

STATE_MESSAGES = {
    "draft": ("poll-not-open", "This poll is not open yet."),
    "scheduled": ("poll-not-open", "Voting has not started yet."),
    "closed": ("poll-closed", "Voting has closed."),
}


class BallotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_ids: list[StrictInt] = Field(max_length=64)
    turnstile_token: str = Field(max_length=4096)


class SuggestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    url: str | None = Field(default=None, max_length=1000)
    notes: str = Field(default="", max_length=2000)
    turnstile_token: str = Field(max_length=4096)


def error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status, headers=headers)


def too_many(retry_after: int) -> JSONResponse:
    return error(
        429,
        "rate-limited",
        "Too many attempts from your network. Please wait and try again.",
        {"Retry-After": str(retry_after)},
    )


@router.post("/ballots", status_code=201)
def cast_ballot(payload: BallotIn, request: Request, ctx: Ctx, conn: Conn):
    state = ctx.state(conn)
    if state != "open":
        return error(403, *STATE_MESSAGES[state])
    poll = db.get_poll(conn, ctx.poll_id)
    try:
        option_ids = validate_choices(
            payload.option_ids,
            min_choices=poll["min_choices"],
            max_choices=poll["max_choices"],
            valid_ids=[o["id"] for o in db.active_options(conn, ctx.poll_id)],
        )
    except BallotRejected as exc:
        return error(exc.status, exc.code, exc.message)

    voter_id = ctx.identity.voter_id(request)
    if voter_id is None:
        return error(
            400, "cookies-required", "Please enable cookies for this site, reload, and try again."
        )
    network_hash = ctx.identity.network_hash(ctx.identity.client_ip(request))
    limiter = ctx.ballot_limiter
    retry_after = limiter.check(network_hash)
    if retry_after is not None:
        return too_many(retry_after)

    # Only failed attempts cost a token, so a busy shared network is never blocked by success.
    voter_hash = ctx.identity.voter_hash(voter_id)
    if has_voted(conn, ctx.poll_id, voter_hash):
        limiter.hit(network_hash)
        return error(409, "already-voted", "This browser has already voted.")
    if not ctx.verifier.verify(payload.turnstile_token, action="vote").ok:
        limiter.hit(network_hash)
        return error(403, "verification-failed", "Bot check failed. Please try again.")

    try:
        ballot_id, total = record_ballot(
            conn,
            poll_id=ctx.poll_id,
            option_ids=option_ids,
            voter_hash=voter_hash,
            network_hash=network_hash,
            now=ctx.now(),
        )
    except BallotRejected as exc:
        if exc.status == 409:
            limiter.hit(network_hash)
        return error(exc.status, exc.code, exc.message)
    ctx.results.invalidate()
    return {"ballot_id": ballot_id, "accepted_choices": len(option_ids), "total_ballots": total}


@router.get("/results")
def results(request: Request, ctx: Ctx, conn: Conn):
    if not ctx.results_visible(ctx.state(conn)):
        return error(403, "results-hidden", "Results are published when the poll closes.")
    snapshot = ctx.snapshot(conn)
    headers = {"ETag": snapshot.etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == snapshot.etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(snapshot.payload, headers=headers)


@router.post("/suggestions", status_code=201)
def suggest(payload: SuggestionIn, request: Request, ctx: Ctx, conn: Conn):
    settings = ctx.config.suggestions
    if not settings.enabled:
        return error(404, "not-found", "Suggestions are disabled.")
    if ctx.state(conn) not in {"open", "scheduled"}:
        return error(403, "poll-not-open", "Suggestions are not being accepted right now.")
    network_hash = ctx.identity.network_hash(ctx.identity.client_ip(request))
    retry_after = ctx.suggestion_limiter.check(network_hash)
    if retry_after is not None:
        return too_many(retry_after)
    ctx.suggestion_limiter.hit(network_hash)
    try:
        name, url, notes = validate_suggestion(
            payload.name, payload.url, payload.notes, allowed_hosts=settings.allowed_hosts
        )
    except SuggestionRejected as exc:
        return error(422, exc.code, exc.message)
    if not ctx.verifier.verify(payload.turnstile_token, action="suggest").ok:
        return error(403, "verification-failed", "Bot check failed. Please try again.")
    store_suggestion(
        conn,
        poll_id=ctx.poll_id,
        name=name,
        url=url,
        notes=notes,
        network_hash=network_hash,
        now=ctx.now(),
    )
    return {"status": "received"}
