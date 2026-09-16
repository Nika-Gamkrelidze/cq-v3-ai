"""Usage reports across calls: the overview and the call log. See `usage.py` for the shared
filter, group and totals helpers these are built on.

Both are read-only and superadmin-only (the router's dependency). Every request value reaches
SQL as a parameter: the filter through `usage.where_sql`, the search as one ILIKE pattern, the
sort through `usage.page_params`' whitelist. What IS interpolated is code — the helpers' CASE
and COALESCE expressions and the sort map below.
"""
from datetime import datetime, timedelta, timezone

from ..db import pool
from . import usage

# Up to two days a day-per-bar chart is one or two bars, which says nothing; past that an
# hour-per-bar chart is hundreds of slivers. The boundary is inclusive: "24h" and a custom
# single day or pair of days are hourly.
HOURLY_UP_TO = timedelta(days=2)
TOP_USERS = 100

# Everything the filter bar narrows by. The facets drop all of them and keep only the range:
# a dropdown that offered only the value already chosen could never be changed back.
_NON_RANGE = ("client_id", "group", "feature", "capability", "provider", "model", "actor",
              "status")


def bucket_of(start: datetime, end: datetime) -> str:
    """The series' bucket for a range: "hour" up to two days, "day" beyond."""
    return "hour" if end - start <= HOURLY_UP_TO else "day"


