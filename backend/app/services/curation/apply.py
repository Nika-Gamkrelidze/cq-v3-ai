"""Stage 6 of the curation loop — turn a human-accepted proposal into a real KB change.

This is the only place in the system where an automated pathway writes to a tenant's knowledge
base, so it is deliberately the most defensive module in P2. Four hazards, each verified in
source rather than assumed, shape every line below.

1. **`update` MUST go through `kb_ingest.ingest_document`, never `reembed_document`.**
   `reembed_document` only runs `UPDATE kb_chunks SET embedding = …`; its own docstring says it
   "reuses existing chunk content, only replaces vectors". It never reads `content_text` and
   never re-chunks. An accepted update routed through it would rewrite `content_text`, leave
   retrieval **bit-for-bit unchanged**, and the next night's miner would re-detect the identical
   gap forever — a loop that looks like it is working and never converges.

2. **`ingest_document` swallows every exception.** It catches, sets the document to
   `status='error'`, logs, and returns None. `await`ing it therefore tells you nothing about
   success. Every call here is followed by a re-read of `kb_documents.status`, and an `'error'`
   lands the proposal in `apply_failed` with the document's own error text. Without that, a
   failed apply would be reported to the reviewer as a success.

3. **`ingest_document`'s `source_type` argument is never used in its body** — the column is
   written by whoever INSERTs the document. So `source_type='curation'` is set on the INSERT
   here, not passed and forgotten.

4. **`remove` is never a hard delete.** It sets `visibility='internal'` (the document stops
   being retrievable) and records a human delete candidate in the activity log. An automated
   pathway that could destroy a regulated tenant's source document — on one click, from a model's
   suggestion — is not a trade this product makes.

Cross-tenant defence: `curation_evidence.source_client_id` is re-verified against the proposal's
`client_id` **before anything is written**. It is redundant only if the write side is always
correct, and the whole point is that a single misattribution upstream would otherwise be
laundered into a permanent, human-*approved* cross-tenant KB contamination.

Claiming: the row is claimed with a conditional UPDATE on `applied_at` (no new column), so the
worker's 5 s poll and a manual admin apply cannot both run the same proposal. A claim older than
`CLAIM_STALE` is reclaimable, because every push to main restarts the stack and a process that
dies between claim and completion must not strand the card forever.

Never holds a pool connection across an embedding await: `ingest_document` and `reembed_chunk`
both hit the CPU-bound TEI encoder, and the pool is `max_size=10` for the entire process.
"""
import json
import logging

from ...db import pool
from .. import kb_events, kb_ingest

log = logging.getLogger("cq")

# A claim this old belonged to a process that no longer exists (a deploy restart, a crash).
# Comfortably longer than any real ingest: a large document is seconds, not minutes.
CLAIM_STALE = "15 minutes"

# doc_type / tags stamped on documents this pathway creates, so a reviewer (and the KB-admin
# console's existing filters) can always tell curated content from imported source material.
CURATED_DOC_TYPE = "curated"
CURATED_TAG = "curated"

# Visibility of a document this pathway CREATES. `internal` by default, which is the ADR's
# "publishability, default-safe" rule: curated content is answerable by the operator copilot,
# fact-check and scoring, but the public autopilot (which passes visibility='public') does not
# see it until a human publishes it.
#
# That default is also why a gap curated from PUBLIC chat traffic still cannot be answered
# publicly, so it is a per-tenant opt-in: `clients.settings->'curation'->>'curated_visibility'`
# set to 'public' means "our reviewer's approval IS the publishability decision". It is opt-in
# and not the default because approving an answer and clearing it for the open internet are two
# different judgements for a bank or a clinic.
CURATED_VISIBILITY = "internal"
_VISIBILITIES = ("internal", "public")


class ApplyError(RuntimeError):
    """A refusal to write. Always ends the proposal in `apply_failed`, never a silent no-op."""


async def _fail(proposal_id: str, message: str) -> None:
    """Land the proposal in `apply_failed` with a reviewer-readable reason.

    `apply_failed` exists precisely because success is not observable from the ingest await
    (see hazard 2 in the module docstring).

    `applied_at` is CLEARED. It was set a moment ago purely as the claim stamp, and leaving it
    would make the column read "this card was applied at 14:46" on a card that was never
    applied — the one timestamp a reviewer and the audit trail rely on. Nothing depends on it
    to stop a retry loop: the worker polls `state = 'accepted' AND apply_error IS NULL`, and
    this row now satisfies neither.
    """
    log.error("curation apply failed: proposal=%s %s", proposal_id, message)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE curation_proposals
               SET state = 'apply_failed', apply_error = $2, applied_at = NULL,
                   updated_at = now()
             WHERE id = $1
            """,
            proposal_id, message[:2000])


async def _finish(proposal_id: str, document_id: str | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE curation_proposals
               SET applied_document_id = $2, applied_at = now(), apply_error = NULL,
                   updated_at = now()
             WHERE id = $1
            """,
            proposal_id, document_id)


