"""Persistence for the conversational-AI tables (chat_conversations, chat_turns,
copilot_suggestions, copilot_feedback, chat_configs), tenant-scoped by client_id.

Every function here takes `client_id` first and every statement filters on it. That is
not a style preference: the chat site is a single integration acting for many tenants,
so the tenant is chosen per request (X-CQ-Tenant, verified against integration_grants)
rather than being implied by the credential. A query that forgets the filter would not
fail loudly — it would quietly answer one bank's customer out of another bank's KB.
The DB backstops us with composite FKs on (conversation_id, client_id) and
(turn_id, client_id), but those only catch a mismatched pair, never a missing WHERE.

Idempotency is a first-class concern rather than an afterthought: the chat site retries,
and a retried send must not double-run an LLM turn or double-count a suggestion. The
partial unique indexes in db/chat.sql — (client_id, turn_ref), (client_id, idempotency_key),
(client_id, suggest_ref) — are the arbiter; every write here is `ON CONFLICT DO NOTHING`
followed by a read, and reports `is_new=False` on replay so callers can skip the work.

asyncpg hands back jsonb as `str` unless a codec is registered (the house workaround, see
settings_store._load_key), so this module decodes on read and json.dumps on write, always.
"""
import json
import logging
import uuid

import asyncpg

from ..db import pool

log = logging.getLogger("cq")

# Returned when a tenant has never saved a chat config. Centralised here on purpose:
# gate() and the routers both read min_score/min_hits, and two independently invented
# default sets would mean the refusal threshold silently differs between the copilot
# and the public autopilot. autopilot_enabled stays False — a model speaking to an end
# customer with no human in the loop is opt-in, per tenant, forever.
CHAT_CONFIG_DEFAULTS: dict = {
    "version": 0,
    "persona": None,
    "greeting": {},
    "refusal_copy": {},
    "languages": ["en", "ka", "ru"],
    "canned": [],
    "autopilot_enabled": False,
    "min_score": 0.35,
    "min_hits": 1,
    "top_k": 8,
    "suggestion_count": 2,
    "settings": {},
    "is_active": False,
}

# How long a suggestion may sit in 'running' before the sweeper calls it dead.
STALE_SUGGESTION_S = 120


def _json(value) -> dict | list:
    """jsonb → python. asyncpg returns jsonb as str with no codec registered."""
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _citations_from(retrieval: dict | None) -> list[dict]:
    """Flatten retrieve_ranked() hits into the Turn envelope's citation shape.

    Numbered from 1 because the numbers are what the model cites in prose ("[1]"), and
    what the operator UI renders next to a passage card.
    """
    hits = (retrieval or {}).get("hits") or []
    return [
        {
            "n": i + 1,
            "document_id": str(h.get("document_id") or ""),
            "chunk_id": str(h.get("chunk_id") or ""),
            "title": h.get("title") or "",
            "score": h.get("score"),
        }
        for i, h in enumerate(hits)
    ]


def _grounding_from(retrieval: dict | None, grounded: bool) -> dict:
    r = retrieval or {}
    hits = r.get("hits") or []
    return {
        "grounded": bool(grounded),
        "method": r.get("method") or "none",
        "top_score": r.get("top_score"),
        "hit_count": len(hits),
        "kb_present": bool(r.get("kb_present")),
    }


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
async def upsert_conversation(
    client_id: str,
    external_ref: str,
    *,
    channel: str = "web",
    locale: str | None = None,
    mode: str = "assist",
    customer_ref: str | None = None,
) -> str:
    """Get-or-create the mirror row for a chat-site thread; returns its uuid.

    The mirror is derived and lossy — the chat site stays the system of record — so an
    upsert only refreshes the cheap descriptive fields. It never resets `state` or
    `mined_through`: those are ours, and clobbering the watermark would make the nightly
    curation miner re-do a tenant's whole day on the next inbound message.
    """
    if not client_id or not external_ref:
        raise ValueError("client_id and external_ref are required")
    async with pool().acquire() as conn:
        return str(await conn.fetchval(
            """
            INSERT INTO chat_conversations
                (client_id, external_ref, channel, locale, end_user_ref, metadata, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, now())
            ON CONFLICT (client_id, external_ref) DO UPDATE SET
                channel      = EXCLUDED.channel,
                locale       = COALESCE(EXCLUDED.locale, chat_conversations.locale),
                end_user_ref = COALESCE(EXCLUDED.end_user_ref, chat_conversations.end_user_ref),
                metadata     = chat_conversations.metadata || EXCLUDED.metadata,
                updated_at   = now()
            RETURNING id
            """,
            client_id, external_ref, channel or "web", locale, customer_ref,
            json.dumps({"mode": mode or "assist"}),
        ))


