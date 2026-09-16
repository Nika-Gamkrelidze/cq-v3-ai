"""What each tenant consumed, broken down the four ways a bill gets argued about.

`llm_usage` holds one row per AI call. This turns those rows into the answers an operator
needs when pricing a subscription or explaining an invoice:

  * per TENANT      — the headline number
  * per USER        — which of their people is driving it
  * per FEATURE     — which part of the product
  * per RECORDING   — the individual call a line item came from

Everything is read-only and superadmin-scoped; the aggregation is done in SQL because the
alternative is pulling a month of call rows into Python to add up.

A NOTE ON "COST". Tokens are counted, not priced: a price per million varies by model and by
contract, so the console shows tokens and lets the operator apply their own rate. Rows where
the tenant supplied their own key are still counted — "what did this workspace consume" is a
support question even when the answer costs us nothing — and flagged so the two are never
silently added together.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from ..db import pool

# The windows the console offers. Anything longer is a data-export question, not a dashboard.
WINDOWS = {"24h": timedelta(days=1), "7d": timedelta(days=7),
           "30d": timedelta(days=30), "90d": timedelta(days=90)}
DEFAULT_WINDOW = "30d"


def _interval(window: str) -> timedelta:
    """A timedelta, not the SQL text: asyncpg binds an `interval` parameter from a timedelta
    and rejects a string like '30 days' outright (it surfaces as a DataError, which the app's
    global handler turns into a flat 400 with no clue what was wrong)."""
    return WINDOWS.get(window, WINDOWS[DEFAULT_WINDOW])


# Every report sums the same four token columns, so the expression lives once.
_SUMS = """
        COALESCE(SUM(input_tokens), 0)::bigint          AS input_tokens,
        COALESCE(SUM(output_tokens), 0)::bigint         AS output_tokens,
        COALESCE(SUM(cache_read_tokens), 0)::bigint     AS cache_read_tokens,
        COALESCE(SUM(cache_creation_tokens), 0)::bigint AS cache_creation_tokens,
        COALESCE(SUM(COALESCE(input_tokens,0) + COALESCE(output_tokens,0)
                   + COALESCE(cache_read_tokens,0) + COALESCE(cache_creation_tokens,0)),
                 0)::bigint                             AS total_tokens,
        -- Of that total, the part that ran on the TENANT'S own key. Reported separately, never
        -- subtracted here: "what did this workspace consume" and "what did it cost us" are
        -- different questions and the console answers both from the same row.
        COALESCE(SUM(COALESCE(input_tokens,0) + COALESCE(output_tokens,0)
                   + COALESCE(cache_read_tokens,0) + COALESCE(cache_creation_tokens,0))
                 FILTER (WHERE byo), 0)::bigint         AS byo_tokens,
        COUNT(*)::bigint                                AS calls,
        COUNT(*) FILTER (WHERE NOT ok)::bigint          AS failed