async def _doc_status(doc_id: str, client_id: str) -> tuple[str | None, str | None]:
    """Re-read the outcome `ingest_document` refused to raise. Tenant-scoped, like everything."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, error FROM kb_documents WHERE id = $1 AND client_id = $2",
            doc_id, client_id)
    if row is None:
        return None, "document disappeared during ingest"
    return row["status"], row["error"]


async def _verify_evidence(proposal_id: str, client_id: str) -> None:
    """Every piece of evidence must belong to the tenant whose KB we are about to write to.

    Runs BEFORE any write. `source_client_id` is stored separately from `client_id` for exactly
    this check — it is the one moment a human is looking, and the last chance to catch an
    upstream misattribution before it becomes an approved, permanent KB fact in the wrong tenant.
    """
    async with pool().acquire() as conn:
        bad = await conn.fetch(
            """
            SELECT id, source_client_id FROM curation_evidence
             WHERE proposal_id = $1 AND source_client_id <> $2
            """,
            proposal_id, client_id)
    if bad:
        raise ApplyError(
            f"cross-tenant evidence on proposal {proposal_id}: "
            f"{len(bad)} row(s) belong to another client (first: {bad[0]['source_client_id']})")


async def _curated_visibility(client_id: str) -> str:
    """The tenant's opt-in publishability choice for curated documents (see CURATED_VISIBILITY)."""
    async with pool().acquire() as conn:
        raw = await conn.fetchval("SELECT settings FROM clients WHERE id = $1", client_id)
    settings = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
    cur = settings.get("curation") if isinstance(settings, dict) else None
    value = str((cur or {}).get("curated_visibility") or "").strip().lower()
    return value if value in _VISIBILITIES else CURATED_VISIBILITY


async def _target_document(doc_id: str | None, client_id: str) -> dict:
    if not doc_id:
        raise ApplyError("proposal has no target_document_id")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, status, visibility FROM kb_documents WHERE id = $1 AND client_id = $2",
            doc_id, client_id)
    if row is None:
        # Not an error in the model's output — the document was deleted between the proposal
        # and the review. The card is stale; say so rather than resurrecting the document.
        raise ApplyError(f"target document {doc_id} no longer exists for this tenant")
    return dict(row)


# ---- the three operations ---------------------------------------------------
async def _apply_add(p: dict) -> str:
    """Create a curated document and ingest it (chunk + embed) so retrieval sees it.

    Visibility is `internal` unless the tenant opted in (see CURATED_VISIBILITY): curated content
    is answerable by the copilot and the fact-check path, and is published to the public autopilot
    only where the tenant has said its reviewer's approval is also the publishability decision.
    `source_type` is written HERE because `ingest_document` ignores its own argument (hazard 3).
    """
    client_id, content = p["client_id"], (p["proposed_content"] or "").strip()
    if not content:
        raise ApplyError("proposal has empty proposed_content")
    tags = [CURATED_TAG] + ([p["signal"]] if p["signal"] else [])
    actor = f"curation:{p['reviewed_by'] or 'unknown'}"
    visibility = await _curated_visibility(client_id)
    async with pool().acquire() as conn:
        doc_id = str(await conn.fetchval(
            """
            INSERT INTO kb_documents
                (client_id, doc_type, title, status, source_type, actor, visibility,
                 tags, metadata, content_text)
            VALUES ($1, $2, $3, 'pending', 'curation', $4, $8, $5, $6::jsonb, $7)
            RETURNING id
            """,
            client_id, CURATED_DOC_TYPE, (p["title"] or "Curated answer")[:500], actor,
            tags, json.dumps({"curation_proposal_id": str(p["id"]),
                              "cluster_key": p["cluster_key"],
                              "signal": p["signal"]}),
            content, visibility))
    # Connection released before the encoder call — TEI is one CPU and the pool is 10 slots.
    await kb_ingest.ingest_document(doc_id, client_id, "curation", text=content)
    status, err = await _doc_status(doc_id, client_id)
    if status != "ready":
        raise ApplyError(f"ingest of new document {doc_id} ended status={status}: {err}")
    return doc_id