async def purge_conversation(client_id: str, external_ref: str) -> int:
    """GDPR erase: drop the thread and (by cascade) its turns, suggestions and feedback.

    Scoped by client_id even though external_ref is the chat site's own id — that id is
    only unique *within* a tenant, so an unscoped delete would erase a namesake thread
    belonging to somebody else.
    """
    if not client_id or not external_ref:
        return 0
    async with pool().acquire() as conn:
        res = await conn.execute(
            "DELETE FROM chat_conversations WHERE client_id = $1 AND external_ref = $2",
            client_id, external_ref)
    try:
        return int(res.split()[-1])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------
async def append_turn(
    client_id: str,
    conversation_id: str,
    *,
    turn_ref: str | None = None,
    idempotency_key: str | None = None,
    role: str,
    content: str,
    lang: str | None = None,
    source: str = "live",
    grounded: bool | None = None,
    retrieval: dict | None = None,
) -> tuple[str, bool]:
    """Append one message to the mirror. Returns (turn_id, is_new).

    `is_new=False` means this exact turn was already stored — the caller must NOT re-run
    the pipeline for it. Two independent unique indexes can claim the row (turn_ref, and
    the partial one on idempotency_key), so the INSERT uses a bare `ON CONFLICT DO NOTHING`:
    naming one inference target would let a replay that reuses the other key slip through
    as a duplicate. The follow-up SELECT prefers the idempotency key, because that is the
    identifier the retrying HTTP client actually holds.

    Grounding telemetry is written even when no Claude call happened — that is what makes
    a deterministic refusal auditable and free.
    """
    if not client_id or not conversation_id:
        raise ValueError("client_id and conversation_id are required")
    ref = turn_ref or f"cq-{uuid.uuid4().hex}"
    g = _grounding_from(retrieval, bool(grounded))
    meta = {"source": source or "live"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            new_id = await conn.fetchval(
                """
                INSERT INTO chat_turns
                    (client_id, conversation_id, turn_ref, role, content, locale,
                     grounded, method, top_score, hit_count, citations,
                     idempotency_key, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                client_id, conversation_id, ref, role, content, lang,
                g["grounded"], g["method"], g["top_score"], g["hit_count"],
                json.dumps(_citations_from(retrieval)),
                idempotency_key, json.dumps(meta),
            )
            if new_id is not None:
                # Only a genuinely new message moves the miner's watermark input.
                await conn.execute(
                    """
                    UPDATE chat_conversations SET last_message_at = now(), updated_at = now()
                    WHERE client_id = $1 AND id = $2
                    """,
                    client_id, conversation_id)
                return str(new_id), True
            if idempotency_key:
                existing = await conn.fetchval(
                    "SELECT id FROM chat_turns WHERE client_id = $1 AND idempotency_key = $2",
                    client_id, idempotency_key)
                if existing is not None:
                    return str(existing), False
            existing = await conn.fetchval(
                "SELECT id FROM chat_turns WHERE client_id = $1 AND turn_ref = $2",
                client_id, ref)
    if existing is None:
        # DO NOTHING fired but neither key finds a row: the only way that happens is a
        # constraint we do not know about, so fail loudly rather than return a bogus id.
        raise RuntimeError(f"chat turn {ref} conflicted but could not be re-read")
    return str(existing), False


async def recent_turns(client_id: str, conversation_id: str, limit: int = 8) -> list[dict]:
    """The rolling window handed to the model, oldest-first.

    Fetched newest-first so the LIMIT keeps the *latest* turns, then reversed in python —
    an ORDER BY ASC with a LIMIT would hand back the beginning of the thread instead.
    """
    if not client_id or not conversation_id:
        return []
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, turn_ref, role, content, locale, grounded, method, top_score,
                   hit_count, citations, metadata, created_at
            FROM chat_turns
            WHERE client_id = $1 AND conversation_id = $2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            client_id, conversation_id, max(1, int(limit or 8)))
    out = [
        {
            "id": str(r["id"]),
            "turn_ref": r["turn_ref"],
            "role": r["role"],
            "content": r["content"] or "",
            "lang": r["locale"],
            "grounded": r["grounded"],
            "method": r["method"],
            "top_score": r["top_score"],
            "hit_count": r["hit_count"],
            "citations": _json(r["citations"]) or [],
            "metadata": _json(r["metadata"]) or {},
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Copilot suggestions
# ---------------------------------------------------------------------------
async def claim_suggestion(
    client_id: str,
    suggest_ref: str,
    conversation_id: str,
    turn_id: str | None = None,
    *,
    locale: str | None = None,
    mode: str | None = None,
    integration_id: str | None = None,
) -> tuple[str, bool]:
    """Stake the 'running' row for a generation. Returns (id, is_new).

    This is the concurrency guard, not a bookkeeping insert: `is_new=False` means another
    worker (or a retried request) already owns this suggest_ref, and this caller must not
    start a second Claude generation for it.

    `locale`/`mode`/`integration_id` are stored because the row is the ONLY thing a later
    `GET /stream` (or a `regenerate`) has to reconstruct the request from — the HTTP call that
    supplied them is long gone by then, and a generation that has to guess the caller's
    language is a generation in the wrong language.
    """
    if not client_id or not suggest_ref:
        raise ValueError("client_id and suggest_ref are required")
    async with pool().acquire() as conn:
        new_id = await conn.fetchval(
            """
            INSERT INTO copilot_suggestions
                (client_id, suggest_ref, conversation_id, turn_id, state,
                 locale, mode, integration_id)
            VALUES ($1, $2, $3, $4, 'running', $5, $6, $7)
            ON CONFLICT (client_id, suggest_ref) DO NOTHING
            RETURNING id
            """,
            client_id, suggest_ref, conversation_id, turn_id,
            locale, mode, integration_id)
        if new_id is not None:
            return str(new_id), True
        existing = await conn.fetchval(
            "SELECT id FROM copilot_suggestions WHERE client_id = $1 AND suggest_ref = $2",
            client_id, suggest_ref)
    if existing is None:
        raise RuntimeError(f"suggestion {suggest_ref} conflicted but could not be re-read")
    return str(existing), False


async def finish_suggestion(
    client_id: str,
    suggest_ref: str,
    *,
    envelope: dict,
    state: str = "ready",
    latency_ms: int | None = None,
) -> None:
    """Land a completed generation. `state` is 'ready' or 'refused' (a refusal is a
    successful, deliberate outcome — the gate declined before any Claude call, and the
    row still carries its grounding so the refusal is auditable).

    The envelope the engine built is stored WHOLE and the indexed columns are projected out
    of it, rather than each caller assembling both halves. That is what makes the three read
    paths (blocking GET, SSE replay, live `done`) return the same bytes: there is one
    producer of a Turn object — `chat.build_turn_envelope` — and this function only files it.
    Reconstructing an envelope from the columns instead would silently drop the fields no
    column exists for (turn_ref, conversation_ref, channel, locale, reason, reply).

    The end-to-end latency is folded into `stages` rather than getting its own column:
    stages is already the per-phase timing blob the ADR's latency budget is tuned from,
    and one shape beats two places to look.
    """
    if not client_id or not suggest_ref:
        raise ValueError("client_id and suggest_ref are required")
    env = dict(envelope or {})
    usage = env.get("usage") or {}
    st = dict(usage.get("latency_ms") or {})
    if latency_ms is not None:
        st["total_ms"] = int(latency_ms)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE copilot_suggestions
               SET state = $3, tier1 = $4::jsonb, suggestions = $5::jsonb,
                   citations = $6::jsonb, grounding = $7::jsonb, handoff = $8::jsonb,
                   stages = $9::jsonb, envelope = $10::jsonb,
                   model = $11, error = NULL, completed_at = now()
             WHERE client_id = $1 AND suggest_ref = $2
            """,
            client_id, suggest_ref, state,
            json.dumps(env.get("tier1") or []),
            json.dumps(env.get("suggestions") or []),
            json.dumps(env.get("citations") or []),
            json.dumps(env.get("grounding") or {}),
            json.dumps(env.get("handoff") or {}),
            json.dumps(st), json.dumps(env),
            usage.get("model"),
        )


async def fail_suggestion(client_id: str, suggest_ref: str, error: str) -> None:
    """Terminal failure. Only a 'running' row is moved, so a late-arriving error from a
    retried worker cannot overwrite an answer that already landed."""
    if not client_id or not suggest_ref:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE copilot_suggestions
               SET state = 'error', error = $3, completed_at = now()
             WHERE client_id = $1 AND suggest_ref = $2 AND state = 'running'
            """,
            client_id, suggest_ref, (error or "")[:2000])


async def get_suggestion(client_id: str, suggest_ref: str) -> dict | None:
    """The warm path: one indexed SELECT on (client_id, suggest_ref)."""
    if not client_id or not suggest_ref:
        return None
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, client_id, conversation_id, turn_id, suggest_ref, state, grounding,
                   tier1, suggestions, citations, handoff, stages, envelope, model, error,
                   locale, mode, integration_id, created_at, completed_at
            FROM copilot_suggestions
            WHERE client_id = $1 AND suggest_ref = $2
            """,
            client_id, suggest_ref)
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "client_id": str(row["client_id"]),
        "conversation_id": str(row["conversation_id"]) if row["conversation_id"] else None,
        "turn_id": str(row["turn_id"]) if row["turn_id"] else None,
        "suggest_ref": row["suggest_ref"],
        "state": row["state"],
        "grounding": _json(row["grounding"]) or {},
        "tier1": _json(row["tier1"]) or [],
        "suggestions": _json(row["suggestions"]) or [],
        "citations": _json(row["citations"]) or [],
        "handoff": _json(row["handoff"]) or {},
        "stages": _json(row["stages"]) or {},
        "envelope": _json(row["envelope"]) or {},
        "model": row["model"],
        "error": row["error"],
        "locale": row["locale"],
        "mode": row["mode"],
        "integration_id": str(row["integration_id"]) if row["integration_id"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


async def record_feedback(
    client_id: str,
    suggest_ref: str,
    *,
    variant_index: int | None,
    action: str,
    final_text: str | None = None,
) -> None:
    """Log what the operator actually did with a card.

    `suggestion_id` is resolved through a client-scoped sub-select rather than trusted
    from the caller, so a feedback row can never be attached to another tenant's
    suggestion. A NULL result is fine and deliberate: feedback survives its suggestion
    being reaped, and `suggest_ref` is retained as the durable join key.
    """
    if not client_id or not suggest_ref:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO copilot_feedback
                (client_id, suggestion_id, suggest_ref, suggestion_index, action, final_text)
            VALUES (
                $1,
                (SELECT id FROM copilot_suggestions WHERE client_id = $1 AND suggest_ref = $2),
                $2, $3, $4, $5)
            """,
            client_id, suggest_ref,
            None if variant_index is None else int(variant_index),
            action, final_text)


async def reap_stale_suggestions(older_than_s: int = STALE_SUGGESTION_S) -> int:
    """Sweep generations abandoned by a crash or a restart. Returns the row count.

    NOT client-scoped, and the one function here that isn't: it is a maintenance pass over
    the whole in-flight set, served by the partial index on (created_at) WHERE state='running'.
    Two properties keep that safe, and both are load-bearing:

      * it touches ONLY copilot_suggestions. Contrast analysis.sweep_stuck_jobs(), which
        blanket-UPDATEs audio_jobs with no client and no age filter on every boot — and
        every push to main restarts the API. That is exactly why the chat state vocabulary
        ('running'/'ready'/'refused'/'error'/'expired') is deliberately disjoint from
        ('queued','transcribing','analyzing'): no janitor can reach across.
      * it has an AGE filter. A restart must not error out a generation that started 200 ms
        ago in another worker.

    RETURNING client_id so a caller can log which tenants were affected — this module's
    every statement names the column, including the one that does not filter on it.
    """
    ttl = max(30, int(older_than_s or STALE_SUGGESTION_S))
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE copilot_suggestions
               SET state = 'error',
                   error = 'Generation abandoned (worker restart or timeout).',
                   completed_at = now()
             WHERE state = 'running'
               AND created_at < now() - make_interval(secs => $1::double precision)
            RETURNING client_id
            """,
            float(ttl))
    if rows:
        log.info("reaped %d stale copilot suggestions (ttl %ss)", len(rows), ttl)
    return len(rows)


