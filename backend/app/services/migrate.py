"""Startup migrations run on every boot (idempotent).

Applies the analyzer + KB SQL, reconciles the pgvector embedding dimension to the
configured provider, and seeds a demo tenant API key. Safe on already-provisioned
volumes where the initdb scripts have already run.
"""
import logging
import secrets
from pathlib import Path

from ..db import pool
from .settings_store import get_embedding_config

log = logging.getLogger("cq")

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "db"


async def _apply(conn, filename: str) -> None:
    await conn.execute((_DB_DIR / filename).read_text())
    # One line per file, so "is my new db/*.sql actually in the list?" is answered by the
    # boot log rather than by a missing column at request time.
    log.info("startup migration: applied %s", filename)


async def _current_embedding_dim(conn) -> int | None:
    t = await conn.fetchval(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a JOIN pg_class c ON a.attrelid = c.oid
        WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'
        """
    )
    if not t or "(" not in t:
        return None
    try:
        return int(t.split("(")[1].rstrip(")"))
    except ValueError:
        return None


async def _reconcile_embedding_dim(conn, target_dim: int) -> str:
    current = await _current_embedding_dim(conn)
    if current == target_dim:
        return f"embedding dim OK ({current})"
    count = await conn.fetchval("SELECT count(*) FROM kb_chunks")
    if count and count > 0:
        # Don't silently drop data — surface a clear signal; requires re-embedding.
        return (f"WARNING: embedding dim mismatch (column={current}, provider={target_dim}) "
                f"but {count} chunks exist. Re-embed the KB, then this will reconcile.")
    # Empty table — safe to recreate the column at the new dimension.
    await conn.execute("DROP INDEX IF EXISTS idx_kb_chunks_embedding")
    await conn.execute(f"ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector({target_dim})")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding "
        "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    return f"embedding dim migrated {current} -> {target_dim}"


async def _seed_demo_tenant(conn) -> None:
    # Give the seeded 'demo' client an API key if it lacks one (dev convenience).
    await conn.execute(
        """
        UPDATE clients SET api_key = $1
        WHERE slug = 'demo' AND (api_key IS NULL OR api_key = '')
        """,
        "cq_" + secrets.token_hex(24),
    )


async def run_startup_migrations() -> list[str]:
    log: list[str] = []
    async with pool().acquire() as conn:
        # Apply schema first — app_settings (read by get_embedding_config) lives in
        # analyzer.sql, so on a fresh database it must exist before we read config.
        await _apply(conn, "analyzer.sql")
        await _apply(conn, "kb.sql")
        await _apply(conn, "scoring.sql")
        await _apply(conn, "partner.sql")
        # This list is hardcoded (no glob): a new db/*.sql file that isn't added here
        # is inert — it silently never runs, on every environment.
        await _apply(conn, "chat.sql")
        # curation.sql AFTER chat.sql: it is the same feature's second half and its comments
        # reference the chat tables the miner reads.
        await _apply(conn, "curation.sql")
        # kb_ops.sql: the tenant KB console's queued full-KB re-embed jobs.
        await _apply(conn, "kb_ops.sql")
        # media.sql: anonymous-submission retention (IP/audio/text) + acoustic sentiment.
        # Last because it only ALTERs audio_jobs, which analyzer.sql created above.
        await _apply(conn, "media.sql")
        # sentiment_config.sql: per-tenant on/off + guidance for standalone sentiment.
        # After media.sql for no structural reason — just keeps the "recent additions"
        # together at the tail of the list.
        await _apply(conn, "sentiment_config.sql")
        # convert.sql: the anonymous meter column for the Asterisk audio converter. Its only
        # statement ALTERs anon_usage, which kb.sql created above.
        await _apply(conn, "convert.sql")
        # workbench.sql: Call Workbench v2 (segments, registered users, conversion history,
        # summaries, personal rubrics). Last: it ALTERs audio_jobs, tts_requests and
        # scoring_configs, all created above.
        await _apply(conn, "workbench.sql")
        # score_edits.sql: the manual-override history behind audio_jobs.scoring. After
        # workbench.sql because its FK points at audio_jobs, which analyzer.sql created.
        await _apply(conn, "score_edits.sql")
        # score_bands.sql: per-workspace red/amber/green thresholds for a scorecard.
        await _apply(conn, "score_bands.sql")
        # actor.sql: the name of whoever created a recording or summary.
        await _apply(conn, "actor.sql")
        # ai_usage.sql: who/which-recording columns on llm_usage, plus per-tenant AI
        # overrides. After chat.sql, which created llm_usage, and after analyzer.sql, whose
        # audio_jobs the new job_id refers to (by value — deliberately not a FK).
        await _apply(conn, "ai_usage.sql")
        emb = await get_embedding_config()
        log.append(await _reconcile_embedding_dim(conn, int(emb["dim"])))
        await _seed_demo_tenant(conn)
    return log