async def _apply_update(p: dict) -> str:
    """Rewrite a document (or a single chunk) and RE-CHUNK + RE-EMBED it.

    Two shapes. A `target_chunk_id` means the model proposed a surgical edit to one chunk —
    `reembed_chunk` both rewrites the content and replaces that chunk's vector, which is
    correct and cheap. Otherwise the whole document's `content_text` is replaced and
    `ingest_document` re-runs the full pipeline. It is never `reembed_document` (hazard 1).
    """
    client_id, content = p["client_id"], (p["proposed_content"] or "").strip()
    if not content:
        raise ApplyError("proposal has empty proposed_content")

    if p["target_chunk_id"]:
        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT document_id FROM kb_chunks WHERE id = $1 AND client_id = $2",
                p["target_chunk_id"], client_id)
        if row is None:
            raise ApplyError(f"target chunk {p['target_chunk_id']} no longer exists for this tenant")
        doc_id = str(row["document_id"])
        # Raises on failure (unlike ingest_document), so no status reconciliation is needed —
        # the exception propagates to apply_accepted and lands the card in apply_failed.
        await kb_ingest.reembed_chunk(str(p["target_chunk_id"]), client_id, new_content=content)
        async with pool().acquire() as conn:
            await conn.execute(
                "UPDATE kb_documents SET updated_at = now() WHERE id = $1 AND client_id = $2",
                doc_id, client_id)
        return doc_id

    doc = await _target_document(str(p["target_document_id"]) if p["target_document_id"] else None,
                                 client_id)
    doc_id = str(doc["id"])
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_documents SET content_text = $3, updated_at = now() "
            "WHERE id = $1 AND client_id = $2",
            doc_id, client_id, content)
    await kb_ingest.ingest_document(doc_id, client_id, "curation", text=content)
    status, err = await _doc_status(doc_id, client_id)
    if status != "ready":
        # content_text is already the new text and the chunks may be half-written. Surfacing
        # this as apply_failed is the point: a human sees a broken document rather than a
        # green tick over a document that no longer retrieves.
        raise ApplyError(f"re-ingest of document {doc_id} ended status={status}: {err}")
    return doc_id


async def _apply_remove(p: dict) -> str:
    """Retire a document from retrieval. NEVER a hard delete.

    `visibility='internal'` is a slight misnomer here and is deliberate: the KB-admin console
    already filters on it, and the document keeps existing, keeps its audit trail, and can be
    restored by a human in one click. The actual deletion decision is surfaced as a candidate in
    the activity log for a person to make.
    """
    client_id = p["client_id"]
    doc = await _target_document(str(p["target_document_id"]) if p["target_document_id"] else None,
                                 client_id)
    doc_id = str(doc["id"])
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_documents SET visibility = 'internal', updated_at = now() "
            "WHERE id = $1 AND client_id = $2",
            doc_id, client_id)
    await kb_events.log(
        client_id, "curation_delete_candidate", document_id=doc_id, method="curation", status="ok",
        actor=f"curation:{p['reviewed_by'] or 'unknown'}",
        detail=(f"Hidden from retrieval by an accepted curation proposal ({p['id']}). "
                f"Not deleted — a human must confirm deletion. Reason: {p['rationale'] or '-'}"))
    return doc_id


_OPS = {"add": _apply_add, "update": _apply_update, "remove": _apply_remove}


