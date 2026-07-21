"""KB curation review queue — ADR-001 endpoint 11 (`/v1/curation/*`), P2.

This is Stage 5 of the curation loop ("don't drown the reviewer") and the *front half* of
Stage 6. Everything expensive — harvesting, clustering, the one Claude call per cluster, and
the actual KB write — happens in `cq-worker`. This file only lets a human read the queue and
record a decision.

Three things here are load-bearing and easy to undo by accident:

1. **Accepting does not apply.** `POST /proposals/{id}/accept` flips the row to `accepted` and
   returns. The worker picks it up and calls `services/curation/apply.py`, which calls
   `kb_ingest.ingest_document` (re-chunk + re-embed) — seconds to minutes of embedder work per
   document. Doing that inside the request would block one of ten pool connections behind an
   embedding round-trip, in a single-uvicorn-worker API, and would 504 behind nginx's
   `proxy_read_timeout 300s`. `POST /admin/curation/run` is 202 for the same reason, only more
   so: a real mining run is far longer than any HTTP timeout.
2. **Accept-with-edits is the SAME endpoint.** A body carrying `content` means "accept this, but
   with my text". There is deliberately no second verb: two endpoints would mean two paths into
   the KB write, and the edited one is the path a reviewer actually uses.
3. **Curation is never reachable with an integration credential.** See `_deny_integration`.

Errors keep FastAPI's exact `{"detail": "<string>"}` shape (brand.js's `CQ.readResp` requires
`detail` to be a string); a machine-readable `code` is only ever a SIBLING key.
"""
import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..db import pool
from ..services.auth import Principal, resolve_principal
from ..services.curation import apply as curation_apply

log = logging.getLogger("cq")

router = APIRouter(tags=["curation"])

# ---- tunables --------------------------------------------------------------
MAX_PAGE = 100                 # a page of review cards; the ADR's open cap is 10 anyway
MAX_BULK_IDS = 50              # a bulk action is a reviewer clearing a screen, not a script
DECLINE_SUPPRESS_DAYS = 90     # ADR: a decline reflects the KB as it was, so it expires
TARGET_EXCERPT_CHARS = 4_000   # enough for the UI's character-level diff, not a whole book

# Whitelisted sort orders. Never interpolate a caller-supplied string into ORDER BY.
_SORTS = {
    "priority": "priority DESC, created_at DESC",
    "recent": "created_at DESC",
    "occurrences": "occurrences DESC, priority DESC",
}


