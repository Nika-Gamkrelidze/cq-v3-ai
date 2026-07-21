"""Harvest the KB gaps the system already labelled for free.

THE PRINCIPLE: do not summarise yesterday's conversations. A tenant with 5,000 threads a
day would need 5,000 model calls to be "read", and the result would be a summary of small
talk. Instead we mine the six places where the platform has ALREADY written down, in a
plain column, that it could not answer something:

  S1  chat_turns.grounded = false            — the deterministic gate refused to answer.
                                               Served by the partial index
                                               `idx_chat_turns_ungrounded`.
  S2  method = 'keyword' / 'none', or        — the answer only survived on the lexical
      top_score below the tenant's min_score   fallback, or barely cleared the floor.
  S3  copilot_feedback.action='edited_sent'  — THE GOLD SIGNAL. `final_text` is a human
      with a large edit distance               operator's own correct answer to a real
                                               customer question. Nothing else in the
                                               system produces supervised text like this,
                                               so it is weighted highest and it is the one
                                               signal that can justify a card on its own.
  S4  an operator turn immediately after a   — a human stepped in after the bot failed;
      failed/ungrounded bot turn               their reply is, again, a correct answer.
  S5  audio_jobs.kb_check verdict NOT_IN_KB  — a call asserted something the KB has no
                                               entry for.
  S6  audio_jobs.kb_check verdict CONTRADICTED — the KB says something DIFFERENT. Highest
                                               precision of the six, and the only one that
                                               argues for `update`/`remove` rather than
                                               `add`.

Every one of the six is pure SQL against existing columns. This whole module spends zero
tokens; the first (and only) Claude call happens one stage later, once per CLUSTER.

RUNBOOK — cold start is CHAT-driven, not call-driven (restated from db/curation.sql
because this is the file where it bites):

  * S5/S6 read `audio_jobs.kb_check`. That column is computed ONLY when the principal is a
    tenant AND that tenant's KB already has chunks, and the whole block sits inside a bare
    `except` in services/analysis.py. So it is null for exactly the thin-KB tenant this
    loop exists to help. Do not expect S5/S6 on day one.
  * S1/S2 read the grounding telemetry on *bot* turns. Until the public autopilot (P3)
    mirrors its own replies into `chat_turns`, that telemetry lives on
    `copilot_suggestions` instead, so S1/S2 are harvested from BOTH tables — a refused or
    weakly-grounded copilot generation is the same event as an ungrounded bot turn, and in
    P1-only deployments it is the only place the event is recorded.
  * A tenant with no chat traffic yet legitimately produces zero candidates. That is a
    correct outcome, not a bug.

ROLE FILTERING IS LOAD-BEARING: `chat_turns.grounded` DEFAULTS to false and `append_turn`
writes bool(grounded) for every role, so an inbound CUSTOMER message is stored ungrounded
too. Selecting S1 without `role = 'bot'` would therefore flag every customer message in the
system as a KB gap and flood the review queue with the questions themselves. Every
turn-level predicate below names the role explicitly.

RESUMABILITY: `chat_conversations.mined_through` is advanced PER CONVERSATION as we go,
in the same order we read them (oldest first). A crash — and every push to `main`
auto-deploys and restarts the stack, so mid-run crashes are routine — therefore resumes
from where it stopped instead of restarting the tenant's day. `audio_jobs` has no
equivalent watermark column (and this track does not own kb.sql), so calls are re-read
within the run window; the duplicate is absorbed one stage later by clustering plus the
partial unique index on (client_id, cluster_key) WHERE state = 'pending'.
"""
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from ...db import pool
from .. import chat_store

log = logging.getLogger("cq")

# Per-run harvest caps. Both are conversation/call counts, NOT candidate counts: the point
# is to bound the SQL, and one thread rarely yields more than a couple of candidates.
# Oldest-first + the watermark means a tenant over the cap catches up over subsequent runs
# rather than permanently losing its backlog.
CONVERSATION_CAP = 500
AUDIO_CAP = 200

