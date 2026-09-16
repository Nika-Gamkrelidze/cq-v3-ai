"""Usage drill-downs: per recording (and per analyser inside it) and per chat conversation
(and per question inside it). See `usage.py` for the shared helpers.

Three rules hold for every query here:

* TENANCY ON EVERY JOIN. `llm_usage` names a recording, a conversation and a turn by id, and
  none of those ids is a foreign key (usage must outlive what it describes). So a join from a
  usage row to a tenant-owned table also matches the owner: a row whose id points at another
  workspace's data comes back with that data NULL instead of borrowing its filename or its
  customer's words. `audio_jobs` may legitimately have no owner (anonymous and registered-user
  recordings), hence IS NOT DISTINCT FROM there.
* A LIST ROW IS (id, workspace), not the id alone — for the same reason: grouping by the id
  would fold one workspace's calls into a row that shows another's recording.
* PAGE FIRST, THEN EXPAND. The per-analyser and per-feature breakdowns are computed for the rows
  on the page only, so a 90-day window costs one GROUP BY plus fifty rows of detail rather than a
  breakdown of every recording in it.

Nothing request-supplied is interpolated: filters go through `usage.where_sql` as parameters,
sort keys through `usage.page_params`' whitelist, and the one interpolated group label comes from
`usage.GROUP_ORDER` after that whitelist has accepted it.
"""
from ..db import pool
from . import usage

# The detail endpoints' CallRow — the same keys as `usage_report.calls` rows (frontend `CallRow`).
# Kept private here rather than imported: the two modules are built in parallel, and the shape
# is pinned by the contract and the tests, not by sharing a string.
_CALL_SELECT = f"""
    SELECT u.id, u.created_at, u.client_id, c.name AS tenant_name, c.slug,
           u.feature, {usage.group_sql('u')} AS "group",
           {usage.capability_sql('u')} AS capability, {usage.provider_sql('u')} AS provider,
           u.model,
           u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_creation_tokens,
           {usage.tokens_sql('u')}::bigint AS total_tokens,
           u.audio_seconds, u.characters, u.latency_ms, u.ok, u.byo, u.actor,
           u.job_id, j.filename,
           u.conversation_id, cv.external_ref AS conversation_ref, cv.channel,
           u.turn_id, t.role AS turn_role, left(t.content, 160) AS turn_preview,
           u.summary_id
      FROM llm_usage u
      LEFT JOIN clients c ON c.id = u.client_id
      LEFT JOIN audio_jobs j
             ON j.id = u.job_id AND j.client_id IS NOT DISTINCT FROM u.client_id
      LEFT JOIN chat_conversations cv
             ON cv.id = u.conversation_id AND cv.client_id = u.client_id
      LEFT JOIN chat_turns t ON t.id = u.turn_id AND t.client_id = u.client_id
"""

# "provider/model", the label every models[] array carries.
_MODEL_LABEL = f"({usage.provider_sql('u')} || '/' || u.model)"

# The small per-analyser / per-feature cell (not a full Totals block: a list row carries a dozen
# of them, and first/last/latency per cell is noise at that size).
_CELL_SQL = f"""
    COALESCE(SUM({usage.tokens_sql('u')}), 0)::bigint AS total_tokens,
    COALESCE(SUM(u.input_tokens), 0)::bigint          AS input_tokens,
    COALESCE(SUM(u.output_tokens), 0)::bigint         AS output_tokens,
    COUNT(*)::bigint                                  AS calls,
    COUNT(*) FILTER (WHERE NOT u.ok)::bigint          AS failed
"""


def _like(q: str) -> str:
    """`q` as an ILIKE pattern that matches it literally: a `_` in a filename is a character
    the operator typed, not a wildcard."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _key(*ids) -> tuple:
    return tuple(None if i is None else str(i) for i in ids)


def _models(rows) -> list[str]:
    return sorted({m for r in rows for m in (r or [])})


def _totals(r) -> dict:
    """A Totals block from a row that selected `totals_sql`, or zeros when the row is missing
    (a LEFT JOIN that found no usage)."""
    if r is None or r["calls"] is None:
        return usage.empty_totals()
    tot, _ = usage.split_totals(usage.record(r))
    return tot


def _page(p: dict, total: int, rows: list) -> dict:
    return {"total": total, "limit": p["limit"], "offset": p["offset"], "sort": p["sort"],
            "dir": p["dir"], "rows": rows}


async def _owner(conn, column: str, value: str) -> tuple:
    """(client_id, tenant_name) of a purged recording / conversation, from its oldest usage row.
    `column` is one of two literals from this module, never a request value. (None, None) if the
    rows vanished between the caller's total and this lookup."""
    r = await conn.fetchrow(f"""
        SELECT u.client_id, c.name AS tenant_name
          FROM llm_usage u LEFT JOIN clients c ON c.id = u.client_id
         WHERE u.{column} = $1::uuid
         ORDER BY u.created_at, u.id LIMIT 1
    """, value)
    return (r["client_id"], r["tenant_name"]) if r else (None, None)