def _err(status: int, detail: str, code: str) -> JSONResponse:
    """FastAPI's error body plus a machine key as a SIBLING (never nested in `detail`)."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _deny_integration(p: Principal) -> None:
    """Curation is a human review action. A chat integration credential is NEVER one.

    `Principal.is_tenant` is already False for `kind == "integration"`, so the gates below would
    reject it anyway — this is stated explicitly because the predictable scope creep is "the chat
    site knows about the gaps, just let it accept proposals directly". That would turn a scoped,
    machine-held key into a fully automated, unreviewed writer to another company's knowledge
    base, which is the one thing the whole accept/decline design exists to prevent. If this ever
    needs to be automated, it needs a new principal kind and its own ADR — not a widened scope.
    """
    if p.kind == "integration":
        raise HTTPException(status_code=403,
                            detail="Curation review requires a tenant admin login; "
                                   "integration credentials cannot review or apply proposals.")


class Scope:
    """The resolved tenant plus who to record as the reviewer."""

    __slots__ = ("client_id", "reviewer")

    def __init__(self, client_id: str, reviewer: str):
        self.client_id = client_id
        self.reviewer = reviewer


# ---- superadmin, tenant-parameterized (mirrors routers/scoring.py::_scope) ----
async def _scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> Scope:
    _deny_integration(principal)
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    try:
        uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return Scope(tenant_id, "superadmin")


# ---- tenant self-serve -------------------------------------------------------
def _tenant_read(principal: Principal = Depends(resolve_principal)) -> Scope:
    """Reading the queue: any authenticated tenant principal (login or API key)."""
    _deny_integration(principal)
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    return Scope(principal.client_id, f"tenant:{principal.user_id or principal.role or 'user'}")


def _tenant_write(principal: Principal = Depends(resolve_principal)) -> Scope:
    """Deciding a proposal: an interactive tenant ADMIN session only.

    Two narrowings beyond the read gate, both deliberate:
      * `via == "token"` — a decision is an accountable act by a person, and only the login token
        carries a `user_id` to write into `reviewed_by`. `clients.api_key` is plaintext,
        non-expiring and full-privilege (see ADR-001's constraints table); a server-to-server key
        silently approving KB rewrites is the same failure mode as an integration key doing it.
      * `role == "owner"` — a member can watch the queue; the tenant admin owns the KB.
    """
    _deny_integration(principal)
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login required")
    if principal.via != "token" or principal.role != "owner":
        raise HTTPException(status_code=403,
                            detail="Reviewing KB proposals requires a tenant admin (owner) login.")
    return Scope(principal.client_id, f"tenant:{principal.user_id or 'owner'}")


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #
class AcceptBody(BaseModel):
    # All optional: an empty body is a plain accept. Any of these present == accept-with-edits,
    # and the edited values REPLACE what the model proposed before the worker applies it.
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None


class DeclineBody(BaseModel):
    # Required, and not just for the audit trail: it is the human-readable half of the
    # suppression row that keeps this cluster out of the queue for the next 90 days.
    reason: str = Field(min_length=1)


class BulkBody(BaseModel):
    ids: list[str]
    action: str                      # accept | decline
    reason: str | None = None        # required when action == "decline"


class RunBody(BaseModel):
    client_id: str | None = None     # omitted == every active tenant
    since: str | None = None         # YYYY-MM-DD; selects the tenant-day to (re)mine


# --------------------------------------------------------------------------- #
# Row shaping
# --------------------------------------------------------------------------- #
_LIST_COLS = """
    id, client_id, run_id, op, state, signal, cluster_key, cluster_medoid, title,
    rationale, target_document_id, target_chunk_id, suggested_tags, occurrences,
    distinct_sources, confidence, priority, risk, window_start, window_end,
    reviewed_by, reviewed_at, decline_reason, applied_document_id, applied_at,
    apply_error, created_at, updated_at
"""


def _proposal(row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for k in ("id", "client_id", "run_id", "target_document_id", "target_chunk_id",
              "applied_document_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    d["suggested_tags"] = list(d.get("suggested_tags") or [])
    return d


def _evidence(row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for k in ("id", "source_id", "conversation_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


# --------------------------------------------------------------------------- #
# Shared implementations (one per verb; the two surfaces are thin wrappers)
# --------------------------------------------------------------------------- #
async def _list(tid: str, state: str, op: str | None, sort: str, limit: int, offset: int):
    if sort not in _SORTS:
        return _err(400, f"Unknown sort '{sort}'.", "bad_sort")
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))

    where = ["client_id = $1"]
    args: list = [tid]
    if state and state != "all":
        args.append(state)
        where.append(f"state = ${len(args)}")
    if op:
        args.append(op)
        where.append(f"op = ${len(args)}")
    clause = " AND ".join(where)

    async with pool().acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM curation_proposals WHERE {clause}", *args)
        rows = await conn.fetch(
            f"SELECT {_LIST_COLS} FROM curation_proposals WHERE {clause} "
            f"ORDER BY {_SORTS[sort]} LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, limit, offset)
    return {"items": [_proposal(r) for r in rows], "total": total,
            "limit": limit, "offset": offset, "client_id": tid}


async def _target_excerpt(conn, tid: str, row) -> dict | None:
    """What the proposal wants to change, as it stands RIGHT NOW.

    The UI diffs this against `proposed_content`. It is read at view time rather than snapshotted
    at mine time on purpose: the document may have been edited since the card was raised, and a
    reviewer approving a diff against stale text would silently revert somebody else's edit.
    Both reads are client_id-scoped, so a stale/foreign target id resolves to nothing.
    """
    if row["target_chunk_id"]:
        c = await conn.fetchrow(
            "SELECT id, document_id, chunk_index, left(content, $3) AS content "
            "FROM kb_chunks WHERE id = $1 AND client_id = $2",
            row["target_chunk_id"], tid, TARGET_EXCERPT_CHARS)
        if c:
            return {"kind": "chunk", "chunk_id": str(c["id"]),
                    "document_id": str(c["document_id"]), "chunk_index": c["chunk_index"],
                    "content": c["content"]}
    if row["target_document_id"]:
        d = await conn.fetchrow(
            "SELECT id, title, doc_type, status, visibility, chunk_count, "
            "       left(content_text, $3) AS content "
            "FROM kb_documents WHERE id = $1 AND client_id = $2",
            row["target_document_id"], tid, TARGET_EXCERPT_CHARS)
        if d:
            return {"kind": "document", "document_id": str(d["id"]), "title": d["title"],
                    "doc_type": d["doc_type"], "status": d["status"],
                    "visibility": d["visibility"], "chunk_count": d["chunk_count"],
                    "content": d["content"]}
    return None


async def _get(tid: str, pid: str):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_LIST_COLS}, proposed_content FROM curation_proposals "
            "WHERE id = $1 AND client_id = $2", pid, tid)
        if not row:
            return _err(404, "Proposal not found.", "not_found")
        ev = await conn.fetch(
            "SELECT id, signal, source_kind, source_id, conversation_id, channel, locale, "
            "       question, answer, occurred_at, source_client_id = $2 AS same_tenant "
            "FROM curation_evidence WHERE proposal_id = $1 AND client_id = $2 "
            "ORDER BY occurred_at DESC NULLS LAST", pid, tid)
        target = await _target_excerpt(conn, tid, row)

    out = _proposal(row)
    out["evidence"] = [_evidence(e) for e in ev]
    out["target"] = target
    return out


async def _accept(tid: str, pid: str, body: AcceptBody, reviewer: str):
    """Record the decision only. The worker does the KB write out of band."""
    edits = {}
    if body.content is not None:
        if not body.content.strip():
            return _err(400, "Edited content cannot be empty.", "empty_content")
        edits["proposed_content"] = body.content.strip()
    if body.title is not None:
        edits["title"] = body.title.strip()
    if body.tags is not None:
        edits["suggested_tags"] = [t.strip() for t in body.tags if str(t).strip()]

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, state, op, cluster_key FROM curation_proposals "
                "WHERE id = $1 AND client_id = $2 FOR UPDATE", pid, tid)
            if not row:
                return _err(404, "Proposal not found.", "not_found")
            if row["state"] != "pending":
                return _err(409, f"Proposal is already {row['state']}.", "not_pending")

            # The cross-tenant guard curation.sql's `source_client_id` exists for, checked at the
            # one moment a human is looking. A single write-side misattribution would otherwise be
            # laundered into a permanent, human-APPROVED contamination of this tenant's KB.
            foreign = await conn.fetchval(
                "SELECT count(*) FROM curation_evidence "
                "WHERE proposal_id = $1 AND source_client_id <> $2", pid, tid)
            if foreign:
                log.error("curation: proposal %s has %s evidence row(s) from another tenant — "
                          "refusing accept", pid, foreign)
                return _err(409, "This proposal cites evidence from another tenant and cannot "
                                 "be applied. Please report it.", "cross_tenant_evidence")

            sets = ["state = 'accepted'", "reviewed_by = $3", "reviewed_at = now()",
                    "updated_at = now()", "apply_error = NULL"]
            args: list = [pid, tid, reviewer]
            for col, val in edits.items():
                args.append(val)
                sets.append(f"{col} = ${len(args)}")
            out = await conn.fetchrow(
                f"UPDATE curation_proposals SET {', '.join(sets)} "
                f"WHERE id = $1 AND client_id = $2 RETURNING {_LIST_COLS}", *args)

    d = _proposal(out)
    d["edited"] = bool(edits)
    # The UI must not claim the KB changed: the worker applies this on its next pass.
    d["applied"] = False
    return d


async def _decline(tid: str, pid: str, reason: str, reviewer: str):
    """Decline + suppress. Declining must make the queue quieter, not just empty for one night.

    The state change, the suppression row and the `kb_events` audit entry all live in
    `curation/apply.py::record_decline` — one implementation, so a decline can never again be
    recorded without its audit trail. This is HTTP glue: validate, delegate, shape the response.
    """
    reason = (reason or "").strip()
    if not reason:
        return _err(400, "A decline reason is required.", "reason_required")
    try:
        uuid.UUID(pid)
    except ValueError:
        return _err(404, "Proposal not found.", "not_found")

    outcome = await curation_apply.record_decline(
        tid, pid, reviewer, reason, suppress_days=DECLINE_SUPPRESS_DAYS)
    if outcome == "not_found":
        return _err(404, "Proposal not found.", "not_found")
    if outcome != "declined":
        return _err(409, f"Proposal is already {outcome}.", "not_pending")

    async with pool().acquire() as conn:
        out = await conn.fetchrow(
            f"SELECT {_LIST_COLS} FROM curation_proposals WHERE id = $1 AND client_id = $2",
            pid, tid)
    if not out:
        return _err(404, "Proposal not found.", "not_found")

    d = _proposal(out)
    d["suppressed_until_days"] = DECLINE_SUPPRESS_DAYS
    return d


async def _bulk(tid: str, body: BulkBody, reviewer: str):
    action = (body.action or "").strip().lower()
    if action not in ("accept", "decline"):
        return _err(400, "action must be 'accept' or 'decline'.", "bad_action")
    ids = [str(i).strip() for i in (body.ids or []) if str(i).strip()]
    if not ids:
        return _err(400, "ids is required.", "ids_required")
    if len(ids) > MAX_BULK_IDS:
        return _err(400, f"At most {MAX_BULK_IDS} proposals per bulk action.", "too_many")
    reason = (body.reason or "").strip()
    if action == "decline" and not reason:
        return _err(400, "A decline reason is required.", "reason_required")
    for i in ids:
        try:
            uuid.UUID(i)
        except ValueError:
            return _err(400, "Invalid proposal id.", "bad_id")

    # `remove` is excluded from bulk, per the ADR. A select-all + Accept is one click, and a
    # `remove` hides documents from retrieval — one fat-fingered bulk must not be able to gut a
    # knowledge base. Removals are reviewed one at a time, with a typed confirmation in the UI.
    async with pool().acquire() as conn:
        removals = await conn.fetchval(
            "SELECT count(*) FROM curation_proposals "
            "WHERE client_id = $1 AND id = ANY($2::uuid[]) AND op = 'remove'", tid, ids)
    if removals:
        return _err(400, "Removal proposals cannot be handled in bulk — review each one "
                         "individually.", "remove_in_bulk")

    results = []
    for pid in ids:
        r = (await _accept(tid, pid, AcceptBody(), reviewer) if action == "accept"
             else await _decline(tid, pid, reason, reviewer))
        if isinstance(r, JSONResponse):
            # Partial failure is normal here (someone else decided a card first). Report it per
            # id instead of failing the batch and leaving the reviewer unsure what landed.
            results.append({"id": pid, "ok": False, "code": "conflict"})
        else:
            results.append({"id": pid, "ok": True, "state": r["state"]})
    return {"action": action, "results": results,
            "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}


# --------------------------------------------------------------------------- #
# Tenant self-serve — /v1/curation/*
# --------------------------------------------------------------------------- #
@router.get("/v1/curation/proposals")
async def tenant_list(sc: Scope = Depends(_tenant_read), state: str = "pending",
                      op: str | None = None, sort: str = "priority",
                      limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0)):
    return await _list(sc.client_id, state, op, sort, limit, offset)


@router.get("/v1/curation/proposals/{proposal_id}")
async def tenant_get(proposal_id: str, sc: Scope = Depends(_tenant_read)):
    return await _get(sc.client_id, proposal_id)


@router.post("/v1/curation/proposals/{proposal_id}/accept")
async def tenant_accept(proposal_id: str, body: AcceptBody | None = None,
                        sc: Scope = Depends(_tenant_write)):
    return await _accept(sc.client_id, proposal_id, body or AcceptBody(), sc.reviewer)


@router.post("/v1/curation/proposals/{proposal_id}/decline")
async def tenant_decline(proposal_id: str, body: DeclineBody,
                         sc: Scope = Depends(_tenant_write)):
    return await _decline(sc.client_id, proposal_id, body.reason, sc.reviewer)


@router.post("/v1/curation/proposals/bulk")
async def tenant_bulk(body: BulkBody, sc: Scope = Depends(_tenant_write)):
    return await _bulk(sc.client_id, body, sc.reviewer)


# --------------------------------------------------------------------------- #
# Superadmin mirror — /admin/curation/{tenant_id}/*
# --------------------------------------------------------------------------- #
@router.get("/admin/curation/{tenant_id}/proposals")
async def admin_list(sc: Scope = Depends(_scope), state: str = "pending",
                     op: str | None = None, sort: str = "priority",
                     limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0)):
    return await _list(sc.client_id, state, op, sort, limit, offset)


@router.get("/admin/curation/{tenant_id}/proposals/{proposal_id}")
async def admin_get(proposal_id: str, sc: Scope = Depends(_scope)):
    return await _get(sc.client_id, proposal_id)


@router.post("/admin/curation/{tenant_id}/proposals/{proposal_id}/accept")
async def admin_accept(proposal_id: str, body: AcceptBody | None = None,
                       sc: Scope = Depends(_scope)):
    return await _accept(sc.client_id, proposal_id, body or AcceptBody(), sc.reviewer)


@router.post("/admin/curation/{tenant_id}/proposals/{proposal_id}/decline")
async def admin_decline(proposal_id: str, body: DeclineBody, sc: Scope = Depends(_scope)):
    return await _decline(sc.client_id, proposal_id, body.reason, sc.reviewer)


@router.post("/admin/curation/{tenant_id}/proposals/bulk")
async def admin_bulk(body: BulkBody, sc: Scope = Depends(_scope)):
    return await _bulk(sc.client_id, body, sc.reviewer)


# --------------------------------------------------------------------------- #
# Manual run trigger — signal only, never the run itself
# --------------------------------------------------------------------------- #
@router.post("/admin/curation/run", status_code=202)
async def admin_run(body: RunBody, principal: Principal = Depends(resolve_principal)):
    """Ask the worker to mine a tenant-day. Returns 202 immediately, always.

    A run harvests up to 500 conversations, embeds them, and makes one Claude call per cluster —
    minutes of work, comfortably past nginx's `proxy_read_timeout 300s`. Running it in the request
    would 504 *and* hold a pool connection across every one of those awaits, in a single-process
    API. So this only writes the signal the worker already polls.

    The signal is a `curation_runs` row in state `requested` with a deliberately BACKDATED
    `heartbeat_at`. That is not a hack for its own sake: it makes the row satisfy the runner's
    verbatim claim statement (`... WHERE state <> 'done' AND heartbeat_at < now() - interval
    '30 minutes'`) on the very next poll, so a manual trigger needs no second code path and no
    extra column. Requests never steal a live run — the upsert below refuses to touch a row that
    is `running` with a fresh heartbeat, and the runner's own claim is the real mutual exclusion.

    The consumer is `worker._requested_runs`, which reads `state = 'requested'` for ANY day,
    outside the nightly window, including days already `done` (re-running one is the point of
    asking). It runs on the worker's curation tick, so the run starts within
    `CURATION_TICK_S` (5 minutes) — "queued", not "started", is what 202 means here.
    """
    _deny_integration(principal)
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")

    day = date.today()
    if body.since:
        try:
            day = date.fromisoformat(body.since.strip())
        except ValueError:
            return _err(400, "since must be a date in YYYY-MM-DD form.", "bad_since")

    async with pool().acquire() as conn:
        if body.client_id:
            try:
                uuid.UUID(body.client_id)
            except ValueError:
                raise HTTPException(status_code=404, detail="Tenant not found")
            targets = [r["id"] for r in await conn.fetch(
                "SELECT id FROM clients WHERE id = $1 AND is_active = true", body.client_id)]
            if not targets:
                raise HTTPException(status_code=404, detail="Tenant not found")
        else:
            targets = [r["id"] for r in await conn.fetch(
                "SELECT id FROM clients WHERE is_active = true ORDER BY created_at")]

        queued, busy = [], []
        for cid in targets:
            rid = await conn.fetchval(
                "INSERT INTO curation_runs (client_id, day, state, started_at, heartbeat_at) "
                "VALUES ($1, $2, 'requested', now(), now() - interval '1 hour') "
                "ON CONFLICT (client_id, day) DO UPDATE "
                "   SET state = 'requested', heartbeat_at = now() - interval '1 hour', "
                "       error = NULL "
                "   WHERE curation_runs.state <> 'running' "
                "      OR curation_runs.heartbeat_at < now() - interval '30 minutes' "
                "RETURNING id", cid, day)
            (queued if rid else busy).append(str(cid))

    log.info("curation: manual run requested for day=%s queued=%s busy=%s",
             day, len(queued), len(busy))
    # `busy` is not an error: that tenant-day is already being mined right now.
    return {"day": day.isoformat(), "queued": queued, "busy": busy}