# Relative importance of each signal. Used here to break ties when the same question is
# flagged twice (an ungrounded bot turn AND the operator answer that followed it are the
# same gap — keep the one that carries a human-authored answer), and exported because
# propose/runner fold it into the proposal's precomputed `priority`.
SIGNAL_WEIGHT: dict[str, float] = {
    "S3": 3.0,   # operator rewrote the AI's answer — supervised text
    "S4": 2.2,   # operator answered after the bot failed — supervised text
    "S6": 1.8,   # KB actively contradicts what was said
    "S5": 1.2,   # claim absent from the KB
    "S2": 0.8,   # weak/lexical-only retrieval
    "S1": 0.6,   # refusal
}

# An `edited_sent` with a *small* edit is a typo fix or a greeting tweak, not a KB gap.
# Normalised distance = 1 - difflib ratio; a third of the text rewritten is the floor.
EDIT_DISTANCE_MIN = 0.34
# difflib is quadratic-ish; suggestions and replies are short, and clipping both sides to
# the same budget keeps a pathological paste from stalling the nightly job.
EDIT_DISTANCE_CLIP = 2000

# Display budgets. The question becomes a cluster medoid and a card headline; the answer
# becomes the seed of the proposed KB content.
MAX_QUESTION_CHARS = 600
MAX_ANSWER_CHARS = 4000

_WS = re.compile(r"\s+")


@dataclass
class Candidate:
    """One labelled failure. Cheap, verbatim, and not yet clustered.

    `question` keeps the customer's ORIGINAL wording — verbatim quotes are what make a
    review card feel real rather than machine-generated, and they are the audit trail for
    the KB change. Normalisation (see `normalise_question`) is applied for the cluster key
    only, never to what a human reads.
    """
    client_id: str
    signal: str
    question: str
    answer: str | None
    source_kind: str          # chat_turn | copilot_suggestion | copilot_feedback | audio_job
    source_id: str | None
    channel: str | None       # web | instagram | messenger | whatsapp | call
    occurred_at: datetime
    target_document_id: str | None = None
    target_chunk_id: str | None = None
    # Additive beyond the fixed contract, both keyword-defaulted so positional construction
    # is unchanged: curation_evidence has `locale` and `conversation_id` columns and there
    # would otherwise be nowhere to carry them from the only place that knows them.
    locale: str | None = None
    metadata: dict = field(default_factory=dict)


def normalise_question(text: str | None) -> str:
    """Collapse whitespace and trim. Display-safe — case is preserved.

    The lowercasing that the cluster key needs happens in cluster.py, on top of this. Doing
    it here would mean the medoid stored on the proposal (and shown on the card) is a
    lowercased mangling of what the customer actually typed.
    """
    return _WS.sub(" ", str(text or "")).strip()


def _clip(text: str | None, limit: int) -> str:
    t = normalise_question(text)
    return t[:limit]


def _json(value):
    """jsonb -> python. asyncpg returns jsonb as str with no codec registered (house
    workaround, same as chat_store._json)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _edit_distance(before: str | None, after: str | None) -> float | None:
    """Normalised 0..1 distance between the AI's text and what the operator actually sent.

    None means "unknown" — see the S3 harvest for why an unknown distance still yields a
    candidate.
    """
    a = normalise_question(before)[:EDIT_DISTANCE_CLIP]
    b = normalise_question(after)[:EDIT_DISTANCE_CLIP]
    if not a or not b:
        return None
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# S1 / S2 / S4 — chat turns
# ---------------------------------------------------------------------------
# One query, not N+1. The question behind a failure is the PRECEDING customer turn, which
# is not necessarily the immediately preceding row (bot -> bot -> operator sequences are
# normal), so a running counter over customer turns partitions each thread into
# "question groups": every group starts with a customer turn, and `first_value` over the
# whole group frame therefore yields that question for every later row in it. `lag` on the
# thread-level window supplies the "immediately following a failed bot turn" test for S4.
_TURNS_SQL = """
WITH turns AS (
    SELECT t.id, t.conversation_id, t.role, t.content, t.locale, t.grounded,
           t.method, t.top_score, t.created_at,
           sum(CASE WHEN t.role = 'customer' THEN 1 ELSE 0 END)
               OVER (PARTITION BY t.conversation_id ORDER BY t.created_at, t.id
                     ROWS UNBOUNDED PRECEDING) AS qgrp
    FROM chat_turns t
    WHERE t.client_id = $1
      AND t.conversation_id = ANY($2::uuid[])
      AND t.created_at < $4
),
marked AS (
    SELECT t.*,
           first_value(t.id)         OVER q AS question_id,
           first_value(t.content)    OVER q AS question,
           first_value(t.locale)     OVER q AS question_locale,
           lag(t.role)               OVER o AS prev_role,
           lag(t.grounded)           OVER o AS prev_grounded
    FROM turns t
    WINDOW q AS (PARTITION BY t.conversation_id, t.qgrp ORDER BY t.created_at, t.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING),
           o AS (PARTITION BY t.conversation_id ORDER BY t.created_at, t.id)
)
SELECT id, conversation_id, role, content, locale, grounded, method, top_score,
       created_at, question_id, question, question_locale, prev_role, prev_grounded
