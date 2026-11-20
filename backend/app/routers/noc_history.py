"""The NOC's stored history (services/noc_history.py): a day list for the date picker, a window of
samples for the timeline, and the full snapshot nearest a moment. Superadmin only."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..services import noc_history
from .admin import require_admin

router = APIRouter(prefix="/admin/noc", dependencies=[Depends(require_admin)])

MAX_WINDOW = timedelta(hours=48)


def _instant(value: str, name: str) -> datetime:
    """An ISO instant with or without 'Z'; one without any offset is UTC."""
    try:
        d = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, f"'{name}' is not a date-time (use ISO 8601, e.g. 2026-11-20T10:00:00Z).") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.get("/history/days")
async def history_days(tz: str = "UTC", days: int = Query(31)):
    try:
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — unknown key, malformed key, missing tzdata: all "not a zone"
        raise HTTPException(400, f"'{tz}' is not a time zone name (use an IANA name such as Asia/Tbilisi).") from None
    days = max(1, min(60, days))
    return {"tz": tz, "retention_days": settings.noc_retention_days, "days": await noc_history.days(tz, days)}


@router.get("/history")
async def history(from_: str = Query(alias="from"), to: str = Query()):
    start, end = _instant(from_, "from"), _instant(to, "to")
    if end < start:
        raise HTTPException(400, "'to' is before 'from'.")
    if end - start > MAX_WINDOW:
        raise HTTPException(400, "The window is longer than 48 hours; ask for one day at a time.")
    return await noc_history.history(start, end)


@router.get("/snapshot")
async def snapshot(at: str = Query()):
    found = await noc_history.snapshot_at(_instant(at, "at"))
    if not found:
        raise HTTPException(404, "No snapshot was stored near that time.")
    return found
