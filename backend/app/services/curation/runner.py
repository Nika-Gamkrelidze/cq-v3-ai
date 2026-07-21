"""The nightly curation pass: harvest -> cluster -> propose -> queue, one tenant-day at a time.

This module is the orchestration and the *cost control*. The expensive stages are guarded by
cheap ones in a strict order — SQL harvest (free) narrows to clusters (embeddings, free-ish,
self-hosted TEI), and only a surviving cluster is worth a Claude call. Everything that can
reject a cluster runs BEFORE `propose`:

  * semantic suppression (applied inside `cluster.cluster`, against declined medoids),
  * the poisoning defence (`distinct_sources >= 2` unless the signal is an operator correction
    or a fact-check verdict — those are trustworthy from a single occurrence because a human
    or the fact-checker already adjudicated them),
  * the open-queue budget (hard cap of 10 pending cards per tenant).

That last one is the difference between a review queue and a wall. A tenant with 5,000
conversations must see ~6–10 cards, and new ones must surface only as old ones are resolved.
Spending tokens to generate card #34 that nobody will ever look at is pure waste, so the budget
is checked before the LLM stage, not after it.

**Concurrency.** A tenant-day is claimed in `curation_runs` with the heartbeat-reclaim upsert
documented verbatim in db/curation.sql. A NULL return means "owned elsewhere or already done" —
a normal skip, never an error. `ON CONFLICT DO NOTHING` would have been the obvious choice and
is wrong: a run killed mid-flight (every push to main auto-deploys and restarts the stack) would
leave a permanent 'running' row and skip that tenant-day forever.

**Resume, not restart.** `curation_runs.window_start` doubles as the watermark and is advanced
per processed cluster. A crashed run resumes from the last committed cluster instead of
re-harvesting and re-paying for the whole day.

**Cold start is chat-driven.** Signals S5/S6 read `audio_jobs.kb_check`, which is only computed
for tenant principals whose KB already has chunks, inside a bare except — i.e. null for exactly
the thin-KB tenant this loop exists to help. Expect the first weeks of proposals to come from
S1–S4, and zero proposals from a tenant with no chat traffic yet. That is correct behaviour.

Never holds a pool connection across an LLM or embedding await: every stage acquires, reads or
writes, and releases before the next await that leaves the process.
"""
import datetime as dt
import json
import logging
import uuid as uuid_mod

from ...db import pool
from .. import llm, settings_store
from . import cluster as cluster_mod
from . import miner
from . import propose as propose_mod

log = logging.getLogger("cq")

# Tenant-overridable via clients.settings->'curation'. Defaults live here rather than in the
# database so a fresh tenant needs no configuration to be curated.
CURATION_DEFAULTS: dict = {
    "candidate_cap": 500,        # per run; the ADR's harvest cap
    "cluster_threshold": 0.82,   # cosine, greedy medoid
    "suppress_threshold": 0.88,  # cosine against a declined medoid
    "max_open_proposals": 10,    # the hard queue cap
    "min_distinct_sources": 2,   # poisoning defence
    "evidence_per_card": 3,      # 2-3 verbatim quotes is what makes a card feel real
    "top_k": 5,                  # current KB chunks shown to the model, so it can propose update
    "enabled": True,
}

# Signals whose single occurrence is already human- or checker-adjudicated, so the
# distinct_sources >= 2 defence does not apply: an operator correction (S3) and the two
# fact-check verdicts (S5, S6).
TRUSTED_SIGNALS = {"S3", "S5", "S6"}

# NOTE: the priority formula and its per-signal weights live in ONE place —
# `propose.compute_priority` / `propose.SOURCE_WEIGHTS`. This module used to carry a second,
# subtly different table and formula, and the column ended up holding whichever of the two ran
# last. Two rankings that disagree about which signal is most trustworthy is a bug, not a matter
# of taste (propose.py says so in its own comment), so there is deliberately no weight table here.

# How often the runner refreshes heartbeat_at while it works. Must be well under the 30-minute
# reclaim window in db/curation.sql, or a healthy long run would be stolen from itself.
HEARTBEAT_EVERY = 3   # clusters