FROM marked
WHERE qgrp > 0
  AND question IS NOT NULL AND btrim(question) <> ''
  AND created_at >= $3 AND created_at < $4
  AND (
        (role = 'bot' AND grounded = false)
     OR (role = 'bot' AND (method IN ('keyword', 'none')
                           OR (top_score IS NOT NULL AND top_score < $5)))
     OR (role = 'operator' AND prev_role = 'bot' AND prev_grounded = false
         AND content IS NOT NULL AND btrim(content) <> '')
      )
ORDER BY conversation_id, created_at, id
"""

# ---------------------------------------------------------------------------
# S1 / S2 — copilot generations
# ---------------------------------------------------------------------------
# The same two events, recorded on the other table. In an assist-only (P1) deployment the
# bot never writes a turn, so this is where "the gate refused" and "we only had a weak hit"
# actually live. `state = 'refused'` is a deliberate, successful outcome of the gate — it is
# precisely a labelled KB gap.
#
# The top_score comparison is wrapped in a CASE guarded by jsonb_typeof: `grounding` is a
# free-form jsonb blob and an unguarded ::double precision cast over a non-numeric value
# would abort the whole nightly run for that tenant.
_SUGGESTIONS_SQL = """
SELECT s.id, s.created_at, s.state, s.grounding, s.conversation_id, s.locale AS s_locale,
       c.channel, c.locale AS conv_locale,
       q.id AS question_id, q.content AS question, q.locale AS question_locale
FROM copilot_suggestions s
LEFT JOIN chat_conversations c
       ON c.id = s.conversation_id AND c.client_id = s.client_id
LEFT JOIN LATERAL (
    SELECT ct.id, ct.content, ct.locale
    FROM chat_turns ct
    WHERE ct.client_id = s.client_id
      AND ct.conversation_id = s.conversation_id
      AND ct.role = 'customer'
      AND ct.created_at <= s.created_at
      AND ct.content IS NOT NULL AND btrim(ct.content) <> ''
    ORDER BY ct.created_at DESC, ct.id DESC
    LIMIT 1
) q ON true
WHERE s.client_id = $1
  AND s.created_at >= $2 AND s.created_at < $3
  AND (
        s.state = 'refused'
     OR s.grounding->>'grounded' = 'false'
     OR s.grounding->>'method' IN ('keyword', 'none')
     OR (CASE WHEN jsonb_typeof(s.grounding->'top_score') = 'number'
              THEN (s.grounding->>'top_score')::double precision END) < $4
      )
ORDER BY s.created_at ASC, s.id ASC
LIMIT $5
"""

# ---------------------------------------------------------------------------
# S3 — operator corrections (the gold signal)
# ---------------------------------------------------------------------------
# `final_text` is a human-authored correct answer to a real customer question. The lateral
# resolves the question the same way the suggestions query does rather than trusting
# s.turn_id, which is nullable (a suggestion may be claimed before the turn lands).
#
# `edit_distance` is read from metadata, not from a column: db/chat.sql has no
# copilot_feedback.edit_distance today and this track does not own that file. The expression
# is regex-guarded for the same reason as the cast above, and returns NULL when absent — the
# distance is then computed here from `suggestions` -> `final_text`.
_FEEDBACK_SQL = """
SELECT f.id, f.created_at, f.final_text, f.suggestion_index, f.metadata,
       (CASE WHEN f.metadata->>'edit_distance' ~ '^[0-9]*[.]?[0-9]+$'
             THEN (f.metadata->>'edit_distance')::double precision END) AS meta_distance,
       s.id AS suggestion_id, s.suggestions, s.conversation_id, s.locale AS s_locale,
       c.channel, c.locale AS conv_locale,
       q.id AS question_id, q.content AS question, q.locale AS question_locale
