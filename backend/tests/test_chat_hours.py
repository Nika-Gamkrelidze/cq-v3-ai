"""The business clock (services/chat_hours.py): what the public bot is told about the time.

Pure — no database, no model — so it runs anywhere. The first pilot conversation opened with
"what day is today?" and the bot had nothing to answer from.
"""
from datetime import datetime, timezone

import pytest

from app.services import chat_hours

WEEK = {
    "mon": [{"open": "09:00", "close": "18:00"}],
    "tue": [{"open": "09:00", "close": "18:00"}],
    "wed": [{"open": "09:00", "close": "18:00"}],
    "thu": [{"open": "09:00", "close": "18:00"}],
    "fri": [{"open": "09:00", "close": "18:00"}],
    "sat": [{"open": "10:00", "close": "14:00"}],
    "sun": [],
}


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_validate_timezone_accepts_iana_names_and_names_the_field_otherwise():
    assert chat_hours.validate_timezone(" Asia/Tbilisi ") == "Asia/Tbilisi"
    for bad in ("Mars/Olympus", "", None, 4):
        with pytest.raises(ValueError, match="settings.timezone"):
            chat_hours.validate_timezone(bad)


def test_validate_opening_hours_fills_the_week_and_rejects_bad_shapes():
    assert chat_hours.validate_opening_hours(None) is None
    week = chat_hours.validate_opening_hours({"MON": [{"open": "09:00", "close": "18:00"}]})
    assert week["mon"] == [{"open": "09:00", "close": "18:00"}]
    assert week["sun"] == [] and set(week) == set(chat_hours.DAYS)
    # Past midnight is a real schedule, not an error.
    assert chat_hours.validate_opening_hours({"fri": [{"open": "22:00", "close": "02:00"}]})

    for bad in (
        [],
        {"someday": []},
        {"mon": "09-18"},
        {"mon": [{"open": "9am", "close": "18:00"}]},
        {"mon": [{"open": "24:00", "close": "18:00"}]},
        {"mon": [{"open": "09:00", "close": "09:00"}]},
        {"mon": [{"open": "09:00", "close": "10:00"}] * 4},
    ):
        with pytest.raises(ValueError, match="settings.opening_hours"):
            chat_hours.validate_opening_hours(bad)


@pytest.mark.parametrize("now, expected", [
    (_utc(2026, 9, 14, 11, 42), "open — closes today at 18:00"),        # Mon 15:42 Tbilisi
    (_utc(2026, 9, 14, 15, 30), "closed — opens tomorrow at 09:00"),    # Mon 19:30
    (_utc(2026, 9, 19, 7, 0), "open — closes today at 14:00"),          # Sat 11:00
    (_utc(2026, 9, 19, 11, 0), "closed — opens on Monday at 09:00"),    # Sat 15:00, Sun closed
])
def test_open_status_in_the_business_time_zone(now, expected):
    local = now.astimezone(chat_hours._zone("Asia/Tbilisi")[0])
    assert chat_hours.open_status(WEEK, local) == expected


def test_an_interval_that_started_yesterday_is_still_open_after_midnight():
    late = {"fri": [{"open": "22:00", "close": "02:00"}]}
    local = _utc(2026, 9, 18, 21, 0).astimezone(chat_hours._zone("Asia/Tbilisi")[0])  # Sat 01:00
    assert chat_hours.open_status(late, local) == "open — closes today at 02:00"
    assert chat_hours.open_status({d: [] for d in chat_hours.DAYS}, local) == "closed all week"


def test_clock_text_gives_local_date_time_hours_status_and_the_note():
    text = chat_hours.clock_text("Asia/Tbilisi", WEEK, "Closed on public holidays.",
                                 now=_utc(2026, 9, 14, 11, 42))
    assert "Monday, 14 September 2026, 15:42 (Asia/Tbilisi, UTC+04:00)" in text
    assert "Opening hours: Monday 09:00–18:00;" in text and "Sunday closed" in text
    assert "Right now the business is open — closes today at 18:00." in text
    assert "Closed on public holidays." in text


def test_clock_text_without_hours_tells_the_model_not_to_guess_them():
    text = chat_hours.clock_text(None, None, now=_utc(2026, 9, 14, 11, 42))
    assert "(Asia/Tbilisi, UTC+04:00)" in text        # the default zone
    assert "not provided" in text and "never state or guess" in text


def test_a_stored_zone_that_no_longer_loads_falls_back_to_utc():
    text = chat_hours.clock_text("Mars/Olympus", None, now=_utc(2026, 9, 14, 11, 42))
    assert "11:42 (UTC, UTC+00:00)" in text
