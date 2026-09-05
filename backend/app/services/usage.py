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
from datetime import timedelta

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
            "cache_creation_tokens": 0, "total_tokens": 0, "calls": 0, "failed": 0}


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