"""


async def totals_by_tenant(window: str = DEFAULT_WINDOW) -> list[dict]:
    """Every tenant that used AI in the window, biggest first.

    LEFT JOIN from usage to clients, not the other way round: a tenant deleted since the call
    was made still has to appear, or the totals stop adding up. `llm_usage.client_id` is
    ON DELETE SET NULL, so those rows survive with a null id and are reported as unattributed
    rather than dropped.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT u.client_id,
                   COALESCE(c.name, '—') AS name,
                   c.slug,
                   {_SUMS},
                   MAX(u.created_at) AS last_used,
                   COUNT(DISTINCT u.model)   AS models,
                   COUNT(DISTINCT u.feature) AS features
            FROM llm_usage u
            LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.created_at > now() - $1::interval
            GROUP BY u.client_id, c.name, c.slug
            ORDER BY total_tokens DESC
        """, _interval(window))
    return [_row(r) for r in rows]


async def tenant_breakdown(client_id: str, window: str = DEFAULT_WINDOW) -> dict:
    """One tenant, sliced by user, by feature, by model and by recording."""
    interval = _interval(window)
    async with pool().acquire() as conn:
        total = await conn.fetchrow(f"""
            SELECT {_SUMS} FROM llm_usage
            WHERE client_id = $1 AND created_at > now() - $2::interval
        """, client_id, interval)

        by_user = await conn.fetch(f"""
            SELECT COALESCE(actor, 'unattributed') AS actor, {_SUMS},
                   MAX(created_at) AS last_used
            FROM llm_usage
            WHERE client_id = $1 AND created_at > now() - $2::interval
            GROUP BY actor ORDER BY total_tokens DESC
        """, client_id, interval)

        by_feature = await conn.fetch(f"""
            SELECT feature, {_SUMS}, MAX(created_at) AS last_used
            FROM llm_usage
            WHERE client_id = $1 AND created_at > now() - $2::interval
            GROUP BY feature ORDER BY total_tokens DESC
        """, client_id, interval)

        by_model = await conn.fetch(f"""
            SELECT model, {_SUMS}, MAX(created_at) AS last_used
            FROM llm_usage
            WHERE client_id = $1 AND created_at > now() - $2::interval
            GROUP BY model ORDER BY total_tokens DESC
        """, client_id, interval)

        # The recording each line came from. LEFT JOIN because retention deletes recordings
        # long before anyone stops asking what a month cost — a purged call still owes its
        # tokens to the total, so it is reported with whatever identity survives.
        by_job = await conn.fetch(f"""
            SELECT u.job_id, {_SUMS},
                   MAX(u.created_at) AS last_used,
                   MAX(j.filename)   AS filename,
                   MAX(j.created_at) AS job_created_at
            FROM llm_usage u
            LEFT JOIN audio_jobs j ON j.id = u.job_id
            WHERE u.client_id = $1 AND u.created_at > now() - $2::interval
              AND u.job_id IS NOT NULL
            GROUP BY u.job_id ORDER BY total_tokens DESC LIMIT 200
        """, client_id, interval)

    return {
        "window": window if window in WINDOWS else DEFAULT_WINDOW,
        "total": _row(total) if total else _empty(),
        "by_user": [_row(r) for r in by_user],
        "by_feature": [_row(r) for r in by_feature],
        "by_model": [_row(r) for r in by_model],
        "by_job": [_row(r) for r in by_job],
    }


def _empty() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "total_tokens": 0, "byo_tokens": 0,
            "calls": 0, "failed": 0}


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif k == "client_id" or k == "job_id":
            out[k] = str(v) if v else None
        else:
            out[k] = v
    return out


# =============================================================================================
# Detailed reporting — the shared half.
#
# The usage page drills from "what did the deployment spend" down to one call: per recording
# and per analyser, per chat conversation and per customer question, filtered and sorted. The
# queries live in `usage_report` (overview, the call log) and `usage_drill` (recordings,
# conversations and their detail); what they must agree on lives here, once: which analyser a
# feature belongs to, how a filter becomes SQL, what a totals block contains.
# =============================================================================================

# feature label -> analyser group, in the order every table lists groups. A feature not named
# here reports as "other", so a label added later still adds up rather than vanishing.
GROUPS: dict[str, tuple[str, ...]] = {
    "transcription": ("transcribe",),
    "analysis": ("analysis",),
    "factcheck": ("factcheck_claims", "factcheck_verdict"),
    "sentiment": ("semantic_text", "sentiment", "voice_tone"),
    "score": ("scoring",),
    "summary": ("summarise",),
    "bot": ("autopilot", "triage", "handoff"),
    "copilot": ("copilot",),
    "tts": ("tts",),
    "kb": ("kb_restructure", "curation_propose"),
    "rubric": ("scoring_import",),
    "test": ("probe",),
}
OTHER_GROUP = "other"
GROUP_ORDER: tuple[str, ...] = (*GROUPS, OTHER_GROUP)
KNOWN_FEATURES: tuple[str, ...] = tuple(f for fs in GROUPS.values() for f in fs)
CAPABILITIES = ("llm", "stt", "tts", "voice_tone")


def group_of(feature: str | None) -> str:
    for group, features in GROUPS.items():
        if feature in features:
            return group
    return OTHER_GROUP


def group_sql(alias: str = "u") -> str:
    """The same mapping as a SQL CASE. Every literal comes from `GROUPS` above — code, never a
    request — so interpolating them is safe; a quote in a label would be a code bug, and is
    refused here rather than trusted."""
    whens = []
    for group, features in GROUPS.items():
        for f in (group, *features):
            if "'" in f:
                raise ValueError(f"quote in usage label {f!r}")
        listed = ", ".join(f"'{f}'" for f in features)
        whens.append(f"WHEN {alias}.feature IN ({listed}) THEN '{group}'")
    return f"(CASE {' '.join(whens)} ELSE '{OTHER_GROUP}' END)"


def group_rank_sql(alias: str = "u") -> str:
    """The analyser group's position in GROUP_ORDER, so a sort keeps each group's rows together."""
    whens = " ".join(f"WHEN '{g}' THEN {i}" for i, g in enumerate(GROUP_ORDER))
    return f"(CASE {group_sql(alias)} {whens} END)"


def capability_sql(alias: str = "u") -> str:
    """Rows written before `capability` existed were all text-model calls."""
    return f"COALESCE({alias}.capability, 'llm')"


def provider_sql(alias: str = "u") -> str:
    """A NULL provider on a text call predates the registry, when everything ran on Anthropic
    (db/ai_connections.sql says so); on any other capability it is simply unknown."""
    return (f"COALESCE({alias}.provider, CASE WHEN COALESCE({alias}.capability, 'llm') = 'llm' "
            f"THEN 'anthropic' ELSE 'unknown' END)")


def tokens_sql(alias: str = "u") -> str:
    """One call's total tokens: input + output + both cache kinds, NULLs as zero."""
    return (f"(COALESCE({alias}.input_tokens,0) + COALESCE({alias}.output_tokens,0) "
            f"+ COALESCE({alias}.cache_read_tokens,0) + COALESCE({alias}.cache_creation_tokens,0))")