# ---------------------------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------------------------
_RECORDING_SORTS = {
    "last_used": "jobs.last_used",
    "total_tokens": "jobs.total_tokens",
    "calls": "jobs.calls",
    "filename": "lower(j.filename)",
    "tenant": "lower(c.name)",
    "duration_s": "j.duration_s",
    "created_at": "j.created_at",
    **{f"group:{g}": "jobs.sort_group" for g in usage.GROUP_ORDER},
}


async def recordings(f: usage.UsageFilter, *, sort: str | None, dir_: str | None, limit: int,
                     offset: int) -> dict:
    """One row per recording that had a (filtered) AI call, with what each analyser spent.

    Queries: the count, the page, and the per-analyser expansion of that page."""
    where, args = usage.where_sql(f, "u")
    base_args = list(args)
    conds = [where, "u.job_id IS NOT NULL"]
    join = ""
    if f.q:
        args.append(_like(f.q))
        join = ("JOIN audio_jobs j "
                "ON j.id = u.job_id AND j.client_id IS NOT DISTINCT FROM u.client_id")
        conds.append(f"j.filename ILIKE ${len(args)} ESCAPE '\\'")
    cond_sql = " AND ".join(conds)

    p = usage.page_params(sort, dir_, limit, offset, _RECORDING_SORTS, "last_used")
    # Sorting by an analyser needs that analyser's subtotal before the page is cut. NULL (not 0)
    # when it never ran on the recording, so NULLS LAST keeps those at the bottom both ways: a
    # recording nobody fact-checked is not the cheapest fact-check.
    # Tokens first, as the contract says, then the analyser's audio length and characters in
    # the same direction: a token-less step (Scribe transcription, voice tone) shows a duration
    # in its cell, and without the tie-breakers every such recording sorted as an equal 0.
    group = p["sort"].removeprefix("group:") if p["sort"].startswith("group:") else None
    in_group = (f"FILTER (WHERE {usage.group_sql('u')} = '{group}')"
                if group in usage.GROUP_ORDER else None)
    sort_group = f"SUM({usage.tokens_sql('u')}) {in_group}::bigint" if in_group else "NULL::bigint"
    sort_audio = f"SUM(u.audio_seconds) {in_group}::float8" if in_group else "NULL::float8"
    sort_chars = f"SUM(u.characters) {in_group}::bigint" if in_group else "NULL::bigint"
    order_sql = p["order_sql"]
    if in_group:
        d = p["dir"].upper()
        order_sql += f", jobs.sort_audio {d} NULLS LAST, jobs.sort_chars {d} NULLS LAST"

    async with pool().acquire() as conn:
        total = await conn.fetchval(f"""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM llm_usage u {join}
                 WHERE {cond_sql}
                 GROUP BY u.job_id, u.client_id) x
        """, *args)

        rows = await conn.fetch(f"""
            WITH jobs AS (
                SELECT u.job_id, u.client_id, {usage.totals_sql('u')},
                       {sort_group} AS sort_group, {sort_audio} AS sort_audio,
                       {sort_chars} AS sort_chars
                  FROM llm_usage u {join}
                 WHERE {cond_sql}
                 GROUP BY u.job_id, u.client_id
            )
            SELECT jobs.*, c.name AS tenant_name, j.filename, j.created_at AS recorded_at,
                   round(j.duration_s::numeric, 2) AS duration_s, j.language
              FROM jobs
              LEFT JOIN clients c ON c.id = jobs.client_id
              LEFT JOIN audio_jobs j
                     ON j.id = jobs.job_id AND j.client_id IS NOT DISTINCT FROM jobs.client_id
             ORDER BY {order_sql}, jobs.job_id, jobs.client_id
             LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """, *args, p["limit"], p["offset"])

        cells: dict[tuple, dict] = {}
        if rows:
            n = len(base_args)
            # The filter again (it decides which calls count), but not `q`: it selects
            # recordings, and every call of a recording on this page already passed it.
            expanded = await conn.fetch(f"""
                SELECT u.job_id, u.client_id, {usage.group_sql('u')} AS grp, {_CELL_SQL},
                       COALESCE(SUM(u.audio_seconds), 0)::float8 AS audio_seconds,
                       COALESCE(SUM(u.characters), 0)::bigint    AS characters,
                       array_agg(DISTINCT {_MODEL_LABEL})        AS models
                  FROM llm_usage u
                  JOIN unnest(${n + 1}::uuid[], ${n + 2}::uuid[]) AS p(job_id, client_id)
                    ON p.job_id = u.job_id AND p.client_id IS NOT DISTINCT FROM u.client_id
                 WHERE {where}
                 GROUP BY u.job_id, u.client_id, grp
            """, *base_args, [r["job_id"] for r in rows], [r["client_id"] for r in rows])
            for g in expanded:
                cell = dict(g)
                cell["models"] = sorted(cell["models"] or [])
                for k in ("job_id", "client_id", "grp"):
                    cell.pop(k)
                cells.setdefault(_key(g["job_id"], g["client_id"]), {})[g["grp"]] = cell

    out = []
    for r in rows:
        tot, rest = usage.split_totals(usage.record(r))
        groups = cells.get(_key(r["job_id"], r["client_id"]), {})
        ordered = {g: groups[g] for g in usage.GROUP_ORDER if g in groups}
        out.append({
            "job_id": rest["job_id"], "client_id": rest["client_id"],
            "tenant_name": rest["tenant_name"], "filename": rest["filename"],
            "created_at": rest["recorded_at"], "duration_s": rest["duration_s"],
            "language": rest["language"], "total": tot, "groups": ordered,
            "models": _models(c["models"] for c in ordered.values()),
        })
    return _page(p, total, out)


