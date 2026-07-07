from fastapi import APIRouter, Depends, Header, HTTPException

from ..config import settings
from ..db import pool
from ..models import CallIngest, CallOut

router = APIRouter(prefix="/calls", tags=["calls"])


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("", response_model=CallOut, dependencies=[Depends(require_api_key)])
async def ingest_call(payload: CallIngest) -> CallOut:
    """Endpoint the PHP app POSTs to when a call is recorded. Idempotent."""
    async with pool().acquire() as conn:
        client_id = await conn.fetchval(
            "SELECT id FROM clients WHERE slug = $1", payload.client_slug
        )
        if client_id is None:
            raise HTTPException(status_code=404, detail="Unknown client_slug")

        operator_id = None
        if payload.operator_external_ref is not None:
            operator_id = await conn.fetchval(
                """
                INSERT INTO operators (client_id, external_ref, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (client_id, external_ref)
                DO UPDATE SET name = COALESCE(EXCLUDED.name, operators.name)
                RETURNING id
                """,
                client_id, payload.operator_external_ref, payload.operator_name,
            )

        row = await conn.fetchrow(
            """
            INSERT INTO calls
                (client_id, operator_id, external_ref, audio_uri, language, duration_sec, recorded_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (client_id, external_ref) DO NOTHING
            RETURNING id, external_ref, status
            """,
            client_id, operator_id, payload.external_ref, payload.audio_uri,
            payload.language, payload.duration_sec, payload.recorded_at,
        )

        if row is not None:
            return CallOut(id=str(row["id"]), external_ref=row["external_ref"],
                           status=row["status"], created=True)

        existing = await conn.fetchrow(
            "SELECT id, external_ref, status FROM calls WHERE client_id = $1 AND external_ref = $2",
            client_id, payload.external_ref,
        )
        return CallOut(id=str(existing["id"]), external_ref=existing["external_ref"],
                       status=existing["status"], created=False)


@router.get("/{call_id}")
async def get_call(call_id: str):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, external_ref, status, language, duration_sec, recorded_at, created_at
            FROM calls WHERE id = $1
            """,
            call_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Call not found")
        return dict(row)