# ---- config -----------------------------------------------------------------
async def _config(client_id: str) -> dict:
    """Effective curation config: defaults, the tenant's override, and the runtime credentials.

    The credentials are resolved HERE and are deliberately NOT part of `CURATION_DEFAULTS`: the
    tenant-supplied override is filtered to keys that already exist in the defaults, so a tenant
    can tune thresholds but can never inject an `api_key` or pin a `model` of its own.

    `propose()` requires both and raises `LLMError` without them — and the runner catches that
    per cluster and carries on, so a missing lookup here produces a run that finishes `done`
    with `proposals=0` every single night while looking perfectly healthy. Read from
    `settings_store.get_effective()`, which is the admin panel's source of truth and falls back
    to `.env` (root CLAUDE.md §4: never hardcode a model id).
    """
    async with pool().acquire() as conn:
        raw = await conn.fetchval(
            "SELECT settings FROM clients WHERE id = $1 AND is_active", client_id)
    settings = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
    override = settings.get("curation") if isinstance(settings, dict) else None
    cfg = dict(CURATION_DEFAULTS)
    if isinstance(override, dict):
        cfg.update({k: v for k, v in override.items() if k in CURATION_DEFAULTS})
    app_cfg = await settings_store.get_effective()
    cfg["api_key"] = app_cfg.get("anthropic_api_key") or ""
    cfg["model"] = app_cfg.get("llm_model") or ""
    return cfg


# ---- run ledger -------------------------------------------------------------
async def _claim(client_id: str, day: dt.date, window_start: dt.datetime,
                 window_end: dt.datetime) -> tuple[str, dt.datetime] | None:
    """Claim (tenant, day). Returns (run_id, resume_from) or None if owned elsewhere / done.

    The statement is the one documented verbatim in db/curation.sql — do not "simplify" it to
    ON CONFLICT DO NOTHING (see the module docstring).
    """
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO curation_runs (client_id, day, state, started_at, heartbeat_at,
                                       window_start, window_end)
            VALUES ($1, $2, 'running', now(), now(), $3, $4)
            ON CONFLICT (client_id, day) DO UPDATE
                SET heartbeat_at = now(), state = 'running', started_at = now()
                WHERE curation_runs.state <> 'done'
                  AND curation_runs.heartbeat_at < now() - interval '30 minutes'
            RETURNING id, window_start
            """,
            client_id, day, window_start, window_end)
    if row is None:
        return None
    # A reclaimed run keeps the watermark its predecessor reached; a fresh one starts at the
    # window's beginning. COALESCE covers a legacy row written before this column was set.
    return str(row["id"]), (row["window_start"] or window_start)


async def _heartbeat(run_id: str, watermark: dt.datetime | None = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE curation_runs SET heartbeat_at = now(), "
            "window_start = COALESCE($2, window_start) WHERE id = $1",
            run_id, watermark)


async def _finish_run(run_id: str, state: str, counters: dict, error: str | None = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE curation_runs
               SET state = $2, finished_at = now(), heartbeat_at = now(),
                   candidates = $3, clusters = $4, proposals = $5, llm_calls = $6, error = $7
             WHERE id = $1
            """,
            run_id, state, counters.get("candidates", 0), counters.get("clusters", 0),
            counters.get("proposals", 0), counters.get("llm_calls", 0),
            (error or None) and error[:2000])


# ---- scoring ----------------------------------------------------------------
def _priority(cl, out: dict) -> float:
    """The proposal's queue rank — always `propose.compute_priority`, never a second formula.

    `propose()` already computed it against the cluster's LATEST occurrence (an exponential
    14-day half-life with a floor), which is strictly better than the day-granular decay this
    module used to apply. The recompute below is only the belt for a `propose` implementation
    that stopped returning the key; it calls the same function, so the two can never disagree.

    Precomputed rather than sorted in SQL so the queue read stays a single index scan on
    (client_id, state, priority DESC) instead of a sort over an expression.
    """
    value = out.get("priority")
    if isinstance(value, (int, float)):
        return float(value)
    return propose_mod.compute_priority(
        getattr(cl, "occurrences", 0), out.get("confidence"), getattr(cl, "signal", "") or "",
        propose_mod._latest_occurrence(list(getattr(cl, "candidates", None) or [])))


def _uuid_or_none(value):
    """A uuid column will not take a stray non-uuid string, and evidence must never be the
    reason a whole night's run aborts. Anything unparseable becomes NULL."""
    if value is None:
        return None
    try:
        return uuid_mod.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# ---- proposal persistence ---------------------------------------------------
