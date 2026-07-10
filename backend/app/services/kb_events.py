"""KB activity log — ingestion history (#5) and change audit (#13).

One append-only table records every KB action (import lifecycle, edits, deletes,
re-embeds, bulk ops, exports) with actor + timing, scoped by client_id.
"""
from ..db import pool


async def log(client_id: str, action: str, *, document_id: str | None = None,
              method: str | None = None, status: str | None = None,
              detail: str | None = None, actor: str | None = None,
              chunk_count: int | None = None, duration_ms: int | None = None) -> str:
    async with pool().acquire() as conn:
        return str(await conn.fetchval(
            """
            INSERT INTO kb_events
                (client_id, document_id, action, method, status, detail, actor, chunk_count, duration_ms)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
            """,
            client_id, document_id, action, method, status,
            (detail or "")[:2000] or None, actor, chunk_count, duration_ms))


async def finish(event_id: str, status: str, *, chunk_count: int | None = None,
                 duration_ms: int | None = None, detail: str | None = None) -> None:
    if not event_id:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE kb_events SET status=$2, chunk_count=COALESCE($3,chunk_count),
                duration_ms=COALESCE($4,duration_ms), detail=COALESCE($5,detail)
            WHERE id=$1
            """,
            event_id, status, chunk_count, duration_ms, (detail or None) and detail[:2000])


async def list_events(client_id: str, action: str | None = None, limit: int = 100,
                      offset: int = 0) -> list[dict]:
    q = "SELECT * FROM kb_events WHERE client_id=$1"
    args = [client_id]
    if action:
        q += " AND action = $2"
        args.append(action)
    q += f" ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}"
    async with pool().acquire() as conn:
        rows = await conn.fetch(q, *args, min(max(limit, 1), 500), max(offset, 0))
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(r["id"])
        d["document_id"] = str(r["document_id"]) if r["document_id"] else None
        d["created_at"] = r["created_at"].isoformat()
        out.append(d)
    return out