# ---- entry point ------------------------------------------------------------
async def apply_accepted(proposal_id: str) -> None:
    """Apply one accepted proposal to the KB. Never raises: every outcome is recorded on the row.

    The caller (the worker's 5 s poll, or an admin route) gets no return value on purpose —
    the proposal row is the single source of truth for what happened, and a caller that
    branched on an exception would disagree with the reviewer's screen.
    """
    # Atomic claim. The predicate is the same one the worker polls on, plus a staleness escape
    # so a process killed mid-apply (routine: every push auto-deploys) releases the card.
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE curation_proposals
               SET applied_at = now(), updated_at = now()
             WHERE id = $1
               AND state = 'accepted'
               AND applied_document_id IS NULL
               AND apply_error IS NULL
               AND (applied_at IS NULL OR applied_at < now() - interval '{CLAIM_STALE}')
            RETURNING id, client_id, op, signal, cluster_key, title, proposed_content, rationale,
                      target_document_id, target_chunk_id, suggested_tags, reviewed_by
            """,
            proposal_id)
    if row is None:
        # Already applied, not accepted, already failed, or claimed by someone else. A normal
        # skip in a polling design, not an error.
        log.debug("curation apply: proposal %s not claimable", proposal_id)
        return

    p = dict(row)
    p["id"] = str(p["id"])
    p["client_id"] = str(p["client_id"])

    try:
        # Order matters: the cross-tenant check runs before the first write, not after it.
        await _verify_evidence(p["id"], p["client_id"])
        handler = _OPS.get(p["op"])
        if handler is None:
            raise ApplyError(f"unknown op {p['op']!r}")
        doc_id = await handler(p)
    except Exception as exc:  # noqa: BLE001 — the outcome belongs on the row, not in a traceback
        log.exception("curation apply raised: proposal=%s op=%s", p["id"], p["op"])
        await _fail(p["id"], f"{type(exc).__name__}: {exc}")
        return

    await _finish(p["id"], doc_id)
    # One kb_events row per accept, so the EXISTING GET /admin/kb/{tid}/activity timeline covers
    # the KB's whole life — imports, manual edits and curation — with zero new UI. `action` has
    # no CHECK constraint in kb.sql, so a new verb needs no migration.
    await kb_events.log(
        p["client_id"], "curation_accept", document_id=doc_id, method="curation", status="ok",
        actor=f"curation:{p['reviewed_by'] or 'unknown'}",
        detail=f"{p['op']}: {p['title'] or p['cluster_key']} (proposal {p['id']}, signal {p['signal']})")
    log.info("curation applied: proposal=%s op=%s client=%s doc=%s",
             p["id"], p["op"], p["client_id"], doc_id)


async def record_decline(client_id: str, proposal_id: str, reviewer: str, reason: str,
                         *, suppress_days: int | None = 90) -> str:
    """Decline a card: state, suppression and audit, as ONE operation.

    This is the *only* decline implementation. The review route used to re-do the state update
    and the suppression INSERT inline and simply omit the `kb_events` row, which left two
    divergent suppression paths of which the audited one was the unused one — so a decline, a
    real decision about a tenant's KB, was invisible in the activity timeline that is supposed to
    account for every KB change.

    Returns `'not_found'`, the card's CURRENT state when it is no longer decidable, or
    `'declined'`. The route turns those into 404 / 409 / 200; nothing here raises for a losing
    race, because two reviewers clicking at once is normal.

    Suppression is matched SEMANTICALLY by the next run's clustering (medoids are re-embedded in
    the same batch and anything within cosine 0.88 is dropped before a single Claude call). A
    content hash would be defeated by any rewording, and rewording is what conversational traffic
    IS. Idempotent: the upsert and the event write can both be repeated harmlessly.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            p = await conn.fetchrow(
                "SELECT id, client_id, state, cluster_key, cluster_medoid, op, title "
                "FROM curation_proposals WHERE id = $1 AND client_id = $2 FOR UPDATE",
                proposal_id, client_id)
            if p is None:
                return "not_found"
            if p["state"] != "pending":
                return str(p["state"])
            await conn.execute(
                "UPDATE curation_proposals SET state = 'declined', decline_reason = $3, "
                "reviewed_by = $4, reviewed_at = now(), updated_at = now() "
                "WHERE id = $1 AND client_id = $2",
                proposal_id, client_id, (reason or "")[:2000] or None, reviewer)
            await conn.execute(
                """
                INSERT INTO curation_suppressions
                    (client_id, cluster_key, medoid, reason, proposal_id, until, created_by)
                VALUES ($1, $2, $3, $4, $5,
                        CASE WHEN $6::int IS NULL THEN NULL
                             ELSE now() + ($6::int * interval '1 day') END,
                        $7)
                ON CONFLICT (client_id, cluster_key) DO UPDATE
                    SET medoid = EXCLUDED.medoid, reason = EXCLUDED.reason,
                        proposal_id = EXCLUDED.proposal_id, until = EXCLUDED.until,
                        created_by = EXCLUDED.created_by
                """,
                p["client_id"], p["cluster_key"], p["cluster_medoid"],
                (reason or "")[:2000] or None, p["id"], suppress_days, reviewer)
    # Outside the transaction: the audit row must not be able to roll the decision back, and
    # kb_events.log acquires its own connection.
    await kb_events.log(
        str(p["client_id"]), "curation_decline", document_id=None, method="curation", status="ok",
        actor=f"curation:{reviewer}",
        detail=f"{p['op']}: {p['title'] or p['cluster_key']} declined — {reason or 'no reason given'}")
    return "declined"