def like_pattern(q: str) -> str:
    """A case-insensitive substring pattern for `ILIKE ... ESCAPE '\\'`. The user's own `%` and
    `_` are escaped, so searching for "100%" finds that text rather than everything."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _ordered(values, order: tuple[str, ...]) -> list[str]:
    """Known values in the house order, anything else after them alphabetically."""
    rank = {v: i for i, v in enumerate(order)}
    return sorted((v for v in values if v is not None),
                  key=lambda v: (rank.get(v, len(order)), v))


def _flat(r) -> dict:
    """A breakdown row: its identity keys, then exactly the Totals keys."""
    tot, rest = usage.split_totals(usage.record(r))
    return {**rest, **tot}


async def overview(f: usage.UsageFilter) -> dict:
    now = datetime.now(timezone.utc)
    rng = usage.range_of(f, now)
    bucket = bucket_of(f.start, f.end or now)
    where, args = usage.where_sql(f)
    totals = usage.totals_sql()
    grp, prov, cap = usage.group_sql(), usage.provider_sql(), usage.capability_sql()

    async with pool().acquire() as conn:
        total = await conn.fetchrow(f"SELECT {totals} FROM llm_usage u WHERE {where}", *args)

        # LEFT JOIN from usage: `llm_usage.client_id` is ON DELETE SET NULL, so a deleted
        # workspace's calls survive with a null id and must still add up to the total.
        by_tenant = await conn.fetch(f"""
            SELECT u.client_id, COALESCE(c.name, '—') AS name, c.slug, {totals}
              FROM llm_usage u LEFT JOIN clients c ON c.id = u.client_id
             WHERE {where}
             GROUP BY u.client_id, c.name, c.slug
             ORDER BY total_tokens DESC""", *args)

        by_group = await conn.fetch(f"""
            SELECT {grp} AS "group", {totals} FROM llm_usage u WHERE {where}
             GROUP BY 1 ORDER BY total_tokens DESC""", *args)

        by_feature = await conn.fetch(f"""
            SELECT u.feature, {grp} AS "group", {totals} FROM llm_usage u WHERE {where}
             GROUP BY 1, 2 ORDER BY total_tokens DESC""", *args)

        by_provider = await conn.fetch(f"""
            SELECT {prov} AS provider, {totals} FROM llm_usage u WHERE {where}
             GROUP BY 1 ORDER BY total_tokens DESC""", *args)

        by_model = await conn.fetch(f"""
            SELECT {prov} AS provider, u.model, {cap} AS capability, {totals}
              FROM llm_usage u WHERE {where}
             GROUP BY 1, 2, 3 ORDER BY total_tokens DESC""", *args)

        # Grouped by the COALESCEd actor, so a null actor and none at all are one row. The
        # same person id in two workspaces is two rows: `actor` is only unique per tenant.
        by_user = await conn.fetch(f"""
            SELECT COALESCE(u.actor, 'unattributed') AS actor, u.client_id,
                   COALESCE(c.name, '—') AS name, {totals}
              FROM llm_usage u LEFT JOIN clients c ON c.id = u.client_id
             WHERE {where}
             GROUP BY 1, 2, 3 ORDER BY total_tokens DESC LIMIT {TOP_USERS}""", *args)

        # Bucketed in UTC whatever the session time zone: the range the page sent is UTC days,
        # and a bucket that started at local midnight would straddle two of them.
        series = await conn.fetch(f"""
            SELECT date_trunc(${len(args) + 1}::text, u.created_at, 'UTC') AS t,
                   COALESCE(SUM({usage.tokens_sql()}), 0)::bigint AS total_tokens,
                   COALESCE(SUM(u.input_tokens), 0)::bigint       AS input_tokens,
                   COALESCE(SUM(u.output_tokens), 0)::bigint      AS output_tokens,
                   COUNT(*)::bigint                               AS calls,
                   COUNT(*) FILTER (WHERE NOT u.ok)::bigint       AS failed
              FROM llm_usage u WHERE {where}
             GROUP BY 1 ORDER BY 1""", *args, bucket)

        range_where, range_args = usage.where_sql(f, skip=_NON_RANGE)
        facet = await conn.fetchrow(f"""
            WITH r AS (SELECT u.feature, u.model, u.actor, {grp} AS grp, {prov} AS provider,
                              {cap} AS capability
                         FROM llm_usage u WHERE {range_where})
            SELECT ARRAY(SELECT DISTINCT grp FROM r)                         AS groups,
                   ARRAY(SELECT DISTINCT feature FROM r ORDER BY 1)          AS features,
                   ARRAY(SELECT DISTINCT provider FROM r ORDER BY 1)         AS providers,
                   ARRAY(SELECT DISTINCT model FROM r ORDER BY 1)            AS models,
                   ARRAY(SELECT DISTINCT capability FROM r)                  AS capabilities,
                   ARRAY(SELECT DISTINCT actor FROM r
                          WHERE actor IS NOT NULL ORDER BY 1)                AS actors""",
                                    *range_args)
        tenants = await conn.fetch(f"""
            SELECT DISTINCT u.client_id, COALESCE(c.name, '—') AS name
              FROM llm_usage u LEFT JOIN clients c ON c.id = u.client_id
             WHERE {range_where} AND u.client_id IS NOT NULL
             ORDER BY 2, 1""", *range_args)

    tot, _ = usage.split_totals(usage.record(total))
    return {
        **rng,
        "bucket": bucket,
        "total": tot,
        "by_tenant": [_flat(r) for r in by_tenant],
        "by_group": [_flat(r) for r in by_group],
        "by_feature": [_flat(r) for r in by_feature],
        "by_provider": [_flat(r) for r in by_provider],
        "by_model": [_flat(r) for r in by_model],
        "by_user": [_flat(r) for r in by_user],
        "series": [usage.record(r) for r in series],
        "facets": {
            "tenants": [usage.record(r) for r in tenants],
            "groups": _ordered(facet["groups"], usage.GROUP_ORDER),
            "features": list(facet["features"]),
            "providers": list(facet["providers"]),
            "models": list(facet["models"]),
            "capabilities": _ordered(facet["capabilities"], usage.CAPABILITIES),
            "actors": list(facet["actors"]),
        },
    }


# The call log's joins. Every join to a workspace's data repeats the tenant: a usage row's
# recording, conversation or turn id is only meaningful inside its own workspace, and the
# multi-tenancy invariant is that no query ever pairs rows across two (the recordings drill-down
# matches the same way, so the two tabs never disagree about one call). The recording join uses
# IS NOT DISTINCT FROM because a registered user's or an anonymous visitor's recording has no
# workspace. All four are joins on a primary key, so none of them can multiply a call.
_CALL_FROM = """
    FROM llm_usage u
    LEFT JOIN clients c             ON c.id = u.client_id
    LEFT JOIN audio_jobs j          ON j.id = u.job_id AND j.client_id IS NOT DISTINCT FROM u.client_id
    LEFT JOIN chat_conversations cv ON cv.id = u.conversation_id AND cv.client_id = u.client_id
    LEFT JOIN chat_turns t          ON t.id = u.turn_id AND t.client_id = u.client_id