def totals_sql(alias: str = "u") -> str:
    """The Totals block every report returns (see `empty_totals` for its keys)."""
    a = alias
    return f"""
        COALESCE(SUM({a}.input_tokens), 0)::bigint          AS input_tokens,
        COALESCE(SUM({a}.output_tokens), 0)::bigint         AS output_tokens,
        COALESCE(SUM({a}.cache_read_tokens), 0)::bigint     AS cache_read_tokens,
        COALESCE(SUM({a}.cache_creation_tokens), 0)::bigint AS cache_creation_tokens,
        COALESCE(SUM({tokens_sql(a)}), 0)::bigint           AS total_tokens,
        COALESCE(SUM({tokens_sql(a)}) FILTER (WHERE {a}.byo), 0)::bigint AS byo_tokens,
        COUNT(*)::bigint                                    AS calls,
        COUNT(*) FILTER (WHERE NOT {a}.ok)::bigint          AS failed,
        COALESCE(SUM({a}.audio_seconds), 0)::float8         AS audio_seconds,
        COALESCE(SUM({a}.characters), 0)::bigint            AS characters,
        ROUND(AVG({a}.latency_ms))::bigint                  AS avg_latency_ms,
        MIN({a}.created_at)                                 AS first_used,
        MAX({a}.created_at)                                 AS last_used
    """


TOTAL_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
              "total_tokens", "byo_tokens", "calls", "failed", "audio_seconds", "characters",
              "avg_latency_ms", "first_used", "last_used")


def empty_totals() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "total_tokens": 0, "byo_tokens": 0, "calls": 0,
            "failed": 0, "audio_seconds": 0.0, "characters": 0, "avg_latency_ms": None,
            "first_used": None, "last_used": None}


def split_totals(row: dict) -> tuple[dict, dict]:
    """(the Totals block, everything else) from one serialised row that selected `totals_sql`."""
    tot = {k: row.get(k) for k in TOTAL_KEYS}
    rest = {k: v for k, v in row.items() if k not in TOTAL_KEYS}
    return tot, rest