FROM copilot_feedback f
JOIN copilot_suggestions s
     ON s.id = f.suggestion_id AND s.client_id = f.client_id
LEFT JOIN chat_conversations c
       ON c.id = s.conversation_id AND c.client_id = f.client_id
LEFT JOIN LATERAL (
    SELECT ct.id, ct.content, ct.locale
    FROM chat_turns ct
    WHERE ct.client_id = f.client_id
      AND ct.conversation_id = s.conversation_id
      AND ct.role = 'customer'
      AND ct.created_at <= f.created_at
      AND ct.content IS NOT NULL AND btrim(ct.content) <> ''
    ORDER BY ct.created_at DESC, ct.id DESC
    LIMIT 1
) q ON true
WHERE f.client_id = $1
  AND f.created_at >= $2 AND f.created_at < $3
  AND f.action = 'edited_sent'
  AND f.final_text IS NOT NULL AND btrim(f.final_text) <> ''
ORDER BY f.created_at ASC, f.id ASC
LIMIT $4
"""

# ---------------------------------------------------------------------------
# S5 / S6 — call fact-check verdicts
# ---------------------------------------------------------------------------
# `kb_check` is the shape services/factcheck.run_factcheck returns:
#   {accuracy_score, counts{...}, claims:[{claim, speaker, category, verdict, rationale,
#    confidence, evidence{title, doc_type, snippet, score}}], contradicted:[...]}
# Read, not guessed. Note `evidence` carries no document_id, so a CONTRADICTED claim cannot
# name a `target_document_id` from here — propose.py re-retrieves to find the offending
# passage.
_AUDIO_SQL = """
SELECT id, created_at, language, kb_check
FROM audio_jobs
WHERE client_id = $1
  AND created_at >= $2 AND created_at < $3
  AND kb_check IS NOT NULL