async def recording_detail(job_id: str) -> dict | None:
    """Every AI call one recording caused, all time, grouped by analyser — plus the summaries
    of several recordings it was part of, whose calls belong to no single recording.

    Queries: the recording, its total, its groups, its calls, its summaries (+ one owner lookup
    when retention has purged the recording). None when neither the recording nor any usage of
    it exists."""
    async with pool().acquire() as conn:
        rec = await conn.fetchrow("""
            SELECT j.id AS job_id, j.client_id, c.name AS tenant_name, j.filename,
                   j.created_at, round(j.duration_s::numeric, 2) AS duration_s, j.language
              FROM audio_jobs j LEFT JOIN clients c ON c.id = j.client_id
             WHERE j.id = $1::uuid
        """, job_id)
        total = await conn.fetchrow(
            f"SELECT {usage.totals_sql('u')} FROM llm_usage u WHERE u.job_id = $1::uuid", job_id)
        if rec is None and not total["calls"]:
            return None

        if rec is not None:
            recording = usage.record(rec)
            owner = rec["client_id"]
        else:
            # Purged: the usage rows still know whose it was; nothing else survives.
            owner, tenant_name = await _owner(conn, "job_id", job_id)
            recording = {"job_id": job_id, "client_id": usage.serialise(owner),
                         "tenant_name": tenant_name, "filename": None,
                         "created_at": None, "duration_s": None, "language": None}

        groups = await conn.fetch(f"""
            SELECT {usage.group_sql('u')} AS grp, {usage.totals_sql('u')},
                   array_agg(DISTINCT {_MODEL_LABEL}) AS models
              FROM llm_usage u WHERE u.job_id = $1::uuid
             GROUP BY grp
        """, job_id)
        calls = await conn.fetch(
            f"{_CALL_SELECT} WHERE u.job_id = $1::uuid ORDER BY u.created_at, u.id", job_id)

        # Only summaries of SEVERAL recordings: a one-recording summary's calls carry this
        # job_id and are already in `groups`. Owner-matched like every other join, and split on
        # NULL so a workspace's recording uses the (client_id, created_at) index. A summary with
        # no usage row (made before summaries were metered) is still listed, with zero totals:
        # the operator asked which summaries this call fed, not which ones cost something.
        owner_cond = "client_id = $2::uuid" if owner is not None else "client_id IS NULL"
        summaries = await conn.fetch(f"""
            WITH s AS (
                SELECT id, cardinality(job_ids) AS job_count, created_at, client_id
                  FROM call_summaries
                 WHERE {owner_cond} AND $1::uuid = ANY(job_ids) AND cardinality(job_ids) > 1
            ), t AS (
                SELECT u.summary_id, {usage.totals_sql('u')}
                  FROM llm_usage u
                  JOIN s ON s.id = u.summary_id AND s.client_id IS NOT DISTINCT FROM u.client_id
                 GROUP BY u.summary_id
            )
            SELECT s.id AS summary_id, s.job_count, s.created_at AS summary_created_at,
                   {", ".join(f"t.{k}" for k in usage.TOTAL_KEYS)}
              FROM s LEFT JOIN t ON t.summary_id = s.id
             ORDER BY s.created_at, s.id
        """, *((job_id, owner) if owner is not None else (job_id,)))

    by_group = {g["grp"]: g for g in groups}
    return {
        "recording": recording,
        "total": _totals(total),
        "groups": [{**_totals(by_group[g]), "group": g,
                    "models": sorted(by_group[g]["models"] or [])}
                   for g in usage.GROUP_ORDER if g in by_group],
        "calls": [usage.record(c) for c in calls],
        "summaries": [{**_totals(s), "summary_id": str(s["summary_id"]),
                       "job_count": s["job_count"],
                       "created_at": usage.serialise(s["summary_created_at"])}
                      for s in summaries],
    }