async def _upsert_proposal(client_id: str, run_id: str, cl, out: dict, cfg: dict,
                           window_start: dt.datetime, window_end: dt.datetime) -> str:
    """Insert the card, or merge into the tenant's existing OPEN card for the same cluster.

    The conflict target is the partial unique index (state = 'pending'), so a *decided* card
    never blocks a legitimate re-raise weeks later — only an unreviewed one does, and merging
    into it is exactly right: the same gap recurring should make one card louder, not spawn a
    second identical one.
    """
    tags = [t for t in (out.get("suggested_tags") or []) if isinstance(t, str)][:12]
    confidence = out.get("confidence")
    priority = _priority(cl, out)
    async with pool().acquire() as conn:
        async with conn.transaction():
            pid = str(await conn.fetchval(
                """
                INSERT INTO curation_proposals
                    (client_id, run_id, op, state, signal, cluster_key, cluster_medoid, title,
                     proposed_content, rationale, target_document_id, target_chunk_id,
                     suggested_tags, occurrences, distinct_sources, confidence, priority, risk,
                     window_start, window_end)
                VALUES ($1,$2,$3,'pending',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                ON CONFLICT (client_id, cluster_key) WHERE state = 'pending' DO UPDATE
                    SET run_id = EXCLUDED.run_id,
                        op = EXCLUDED.op,
                        title = EXCLUDED.title,
                        proposed_content = EXCLUDED.proposed_content,
                        rationale = EXCLUDED.rationale,
                        target_document_id = EXCLUDED.target_document_id,
                        target_chunk_id = EXCLUDED.target_chunk_id,
                        suggested_tags = EXCLUDED.suggested_tags,
                        -- The gap recurred: the card gets louder, it does not reset.
                        occurrences = curation_proposals.occurrences + EXCLUDED.occurrences,
                        distinct_sources = GREATEST(curation_proposals.distinct_sources,
                                                    EXCLUDED.distinct_sources),
                        confidence = EXCLUDED.confidence,
                        priority = EXCLUDED.priority,
                        risk = EXCLUDED.risk,
                        window_start = LEAST(curation_proposals.window_start, EXCLUDED.window_start),
                        window_end = GREATEST(curation_proposals.window_end, EXCLUDED.window_end),
                        updated_at = now()
                RETURNING id
                """,
                client_id, run_id, out["op"], cl.signal, cl.key, cl.medoid,
                (out.get("title") or "")[:500] or None, out.get("proposed_content"),
                out.get("rationale"), out.get("target_document_id"), out.get("target_chunk_id"),
                tags, cl.occurrences, cl.distinct_sources, confidence, priority,
                out.get("risk"), window_start, window_end))

            # Evidence is rewritten wholesale rather than appended: a merged card must show the
            # freshest quotes, and unbounded growth would turn a 3-quote card into a transcript.
            await conn.execute("DELETE FROM curation_evidence WHERE proposal_id = $1", pid)
            for cand in cl.candidates[:int(cfg["evidence_per_card"])]:
                # Defence in depth. apply.py re-verifies this at accept time; refusing to write
                # it in the first place means the accept-time check should never fire.
                if str(cand.client_id) != str(client_id):
                    log.error("curation: dropping cross-tenant candidate %s on client %s",
                              cand.source_id, client_id)
                    continue
                # `locale` and `conversation_id` are carried on the Candidate for exactly this
                # INSERT (see miner.Candidate's comment). Dropping them leaves the card's
                # language badge and its deep-link to the source conversation permanently empty.
                await conn.execute(
                    """
                    INSERT INTO curation_evidence
                        (client_id, proposal_id, source_client_id, signal, source_kind, source_id,
                         conversation_id, channel, locale, question, answer, occurred_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    """,
                    client_id, pid, cand.client_id, cand.signal, cand.source_kind,
                    _uuid_or_none(cand.source_id),
                    _uuid_or_none((getattr(cand, "metadata", None) or {}).get("conversation_id")),
                    cand.channel, getattr(cand, "locale", None),
                    cand.question, cand.answer, cand.occurred_at)
    return pid


async def _open_count(client_id: str) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM curation_proposals WHERE client_id = $1 AND state = 'pending'",
            client_id) or 0)


async def _suppressed_medoids(client_id: str) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT medoid FROM curation_suppressions "
            "WHERE client_id = $1 AND (until IS NULL OR until > now())",
            client_id)
    return [r["medoid"] for r in rows if r["medoid"]]