# ---------------------------------------------------------------------------
# Per-tenant chat config
# ---------------------------------------------------------------------------
async def get_chat_config(client_id: str) -> dict:
    """The active chat_configs row merged over CHAT_CONFIG_DEFAULTS. Never returns None.

    The tuning knobs the gate reads (min_score, min_hits, top_k, suggestion_count) live in
    the row's `settings` jsonb and are lifted to the top level here, so callers see one flat
    dict and no caller has to re-invent a threshold. A tenant with no config at all gets the
    defaults — a fresh tenant must behave, not crash.
    """
    cfg = dict(CHAT_CONFIG_DEFAULTS)
    if not client_id:
        return cfg
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT version, persona, greeting, refusal_copy, languages, canned,
                   autopilot_enabled, settings, is_active, updated_at, updated_by
            FROM chat_configs
            WHERE client_id = $1 AND is_active
            ORDER BY version DESC LIMIT 1
            """,
            client_id)
    if not row:
        return cfg
    settings_blob = _json(row["settings"]) or {}
    cfg.update({
        "version": row["version"],
        "persona": row["persona"],
        "greeting": _json(row["greeting"]) or {},
        "refusal_copy": _json(row["refusal_copy"]) or {},
        "languages": list(row["languages"] or []) or CHAT_CONFIG_DEFAULTS["languages"],
        "canned": _json(row["canned"]) or [],
        "autopilot_enabled": bool(row["autopilot_enabled"]),
        "settings": settings_blob,
        "is_active": bool(row["is_active"]),
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
    })
    # Lift only the known knobs: an arbitrary settings key must not be able to shadow a
    # structural field like `autopilot_enabled` or `languages`.
    for knob in ("min_score", "min_hits", "top_k", "suggestion_count"):
        if settings_blob.get(knob) is not None:
            cfg[knob] = settings_blob[knob]
    return cfg


async def save_chat_config(
    client_id: str,
    *,
    persona: str | None = None,
    greeting: dict | None = None,
    refusal_copy: dict | None = None,
    languages: list[str] | None = None,
    canned: list | None = None,
    autopilot_enabled: bool = False,
    settings: dict | None = None,
    updated_by: str = "superadmin",
) -> dict:
    """Persist a NEW active version and return the merged effective config.

    Versioned-and-superseded rather than updated in place, exactly like scoring_configs: the
    refusal copy is lawyer-reviewed text and `autopilot_enabled` decides whether a model talks
    to end customers unsupervised, so "what was this tenant configured to say last Tuesday" has
    to be answerable. `uq_chat_configs_active` enforces one active row, so the deactivate and
    the insert share a transaction — and a concurrent save colliding on
    UNIQUE (client_id, version) is retried rather than 500'd (the same failure scoring_store
    had to fix).
    """
    if not client_id:
        raise ValueError("client_id is required")
    langs = [str(x).strip().lower() for x in (languages or []) if str(x).strip()]
    for _attempt in range(3):
        try:
            async with pool().acquire() as conn:
                async with conn.transaction():
                    next_ver = await conn.fetchval(
                        "SELECT COALESCE(MAX(version), 0) + 1 FROM chat_configs "
                        "WHERE client_id = $1", client_id)
                    await conn.execute(
                        "UPDATE chat_configs SET is_active = false, updated_at = now() "
                        "WHERE client_id = $1 AND is_active", client_id)
                    await conn.execute(
                        """
                        INSERT INTO chat_configs
                            (client_id, version, persona, greeting, refusal_copy, languages,
                             canned, autopilot_enabled, settings, is_active,
                             updated_at, updated_by)
                        VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7::jsonb,$8,$9::jsonb,true,
                                now(),$10)
                        """,
                        client_id, next_ver, (persona or "").strip() or None,
                        json.dumps(greeting or {}), json.dumps(refusal_copy or {}),
                        langs or CHAT_CONFIG_DEFAULTS["languages"],
                        json.dumps(canned or []), bool(autopilot_enabled),
                        json.dumps(settings or {}), updated_by)
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 2:
                raise
    return await get_chat_config(client_id)