ORDER BY created_at ASC, id ASC
LIMIT $4
"""


async def collect(client_id: str, since, until, cap: int = CONVERSATION_CAP) -> list[Candidate]:
    """Harvest one tenant's labelled failures in [since, until). Zero tokens.

    Returns candidates ordered oldest-first, de-duplicated so that one customer question
    yields at most one candidate (the highest-weighted signal that flagged it). Never
    raises for lack of data: an empty list is a correct answer for a quiet tenant.
    """
    if not client_id:
        return []
    conv_cap = max(1, int(cap or CONVERSATION_CAP))

    # The tenant's own retrieval floor — the same number the chat gate uses, so "weak
    # retrieval" here means exactly what it meant when the answer was served. Reading the
    # global default instead would flag one tenant's healthy hits and miss another's.
    cfg = await chat_store.get_chat_config(client_id)
    try:
        min_score = float(cfg.get("min_score") or chat_store.CHAT_CONFIG_DEFAULTS["min_score"])
    except (TypeError, ValueError):
        min_score = float(chat_store.CHAT_CONFIG_DEFAULTS["min_score"])

    out: list[Candidate] = []
    out.extend(await _collect_chat(client_id, since, until, conv_cap, min_score))
    out.extend(await _collect_suggestions(client_id, since, until, conv_cap, min_score))
    out.extend(await _collect_feedback(client_id, since, until, conv_cap))
    out.extend(await _collect_audio(client_id, since, until))

    deduped = _dedupe(out)
    deduped.sort(key=lambda c: c.occurred_at)
    log.info("curation miner: client=%s candidates=%d (raw=%d) window=%s..%s",
             client_id, len(deduped), len(out), since, until)
    return deduped


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    """One customer question -> one candidate.

    An ungrounded bot turn (S1), the weak retrieval behind it (S2) and the operator reply
    that fixed it (S4) are three rows describing ONE gap. Clustering would merge them
    anyway, but they would first inflate `occurrences` — the number a human reads as "this
    was asked 12 times" — into a lie, and they would let a single conversation satisfy the
    `distinct_sources >= 2` poisoning defence on its own.
    """
    best: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    for c in cands:
        key = (c.source_kind if c.source_kind == "audio_job" else "chat",
               c.metadata.get("question_id") or c.source_id, normalise_question(c.question).lower())
        cur = best.get(key)
        if cur is None:
            best[key] = c
            order.append(key)
            continue
        if SIGNAL_WEIGHT.get(c.signal, 0.0) > SIGNAL_WEIGHT.get(cur.signal, 0.0):
            best[key] = c
    return [best[k] for k in order]


async def _collect_chat(client_id: str, since, until, cap: int, min_score: float) -> list[Candidate]:
    """S1 / S2 / S4, plus the resumable watermark."""
    async with pool().acquire() as conn:
        convs = await conn.fetch(
            """
            SELECT id, channel, locale, mined_through
            FROM chat_conversations
            WHERE client_id = $1
              AND last_message_at >= $2 AND last_message_at < $3
              AND (mined_through IS NULL OR mined_through < last_message_at)
            ORDER BY last_message_at ASC
            LIMIT $4
            """,
            client_id, since, until, cap)
        if not convs:
            return []

        meta = {str(r["id"]): r for r in convs}
        rows = await conn.fetch(_TURNS_SQL, client_id, list(meta.keys()), since, until, min_score)

        by_conv: dict[str, list] = {cid: [] for cid in meta}
        for r in rows:
            by_conv.setdefault(str(r["conversation_id"]), []).append(r)

        out: list[Candidate] = []
        # Oldest-first, advancing the watermark PER CONVERSATION: a crash mid-tenant
        # resumes at the next conversation instead of re-mining the whole day. Each UPDATE
        # is its own statement (no wrapping transaction) precisely so partial progress
        # survives. No LLM or embedding await happens inside this connection.
        for cid in meta:
            # `mined_through` is the per-conversation floor, and it is enforced HERE rather than
            # in _TURNS_SQL's `created_at >= $3`: that bound is the RUN's window start, which is
            # the same for every conversation. A thread already mined up to noon would otherwise
            # have its morning re-harvested and re-proposed on the next pass, and the reviewer
            # would be shown decisions they already made.
            floor = meta[cid]["mined_through"]
            for r in by_conv.get(cid, []):
                if floor is not None and r["created_at"] < floor:
                    continue
                cand = _chat_candidate(client_id, r, meta[cid], min_score)
                if cand is not None:
                    out.append(cand)
            await conn.execute(
                """
                UPDATE chat_conversations
                   SET mined_through = $3, updated_at = now()
                 WHERE client_id = $1 AND id = $2
                   AND (mined_through IS NULL OR mined_through < $3)
                """,
                client_id, cid, until)
    return out


def _chat_candidate(client_id: str, r, conv, min_score: float) -> Candidate | None:
    question = _clip(r["question"], MAX_QUESTION_CHARS)
    if not question:
        return None
    role = r["role"]
    if role == "operator":
        signal, answer = "S4", _clip(r["content"], MAX_ANSWER_CHARS)
        if not answer:
            return None
    elif r["method"] in ("keyword", "none") or (
            r["top_score"] is not None and float(r["top_score"]) < min_score):
        signal, answer = "S2", None
    else:
        signal, answer = "S1", None
    return Candidate(
        client_id=client_id,
        signal=signal,
        question=question,
        answer=answer,
        source_kind="chat_turn",
        source_id=str(r["id"]),
        channel=conv["channel"],
        occurred_at=r["created_at"],
        locale=r["question_locale"] or conv["locale"],
        metadata={
            "question_id": str(r["question_id"]) if r["question_id"] else None,
            "conversation_id": str(r["conversation_id"]),
            "method": r["method"],
            "top_score": r["top_score"],
        },
    )


async def _collect_suggestions(client_id: str, since, until, cap: int, min_score: float) -> list[Candidate]:
    """S1 / S2 as recorded on copilot_suggestions — the only place they exist pre-autopilot."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(_SUGGESTIONS_SQL, client_id, since, until, min_score, cap)

    out: list[Candidate] = []
    for r in rows:
        question = _clip(r["question"], MAX_QUESTION_CHARS)
        if not question:
            continue
        g = _json(r["grounding"]) or {}
        method = g.get("method")
        try:
            top_score = float(g["top_score"]) if g.get("top_score") is not None else None
        except (TypeError, ValueError):
            top_score = None
        weak = method in ("keyword", "none") or (top_score is not None and top_score < min_score)
        signal = "S2" if (weak and r["state"] != "refused") else "S1"
        out.append(Candidate(
            client_id=client_id,
            signal=signal,
            question=question,
            answer=None,
            source_kind="copilot_suggestion",
            source_id=str(r["id"]),
            channel=r["channel"] or "web",
            occurred_at=r["created_at"],
            locale=r["question_locale"] or r["s_locale"] or r["conv_locale"],
            metadata={
                "question_id": str(r["question_id"]) if r["question_id"] else None,
                "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
                "state": r["state"],
                "method": method,
                "top_score": top_score,
            },
        ))
    return out


