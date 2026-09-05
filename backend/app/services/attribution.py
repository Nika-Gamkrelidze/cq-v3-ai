"""Who a Claude call belongs to, without threading two arguments through fifteen layers.

`llm_usage` records an `actor` and a `job_id` so an operator can answer the two questions an
invoice argument actually turns on: *which of their people* ran this, and *which recording* it
belongs to. The problem is where those values live. The actor is known at the auth boundary;
the job id is known when the recording row is created; and the Claude call happens four or
five frames deeper, inside services (`scoring`, `factcheck`, `summarise`, `chat`…) that have
no business knowing about HTTP requests at all.

Passing them down explicitly would mean adding two parameters to every one of those service
functions AND to everything that calls them — and any call site that forgot would silently
record an unattributed row that looks exactly like a correct one. So this is a `ContextVar`
instead: set once per request, read once inside `llm._record`, invisible to everything in
between. That is precisely the job contextvars exist for, and asyncio propagates them into
tasks spawned within the same context, which is what the fire-and-forget usage write is.

SCOPE AND SAFETY. A ContextVar set inside a request is visible only to that request's task
tree: concurrent requests cannot see each other's actor, and a value cannot leak from one
request to the next, because Starlette runs each in its own context. The default is None,
which records as unattributed — the honest answer when nobody set one (a startup migration, a
background worker, a script).
"""
from contextvars import ContextVar

# Deliberately module-private with function accessors, so a caller cannot hold a reference to
# the ContextVar and set it from outside a request context by accident.
_actor: ContextVar[str | None] = ContextVar("cq_llm_actor", default=None)
_job_id: ContextVar[str | None] = ContextVar("cq_llm_job_id", default=None)


def set_actor(actor: str | None) -> None:
    """Called once per request from `resolve_principal`, for every principal kind."""
    _actor.set(actor or None)


def set_job(job_id) -> None:
    """Called when a recording's row exists, so the AI work that follows is attributed to it.

    Takes anything stringable (asyncpg hands back a UUID object) or None to clear.
    """
    _job_id.set(str(job_id) if job_id else None)


def current() -> tuple[str | None, str | None]:
    """`(actor, job_id)` for the request in flight, either of which may be None."""
    return _actor.get(), _job_id.get()