"""

CALL_SORTS = {
    "created_at": "u.created_at",
    "total_tokens": usage.tokens_sql(),
    "input_tokens": "u.input_tokens",
    "output_tokens": "u.output_tokens",
    "latency_ms": "u.latency_ms",
    "audio_seconds": "u.audio_seconds",
    # Seconds for speech, characters for synthesis: one unit per kind, so the order is exact
    # whenever the Kind filter is set, and text calls (neither) sink to the bottom.
    "consumed": "COALESCE(u.audio_seconds, u.characters::double precision)",
    # The analyser column shows the GROUP, so rows of one analyser must stay together (sorting
    # the raw label split "Chat bot" into autopilot / handoff / triage blocks). One expression,
    # because page_params appends the direction once.
    "feature": f"(lpad({usage.group_rank_sql()}::text, 2, '0') || '/' || u.feature)",
    "provider": usage.provider_sql(),
    # The column shows "provider / model", and on a one-provider deployment sorting by the
    # provider alone changed nothing.
    "model": f"({usage.provider_sql()} || '/' || u.model)",
    "tenant": "lower(c.name)",
}
DEFAULT_CALL_SORT = "created_at"


def call_columns() -> str:
    """The CallRow select list over `_CALL_FROM`'s aliases. Token columns stay raw (NULL when
    the provider reported none, as speech-to-text often does); `total_tokens` counts them 0."""
    return f"""
        u.id, u.created_at, u.client_id, c.name AS tenant_name, c.slug,
        u.feature, {usage.group_sql()} AS "group", {usage.capability_sql()} AS capability,
        {usage.provider_sql()} AS provider, u.model,
        u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_creation_tokens,
        {usage.tokens_sql()} AS total_tokens,
        u.audio_seconds, u.characters, u.latency_ms, u.ok, u.byo, u.actor,
        u.job_id, j.filename,
        u.conversation_id, cv.external_ref AS conversation_ref, cv.channel,
        u.turn_id, t.role AS turn_role, left(t.content, 160) AS turn_preview,
        u.summary_id
    """


async def calls(f: usage.UsageFilter, *, sort: str | None, dir_: str | None, limit: int,
                offset: int) -> dict:
    page = usage.page_params(sort, dir_, limit, offset, CALL_SORTS, DEFAULT_CALL_SORT)
    where, args = usage.where_sql(f)
    if f.q:
        args.append(like_pattern(f.q))
        p = f"${len(args)}"
        where += (f" AND (j.filename ILIKE {p} ESCAPE '\\' OR cv.external_ref ILIKE {p} ESCAPE '\\'"
                  f" OR t.content ILIKE {p} ESCAPE '\\' OR u.actor ILIKE {p} ESCAPE '\\')")
    # Without a search nothing in the WHERE touches a join, and joins on primary keys cannot
    # change the count, so the count skips them.
    count_from = _CALL_FROM if f.q else "FROM llm_usage u"

    async with pool().acquire() as conn:
        # A separate count rather than COUNT(*) OVER (): a page past the end returns no rows,
        # and with them the window count, so the pager could not say how far back to go.
        total = await conn.fetchval(f"SELECT COUNT(*) {count_from} WHERE {where}", *args)
        # created_at and id break ties, so equal sort values page in a stable order.
        rows = await conn.fetch(f"""
            SELECT {call_columns()} {_CALL_FROM}
             WHERE {where}
             ORDER BY {page['order_sql']}, u.created_at DESC, u.id
             LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}""",
                                *args, page["limit"], page["offset"])

    return {"total": int(total or 0), "limit": page["limit"], "offset": page["offset"],
            "sort": page["sort"], "dir": page["dir"], "rows": [usage.record(r) for r in rows]}