# ---- the pass ---------------------------------------------------------------
async def run_for_client(client_id: str, day: dt.date) -> dict:
    """Run one tenant-day. Never raises: the outcome is the returned dict and the run row."""
    client_id = str(client_id)
    counters = {"candidates": 0, "clusters": 0, "proposals": 0, "llm_calls": 0}
    window_start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    window_end = window_start + dt.timedelta(days=1)

    cfg = await _config(client_id)
    if not cfg.get("enabled", True):
        return {"client_id": client_id, "day": str(day), "skipped": "disabled", **counters}

    claimed = await _claim(client_id, day, window_start, window_end)
    if claimed is None:
        # Owned by another runner, or already done. Documented as a normal skip.
        return {"client_id": client_id, "day": str(day), "skipped": "not_claimed", **counters}
    run_id, resume_from = claimed

    try:
        budget = int(cfg["max_open_proposals"]) - await _open_count(client_id)
        if budget <= 0:
            # Deliberately BEFORE the harvest: a full queue means the reviewer has not caught
            # up, and mining a day nobody will look at spends TEI time and Claude tokens for
            # cards that could never be shown.
            log.info("curation: client=%s day=%s queue full, skipping", client_id, day)
            await _finish_run(run_id, "done", counters)
            return {"client_id": client_id, "day": str(day), "skipped": "queue_full", **counters}

        cands = await miner.collect(client_id, resume_from, window_end,
                                    cap=int(cfg["candidate_cap"]))
        counters["candidates"] = len(cands)
        if not cands:
            await _finish_run(run_id, "done", counters)
            return {"client_id": client_id, "day": str(day), "run_id": run_id, **counters}

        # Suppression happens INSIDE clustering, in the same embedding batch as the day's
        # candidates, so a declined question costs tokens exactly once — not every night.
        suppressed = await _suppressed_medoids(client_id)
        clusters = await cluster_mod.cluster(cands, suppressed,
                                             threshold=float(cfg["cluster_threshold"]))
        counters["clusters"] = len(clusters)

        # Loudest first, so a truncated budget spends on the biggest gaps.
        clusters.sort(key=lambda c: (c.occurrences, c.distinct_sources), reverse=True)

        watermark = resume_from
        for i, cl in enumerate(clusters):
            if budget <= 0:
                log.info("curation: client=%s day=%s budget exhausted after %s proposal(s)",
                         client_id, day, counters["proposals"])
                break
            if (cl.distinct_sources < int(cfg["min_distinct_sources"])
                    and cl.signal not in TRUSTED_SIGNALS):
                # Poisoning defence: one person asking one thing many times must not be able to
                # write into a regulated tenant's KB.
                continue
            try:
                counters["llm_calls"] += 1
                out = await propose_mod.propose(client_id, cl, cfg)
            except llm.LLMError as exc:
                # One cluster's synthesis failing must not abandon the rest of the night.
                log.warning("curation: propose failed client=%s cluster=%s: %s",
                            client_id, cl.key, exc)
                continue
            except Exception:  # noqa: BLE001 — a malformed tool result is a per-cluster problem
                log.exception("curation: propose raised client=%s cluster=%s", client_id, cl.key)
                continue
            if not out:
                continue
            await _upsert_proposal(client_id, run_id, cl, out, cfg, window_start, window_end)
            counters["proposals"] += 1
            budget -= 1
            # Watermark advances per item, so a crash resumes here rather than restarting the day.
            newest = max((c.occurred_at for c in cl.candidates), default=None)
            if newest and newest > watermark:
                watermark = newest
            if i % HEARTBEAT_EVERY == 0:
                await _heartbeat(run_id, watermark)

        await _heartbeat(run_id, watermark)
        await _finish_run(run_id, "done", counters)
        log.info("curation done: client=%s day=%s candidates=%s clusters=%s proposals=%s llm=%s",
                 client_id, day, counters["candidates"], counters["clusters"],
                 counters["proposals"], counters["llm_calls"])
        return {"client_id": client_id, "day": str(day), "run_id": run_id, **counters}
    except Exception as exc:  # noqa: BLE001 — one tenant's failure must not stop the others
        log.exception("curation run failed: client=%s day=%s", client_id, day)
        await _finish_run(run_id, "error", counters, f"{type(exc).__name__}: {exc}")
        return {"client_id": client_id, "day": str(day), "run_id": run_id,
                "error": str(exc), **counters}


async def run_all(day: dt.date) -> list[dict]:
    """Run every active tenant for `day`, SEQUENTIALLY.

    Sequential is not laziness: embeddings are fp32 BGE-M3 on a single-CPU TEI container with no
    reservation, shared with the live copilot read path. Fanning tenants out in parallel would
    turn a background job into a latency incident for whoever is chatting at 02:00.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT id FROM clients WHERE is_active ORDER BY id")
    out = []
    for r in rows:
        out.append(await run_for_client(str(r["id"]), day))
    return out
