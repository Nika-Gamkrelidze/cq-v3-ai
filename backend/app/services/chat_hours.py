"""The business clock: a tenant's time zone and weekly opening hours, and what the bot is told.

The first pilot conversation opened with "what day is today?" and the bot had to say it did not
know — nothing in the request carried a date. A support bot is asked "are you open now?" and
"can you install today?" constantly, and both need the tenant's local time, not the server's
UTC. So every autopilot model call gets the lines `clock_text` renders.

Pure and synchronous (zoneinfo only), so validation and the rendered text are testable with a
pinned `now` and no database. `settings.opening_hours` shape:

    {"mon": [{"open": "09:00", "close": "18:00"}], ..., "sun": []}

An empty list is a closed day, a missing day in a present object is closed too, and an absent
object means "not configured" — which the bot is told, so it does not invent hours. A close
time earlier than the open time runs past midnight.
"""
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Tbilisi"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_INTERVALS = 3
MAX_NOTE_CHARS = 500

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# --- validation ------------------------------------------------------------------

def validate_timezone(value) -> str:
    """An IANA zone name zoneinfo can load, stripped. Raises ValueError naming the field."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("settings.timezone must be a time zone name such as Asia/Tbilisi")
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"settings.timezone: unknown time zone {name!r}") from exc
    return name


def validate_opening_hours(value) -> dict | None:
    """The normalised weekly hours (all seven days present), or None for "not configured"."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("settings.opening_hours must be an object keyed by weekday (mon … sun)")
    out: dict = {}
    for key, intervals in value.items():
        day = str(key).strip().lower()
        if day not in DAYS:
            raise ValueError(f"settings.opening_hours.{key}: unknown day "
                             "(expected mon, tue, wed, thu, fri, sat, sun)")
        if intervals is None:
            intervals = []
        if not isinstance(intervals, list):
            raise ValueError(f"settings.opening_hours.{day} must be a list of open/close times")
        if len(intervals) > MAX_INTERVALS:
            raise ValueError(f"settings.opening_hours.{day}: at most {MAX_INTERVALS} "
                             "intervals per day")
        clean = []
        for i, iv in enumerate(intervals):
            if not isinstance(iv, dict):
                raise ValueError(f"settings.opening_hours.{day}[{i}] must be "
                                 "{\"open\": \"HH:MM\", \"close\": \"HH:MM\"}")
            opens, closes = str(iv.get("open") or ""), str(iv.get("close") or "")
            if not _HHMM.match(opens) or not _HHMM.match(closes):
                raise ValueError(f"settings.opening_hours.{day}[{i}]: open and close must be "
                                 "times written HH:MM")
            if opens == closes:
                raise ValueError(f"settings.opening_hours.{day}[{i}]: open and close must differ")
            clean.append({"open": opens, "close": closes})
        out[day] = clean
    for day in DAYS:
        out.setdefault(day, [])
    return out


# --- rendering -------------------------------------------------------------------

def _zone(name: str | None):
    """The tenant's zone, or UTC when the stored name no longer loads — a clock that is an
    honest UTC beats a turn that fails because a tz database lost an alias."""
    try:
        return ZoneInfo(str(name or DEFAULT_TIMEZONE)), str(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc, "UTC"


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _offset(local: datetime) -> str:
    raw = local.strftime("%z") or "+0000"
    return f"UTC{raw[:3]}:{raw[3:]}"


def _when(moment: datetime, today: date) -> str:
    """'today at 09:00' | 'tomorrow at 09:00' | 'on Tuesday at 09:00'."""
    at = moment.strftime("%H:%M")
    days = (moment.date() - today).days
    if days == 0:
        return f"today at {at}"
    if days == 1:
        return f"tomorrow at {at}"
    return f"on {_DAY_NAMES[moment.weekday()]} at {at}"


def _intervals(hours: dict, zone, start: date, span_days: int):
    """Absolute (start, end) datetimes for every interval on the days [start, start+span)."""
    for offset in range(span_days):
        day = start + timedelta(days=offset)
        for iv in hours.get(DAYS[day.weekday()]) or []:
            try:
                opens, closes = _hhmm(iv["open"]), _hhmm(iv["close"])
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            begin = datetime.combine(day, opens, tzinfo=zone)
            end = datetime.combine(day, closes, tzinfo=zone)
            if end <= begin:                       # past midnight
                end += timedelta(days=1)
            yield begin, end


def open_status(hours: dict | None, local: datetime) -> str:
    """'open — closes today at 18:00' | 'closed — opens tomorrow at 09:00' | 'closed all week'."""
    if not hours:
        return ""
    zone = local.tzinfo
    today = local.date()
    # Yesterday is included for intervals that started last night and are still open.
    spans = sorted(_intervals(hours, zone, today - timedelta(days=1), 9))
    for begin, end in spans:
        if begin <= local < end:
            return f"open — closes {_when(end, today)}"
    for begin, _end in spans:
        if begin > local:
            return f"closed — opens {_when(begin, today)}"
    return "closed all week"


def _hours_line(hours: dict) -> str:
    parts = []
    for i, day in enumerate(DAYS):
        intervals = hours.get(day) or []
        if intervals:
            spans = ", ".join(f"{iv['open']}–{iv['close']}" for iv in intervals
                              if isinstance(iv, dict) and iv.get("open") and iv.get("close"))
            parts.append(f"{_DAY_NAMES[i]} {spans or 'closed'}")
        else:
            parts.append(f"{_DAY_NAMES[i]} closed")
    return "; ".join(parts)


def clock_text(tz_name: str | None, hours: dict | None, note: str | None = None,
               now: datetime | None = None) -> str:
    """The lines every autopilot call is given about the business's time.

    English on purpose: they are instructions to the model, which answers in the customer's
    language, like every other line of the prompt. `now` is injectable for tests; the default
    is the real clock.
    """
    zone, name = _zone(tz_name)
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    lines = [
        f"Current local date and time for this business: {_DAY_NAMES[local.weekday()]}, "
        f"{local.day} {_MONTH_NAMES[local.month - 1]} {local.year}, {local.strftime('%H:%M')} "
        f"({name}, {_offset(local)})."
    ]
    if isinstance(hours, dict) and hours:
        lines.append(f"Opening hours: {_hours_line(hours)}.")
        lines.append(f"Right now the business is {open_status(hours, local)}.")
    else:
        lines.append("Opening hours: not provided — never state or guess them; offer a "
                     "colleague instead.")
    note = str(note or "").strip()[:MAX_NOTE_CHARS]
    if note:
        lines.append(f"The business's own note about its hours: {note}")
    return "\n".join(lines)