def serialise(value):
    """JSON-ready: uuids as strings, timestamps as ISO, Decimals as floats, recursively."""
    if isinstance(value, dict):
        return {k: serialise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialise(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def record(r) -> dict:
    """An asyncpg Record (or dict) as a JSON-ready dict."""
    return serialise(dict(r)) if r is not None else {}


class FilterError(ValueError):
    """A filter the caller got wrong. The router turns it into a 400 with this sentence."""


@dataclass(frozen=True)
class UsageFilter:
    window: str                 # the preset the range came from, or "custom"
    start: datetime             # inclusive
    end: datetime | None        # exclusive; None = now
    client_id: str | None = None
    group: str | None = None
    feature: str | None = None
    capability: str | None = None
    provider: str | None = None
    model: str | None = None
    actor: str | None = None
    status: str | None = None   # "ok" | "failed"
    q: str | None = None


def _day(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise FilterError(f"`{name}` must be a date like 2026-09-16.") from exc


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def parse_filter(*, window: str | None = None, from_: str | None = None, to: str | None = None,
                 client_id: str | None = None, group: str | None = None,
                 feature: str | None = None, capability: str | None = None,
                 provider: str | None = None, model: str | None = None,
                 actor: str | None = None, status: str | None = None, q: str | None = None,
                 now: datetime | None = None) -> UsageFilter:
    """Query parameters -> a validated filter, or FilterError.

    `from`/`to` are whole UTC days and REPLACE the window when either is given: `to` includes
    its whole day. An unknown preset falls back to the default rather than failing, as the
    tenant list always has; everything else that is wrong says so."""
    now = now or datetime.now(timezone.utc)
    from_, to = _clean(from_), _clean(to)
    if from_ or to:
        start_day = _day(from_, "from") if from_ else None
        end_day = _day(to, "to") if to else None
        if start_day and end_day and end_day < start_day:
            raise FilterError("`to` is before `from`.")
        # A day at the edge of the calendar (9999-12-31, 0001-01-05) parses, and then the
        # arithmetic overflows — an OverflowError, not a ValueError, which would be a 500.
        try:
            start = (datetime.combine(start_day, time.min, tzinfo=timezone.utc) if start_day
                     else datetime.combine(end_day, time.min, tzinfo=timezone.utc)
                     - WINDOWS[DEFAULT_WINDOW])
            end = (datetime.combine(end_day, time.min, tzinfo=timezone.utc) + timedelta(days=1)
                   if end_day else None)
        except OverflowError as exc:
            raise FilterError("`from`/`to` is out of range.") from exc
        resolved = "custom"
    else:
        resolved = window if window in WINDOWS else DEFAULT_WINDOW
        start, end = now - WINDOWS[resolved], None

    cid = _clean(client_id)
    if cid:
        try:
            cid = str(uuid.UUID(cid))
        except ValueError as exc:
            raise FilterError("`client_id` must be a workspace id.") from exc
    grp = _clean(group)
    if grp and grp not in GROUP_ORDER:
        raise FilterError(f"Unknown analyser group {grp!r}.")
    cap = _clean(capability)
    if cap and cap not in CAPABILITIES:
        raise FilterError(f"Unknown capability {cap!r}.")
    st = _clean(status)
    if st and st not in ("ok", "failed"):
        raise FilterError("`status` must be ok or failed.")
    return UsageFilter(window=resolved, start=start, end=end, client_id=cid, group=grp,
                       feature=_clean(feature), capability=cap, provider=_clean(provider),
                       model=_clean(model), actor=_clean(actor), status=st, q=_clean(q))


def where_sql(f: UsageFilter, alias: str = "u", *, first: int = 1,
              skip: tuple[str, ...] = ()) -> tuple[str, list]:
    """The filter as a WHERE fragment over `llm_usage {alias}` with $-placeholders numbered
    from `first`, and its arguments. `skip` leaves named parts out (the overview's facets drop
    everything but the range). `q` is NOT handled here: what it searches differs per report."""
    a = alias
    clauses: list[str] = []
    args: list = []

    def add(sql: str, value) -> None:
        args.append(value)
        clauses.append(sql.replace("$?", f"${first + len(args) - 1}"))

    add(f"{a}.created_at >= $?", f.start)
    if f.end is not None:
        add(f"{a}.created_at < $?", f.end)
    if f.client_id and "client_id" not in skip:
        add(f"{a}.client_id = $?::uuid", f.client_id)
    if f.group and "group" not in skip:
        if f.group == OTHER_GROUP:
            add(f"NOT ({a}.feature = ANY($?::text[]))", list(KNOWN_FEATURES))
        else:
            add(f"{a}.feature = ANY($?::text[])", list(GROUPS[f.group]))
    if f.feature and "feature" not in skip:
        add(f"{a}.feature = $?", f.feature)
    if f.capability and "capability" not in skip:
        add(f"{capability_sql(a)} = $?", f.capability)
    if f.provider and "provider" not in skip:
        add(f"{provider_sql(a)} = $?", f.provider)
    if f.model and "model" not in skip:
        add(f"{a}.model = $?", f.model)
    if f.actor and "actor" not in skip:
        add(f"{a}.actor = $?", f.actor)
    if f.status and "status" not in skip:
        clauses.append(f"{a}.ok" if f.status == "ok" else f"NOT {a}.ok")
    return " AND ".join(clauses), args


SORT_DIRS = ("asc", "desc")
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def page_params(sort: str | None, dir_: str | None, limit, offset,
                allowed: dict[str, str], default: str) -> dict:
    """Whitelisted ORDER BY + clamped paging. `allowed` maps a public sort key to its SQL
    expression; an unknown key falls back to `default` — a sort is never interpolated from the
    request. NULLS LAST both ways, so a missing value never tops a biggest-first list."""
    key = sort if sort in allowed else default
    direction = dir_ if dir_ in SORT_DIRS else "desc"
    try:
        lim = max(1, min(MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        lim = DEFAULT_LIMIT
    try:
        off = max(0, int(offset))
    except (TypeError, ValueError):
        off = 0
    return {"sort": key, "dir": direction, "limit": lim, "offset": off,
            "order_sql": f"{allowed[key]} {direction.upper()} NULLS LAST"}


def range_of(f: UsageFilter, now: datetime | None = None) -> dict:
    """The resolved range as the reports echo it."""
    end = f.end or (now or datetime.now(timezone.utc))
    return {"window": f.window, "from": f.start.isoformat(), "to": end.isoformat()}