async def _collect_feedback(client_id: str, since, until, cap: int) -> list[Candidate]:
    """S3 — the operator rewrote the AI's answer before sending it."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(_FEEDBACK_SQL, client_id, since, until, cap)

    out: list[Candidate] = []
    for r in rows:
        question = _clip(r["question"], MAX_QUESTION_CHARS)
        answer = _clip(r["final_text"], MAX_ANSWER_CHARS)
        if not question or not answer:
            continue
        distance = r["meta_distance"]
        if distance is None:
            distance = _edit_distance(_suggestion_text(r["suggestions"], r["suggestion_index"]), answer)
        # An UNKNOWN distance still yields a candidate: `edited_sent` already means the
        # operator did not send what the model wrote, and dropping the row would discard
        # the highest-quality signal in the system over a missing measurement. A KNOWN but
        # small distance is a typo fix and is dropped.
        if distance is not None and distance < EDIT_DISTANCE_MIN:
            continue
        out.append(Candidate(
            client_id=client_id,
            signal="S3",
            question=question,
            answer=answer,
            source_kind="copilot_feedback",
            source_id=str(r["id"]),
            channel=r["channel"] or "web",
            occurred_at=r["created_at"],
            locale=r["question_locale"] or r["s_locale"] or r["conv_locale"],
            metadata={
                "question_id": str(r["question_id"]) if r["question_id"] else None,
                "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
                "suggestion_id": str(r["suggestion_id"]),
                "edit_distance": round(distance, 3) if distance is not None else None,
            },
        ))
    return out


def _suggestion_text(blob, index) -> str | None:
    """The variant the operator started from: `suggestions` is [{kind, text}, ...]."""
    items = _json(blob) or []
    if not isinstance(items, list) or not items:
        return None
    try:
        i = int(index)
    except (TypeError, ValueError):
        i = 0
    if not 0 <= i < len(items):
        i = 0
    item = items[i]
    return item.get("text") if isinstance(item, dict) else None


async def _collect_audio(client_id: str, since, until) -> list[Candidate]:
    """S5 / S6 — fact-check verdicts on analysed calls. Null on thin-KB tenants (see the
    module runbook), so this contributes nothing at cold start and that is expected."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(_AUDIO_SQL, client_id, since, until, AUDIO_CAP)

    out: list[Candidate] = []
    for r in rows:
        kb_check = _json(r["kb_check"]) or {}
        claims = kb_check.get("claims") if isinstance(kb_check, dict) else None
        for c in (claims or []):
            if not isinstance(c, dict):
                continue
            verdict = str(c.get("verdict") or "").upper()
            if verdict == "NOT_IN_KB":
                signal = "S5"
            elif verdict == "CONTRADICTED":
                signal = "S6"
            else:
                continue
            # Only what the AGENT asserted is actionable: a customer's own wrong statement
            # is not a gap in the tenant's knowledge base.
            if str(c.get("speaker") or "").lower() == "customer":
                continue
            question = _clip(c.get("claim"), MAX_QUESTION_CHARS)
            if not question:
                continue
            evidence = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
            out.append(Candidate(
                client_id=client_id,
                signal=signal,
                question=question,
                answer=None,
                source_kind="audio_job",
                source_id=str(r["id"]),
                channel="call",
                occurred_at=r["created_at"],
                locale=r["language"],
                metadata={
                    "verdict": verdict,
                    "category": c.get("category"),
                    "rationale": c.get("rationale"),
                    "confidence": c.get("confidence"),
                    # Verbatim KB text the claim contradicts. propose.py needs it to argue
                    # for an `update`; the evidence blob carries no document_id, so the
                    # target has to be re-retrieved there.
                    "kb_snippet": (evidence or {}).get("snippet"),
                    "kb_title": (evidence or {}).get("title"),
                },
            ))
    return out