# ---------------------------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------------------------
_TURNS_SQL = ("(SELECT COUNT(*) FROM chat_turns t WHERE t.conversation_id = {a}.conversation_id "
              "AND t.client_id = {a}.client_id)")

_CONVERSATION_SORTS = {
    "last_used": "convs.last_used",
    "total_tokens": "convs.total_tokens",
    "calls": "convs.calls",
    # Counted before the page is cut only when it is the sort; otherwise after (below).
    "turns": _TURNS_SQL.format(a="convs"),
    "questions": "convs.questions",
    "tenant": "lower(c.name)",
    "channel": "cv.channel",
    "created_at": "cv.created_at",
}


async def conversations(f: usage.UsageFilter, *, channel: str | None, sort: str | None,
                        dir_: str | None, limit: int, offset: int) -> dict:
    """One row per chat conversation that had a (filtered) AI call, with what each feature spent.

    Queries: the count, the page, and the per-feature expansion of that page."""
    where, args = usage.where_sql(f, "u")
    base_args = list(args)
    conds = [where, "u.conversation_id IS NOT NULL"]
    join = ""
    if f.q or channel:
        # An inner join is right here: both conditions need the conversation to exist and to
        # belong to the caller's workspace.
        join = ("JOIN chat_conversations cv "
                "ON cv.id = u.conversation_id AND cv.client_id = u.client_id")
    if f.q:
        args.append(_like(f.q))
        n = len(args)
        conds.append(f"(cv.external_ref ILIKE ${n} ESCAPE '\\' "
                     f"OR cv.subject ILIKE ${n} ESCAPE '\\')")
    if channel:
        args.append(channel)
        conds.append(f"cv.channel = ${len(args)}")
    cond_sql = " AND ".join(conds)

    p = usage.page_params(sort, dir_, limit, offset, _CONVERSATION_SORTS, "last_used")

    async with pool().acquire() as conn:
        total = await conn.fetchval(f"""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM llm_usage u {join}
                 WHERE {cond_sql}
                 GROUP BY u.conversation_id, u.client_id) x
        """, *args)

        # row_number() carries the order out of the CTE, so the turn count below runs for the
        # page's rows only.
        rows = await conn.fetch(f"""
            WITH convs AS (
                SELECT u.conversation_id, u.client_id, {usage.totals_sql('u')},
                       COUNT(DISTINCT u.turn_id)::bigint AS questions
                  FROM llm_usage u {join}
                 WHERE {cond_sql}
                 GROUP BY u.conversation_id, u.client_id
            ), page AS (
                SELECT convs.*, c.name AS tenant_name, cv.external_ref, cv.channel, cv.locale,
                       cv.state, cv.subject, cv.created_at AS started_at, cv.last_message_at,
                       row_number() OVER (ORDER BY {p["order_sql"]}, convs.conversation_id,
                                          convs.client_id) AS rn
                  FROM convs
                  LEFT JOIN clients c ON c.id = convs.client_id
                  LEFT JOIN chat_conversations cv
                         ON cv.id = convs.conversation_id AND cv.client_id = convs.client_id
                 ORDER BY rn
                 LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
            )
            SELECT page.*, {_TURNS_SQL.format(a="page")}::bigint AS turns
              FROM page ORDER BY page.rn
        """, *args, p["limit"], p["offset"])

        features: dict[tuple, list] = {}
        if rows:
            n = len(base_args)
            expanded = await conn.fetch(f"""
                SELECT u.conversation_id, u.client_id, u.feature, {_CELL_SQL},
                       array_agg(DISTINCT {_MODEL_LABEL}) AS models
                  FROM llm_usage u
                  JOIN unnest(${n + 1}::uuid[], ${n + 2}::uuid[]) AS p(conversation_id, client_id)
                    ON p.conversation_id = u.conversation_id
                   AND p.client_id IS NOT DISTINCT FROM u.client_id
                 WHERE {where}
                 GROUP BY u.conversation_id, u.client_id, u.feature
                 ORDER BY total_tokens DESC, u.feature
            """, *base_args, [r["conversation_id"] for r in rows],
                [r["client_id"] for r in rows])
            for e in expanded:
                features.setdefault(_key(e["conversation_id"], e["client_id"]), []).append(e)

    out = []
    for r in rows:
        tot, rest = usage.split_totals(usage.record(r))
        mine = features.get(_key(r["conversation_id"], r["client_id"]), [])
        out.append({
            "conversation_id": rest["conversation_id"], "client_id": rest["client_id"],
            "tenant_name": rest["tenant_name"], "external_ref": rest["external_ref"],
            "channel": rest["channel"], "locale": rest["locale"], "state": rest["state"],
            "subject": rest["subject"], "created_at": rest["started_at"],
            "last_message_at": rest["last_message_at"],
            "turns": rest["turns"], "questions": rest["questions"], "total": tot,
            "features": {e["feature"]: {k: e[k] for k in ("total_tokens", "input_tokens",
                                                          "output_tokens", "calls", "failed")}
                         for e in mine},
            "models": _models(e["models"] for e in mine),
        })
    return _page(p, total, out)


