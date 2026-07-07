# backend/ — Claude context

FastAPI service + (later) AI workers. See the root CLAUDE.md for the big picture.

## Layout
- `app/main.py` — FastAPI app + `/health`.
- `app/config.py` — settings from env (pydantic-settings).
- `app/db.py` — asyncpg pool (connect on lifespan startup).
- `app/models.py` — pydantic request/response models.
- `app/routers/calls.py` — `POST /calls` (ingest, idempotent, X-API-Key), `GET /calls/{id}`.
- `app/services/` — (to build) elevenlabs transcription, claude scoring, pgvector retrieval.
- `app/workers/` — (to build) transcribe + score background processes.
- `db/schema.sql`, `db/seed.sql` — schema (8 tables) + dev seed (demo client).

## Transcription worker (the next build)
New file `app/workers/transcribe.py`: query `calls WHERE status='pending'`, fetch audio from
object storage (S3/Spaces — TBD), call Scribe (diarize on, language auto), insert into
`transcripts`, update `calls.status`. Run as a separate compose service using the SAME image
with a different command, triggered on a schedule / simple queue.

## Conventions
- Raw SQL via asyncpg ($1 placeholders). uuid PKs (gen_random_uuid). timestamptz everywhere.
- Return pydantic models; keep DB writes idempotent (ON CONFLICT) where PHP may retry.