async def conversation_detail(conversation_id: str) -> dict | None:
    """One conversation, all time, message by message: each turn and the AI calls it caused.

    Queries: the conversation, its total, its turns, the per-turn totals, its calls (+ one
    owner lookup when the conversation is gone). A call joins a turn only when it belongs to the
    turn's workspace; every other call of the conversation id — no turn, a turn past the first
    500, or another workspace's row naming this id — is `unattached`. None when neither the
    conversation nor any usage of it exists."""
    async with pool().acquire() as conn:
        conv = await conn.fetchrow("""
            SELECT cv.id AS conversation_id, cv.client_id, c.name AS tenant_name,
                   cv.external_ref, cv.channel, cv.locale, cv.state, cv.subject,
                   cv.created_at, cv.last_message_at
              FROM chat_conversations cv LEFT JOIN clients c ON c.id = cv.client_id
             WHERE cv.id = $1::uuid
        """, conversation_id)
        total = await conn.fetchrow(
            f"SELECT {usage.totals_sql('u')} FROM llm_usage u WHERE u.conversation_id = $1::uuid",
            conversation_id)
        if conv is None and not total["calls"]:
            return None

        turns, per_turn = [], {}
        if conv is not None:
            conversation = usage.record(conv)
            owner = conv["client_id"]
            turns = await conn.fetch("""
                SELECT t.id AS turn_id, t.turn_ref, t.role, left(t.content, 1000) AS content,
                       t.created_at, t.grounded
                  FROM chat_turns t
                 WHERE t.conversation_id = $1::uuid AND t.client_id = $2::uuid
                 ORDER BY t.created_at, t.id
                 LIMIT 500
            """, conversation_id, owner)
            if turns:
                per_turn = {str(r["turn_id"]): r for r in await conn.fetch(f"""
                    SELECT u.turn_id, {usage.totals_sql('u')}
                      FROM llm_usage u
                     WHERE u.conversation_id = $1::uuid AND u.client_id = $2::uuid
                       AND u.turn_id = ANY($3::uuid[])
                     GROUP BY u.turn_id
                """, conversation_id, owner, [t["turn_id"] for t in turns])}
        else:
            # Purged conversation (turns cascade with it): the usage rows are all that is left.
            owner, tenant_name = await _owner(conn, "conversation_id", conversation_id)
            conversation = {"conversation_id": conversation_id,
                            "client_id": usage.serialise(owner),
                            "tenant_name": tenant_name, "external_ref": None,
                            "channel": None, "locale": None, "state": None, "subject": None,
                            "created_at": None, "last_message_at": None}

        calls = await conn.fetch(
            f"{_CALL_SELECT} WHERE u.conversation_id = $1::uuid ORDER BY u.created_at, u.id",
            conversation_id)

    owner_s = usage.serialise(owner)
    by_turn: dict[str, list] = {str(t["turn_id"]): [] for t in turns}
    unattached = []
    for c in calls:
        row = usage.record(c)
        if row["turn_id"] in by_turn and conv is not None and row["client_id"] == owner_s:
            by_turn[row["turn_id"]].append(row)
        else:
            unattached.append(row)

    return {
        "conversation": conversation,
        "total": _totals(total),
        "turns": [{**usage.record(t), "total": _totals(per_turn.get(str(t["turn_id"]))),
                   "calls": by_turn[str(t["turn_id"])]} for t in turns],
        "unattached": unattached,
    }
