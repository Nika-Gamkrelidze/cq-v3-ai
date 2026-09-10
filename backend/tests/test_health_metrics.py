"""`services/health_metrics` — the parts that decide what a chart says, with no database.

The sampler's psutil readings and the SQL are exercised by the running deployment; what can go
quietly wrong is the arithmetic around them: a range key that 400s instead of falling back, a
request counted twice because two records missed the same minute bucket, a rate that spikes
negative when a counter resets, or a health setting that accepts `0` days and purges the row
the sampler just wrote. Each of those is a pure function here, driven with injected clocks and
counters — no psutil, no pool, no event loop.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import health_metrics as hm
from app.services import settings_store

CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"


# --------------------------------------------------------------------------- #
# 1. Range keys
# --------------------------------------------------------------------------- #
def test_resolve_range_known_keys_return_their_window_and_step():
    for key, (window, step) in hm.RANGES.items():
        assert hm.resolve_range(key) == (key, window, step)


@pytest.mark.parametrize("bad", [None, "", "  ", "2h", "week", "24"])
def test_resolve_range_falls_back_to_24h_for_anything_else(bad):
    assert hm.resolve_range(bad) == ("24h", timedelta(days=1), 120)


def test_resolve_range_is_case_and_whitespace_tolerant():
    assert hm.resolve_range(" 7D ")[0] == "7d"
    assert hm.resolve_range("1H ")[0] == "1h"


# --------------------------------------------------------------------------- #
# 2. Load accumulator: bucketing, merging, draining
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _acc(t: float = 1_700_000_030.0) -> tuple[hm.LoadAccumulator, _Clock]:
    clock = _Clock(t)
    return hm.LoadAccumulator(clock=clock), clock


def test_records_in_the_same_minute_merge_into_one_row():
    acc, _ = _acc()
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=100.4, status=200,
               bytes_in=10, bytes_out=20)
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=300.0, status=502,
               bytes_in=5, bytes_out=7)
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=50.0, status=404,
               bytes_in=0, bytes_out=1)

    rows = acc.drain()
    assert len(rows) == 1
    row = rows[0]
    assert row["requests"] == 3
    assert row["errors"] == 1                        # the 502 only — a 404 is the caller's
    assert row["total_ms"] == 100 + 300 + 50
    assert row["max_ms"] == 300
    assert (row["bytes_in"], row["bytes_out"]) == (15, 28)
    assert row["principal_kind"] == "tenant"
    assert row["client_key"] == CLIENT_A
    assert row["client_id"] == uuid.UUID(CLIENT_A)   # typed for the uuid column


def test_bucket_is_the_calendar_minute_floored_in_utc():
    acc, _ = _acc(1_700_000_030.0)                   # :30 into a minute
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=1, status=200,
               bytes_in=0, bytes_out=0)
    bucket = acc.drain()[0]["bucket"]
    assert bucket == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert bucket.tzinfo is not None and bucket.second == 0


def test_different_minute_kind_or_client_is_a_different_row():
    acc, clock = _acc(1_700_000_030.0)
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=1, status=200, bytes_in=0, bytes_out=0)
    acc.record(principal_kind="tenant", client_id=CLIENT_B, ms=1, status=200, bytes_in=0, bytes_out=0)
    acc.record(principal_kind="superadmin", client_id=CLIENT_A, ms=1, status=200, bytes_in=0, bytes_out=0)
    acc.record(principal_kind="anonymous", client_id=None, ms=1, status=200, bytes_in=0, bytes_out=0)
    clock.t += 60                                     # next minute
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=1, status=200, bytes_in=0, bytes_out=0)

    rows = acc.drain()
    keys = {(r["bucket"], r["principal_kind"], r["client_key"]) for r in rows}
    assert len(rows) == len(keys) == 5
    anon = next(r for r in rows if r["principal_kind"] == "anonymous")
    assert anon["client_key"] == "" and anon["client_id"] is None


def test_unknown_kind_and_unparseable_client_id_are_kept_but_labelled():
    acc, _ = _acc()
    acc.record(principal_kind="martian", client_id="not-a-uuid", ms=1, status=200,
               bytes_in=0, bytes_out=0)
    row = acc.drain()[0]
    assert row["principal_kind"] == "unknown"        # never a new row per typo
    assert row["client_key"] == "not-a-uuid"         # the label survives for the operator
    assert row["client_id"] is None                  # but the uuid column gets NULL, not a 400


def test_drain_empties_the_accumulator():
    acc, _ = _acc()
    acc.record(principal_kind="user", client_id=None, ms=1, status=200, bytes_in=0, bytes_out=0)
    assert len(acc) == 1
    assert len(acc.drain()) == 1
    assert len(acc) == 0
    assert acc.drain() == []


def test_negative_or_missing_figures_do_not_corrupt_the_counters():
    acc, _ = _acc()
    acc.record(principal_kind="tenant", client_id=CLIENT_A, ms=-5, status=None,
               bytes_in=-1, bytes_out=None)
    row = acc.drain()[0]
    assert (row["total_ms"], row["max_ms"], row["errors"]) == (0, 0, 0)
    assert (row["bytes_in"], row["bytes_out"]) == (0, 0)


# --------------------------------------------------------------------------- #
# 3. Rate derivation between two sampler readings
# --------------------------------------------------------------------------- #
MB = 1024 * 1024


class _Counters:
    def __init__(self, disk, net) -> None:
        self.disk, self.net = disk, net

    def __call__(self) -> dict:
        return {"disk": self.disk, "net": self.net}


def test_first_reading_has_no_rates():
    clock = _Clock(100.0)
    s = hm.HostSampler(clock=clock, read_counters=_Counters((0, 0), (0, 0)), media_root="")
    assert s.rates() == {"disk_read_mb_s": None, "disk_write_mb_s": None,
                         "net_rx_mb_s": None, "net_tx_mb_s": None}


def test_second_reading_derives_mb_per_second_from_the_deltas():
    clock = _Clock(100.0)
    counters = _Counters((10 * MB, 0), (5 * MB, 1 * MB))
    s = hm.HostSampler(clock=clock, read_counters=counters, media_root="")
    s.rates()

    clock.t = 110.0                                   # 10 s later
    counters.disk = (20 * MB, 5 * MB)                 # +10 MB read, +5 MB written
    counters.net = (25 * MB, 1 * MB)                  # +20 MB in, nothing out
    r = s.rates()
    assert r == {"disk_read_mb_s": 1.0, "disk_write_mb_s": 0.5,
                 "net_rx_mb_s": 2.0, "net_tx_mb_s": 0.0}


def test_a_counter_that_went_backwards_is_a_gap_not_a_negative_rate():
    clock = _Clock(100.0)
    counters = _Counters((10 * MB, 10 * MB), (10 * MB, 10 * MB))
    s = hm.HostSampler(clock=clock, read_counters=counters, media_root="")
    s.rates()
    clock.t = 110.0
    counters.net = (1 * MB, 20 * MB)                  # interface reset: rx dropped
    r = s.rates()
    assert r["net_rx_mb_s"] is None and r["net_tx_mb_s"] is None
    assert r["disk_read_mb_s"] == 0.0                 # the other pair is unaffected
    # The reset reading becomes the new baseline, so the next interval is measured from it.
    clock.t = 120.0
    counters.net = (11 * MB, 20 * MB)
    assert s.rates()["net_rx_mb_s"] == 1.0


def test_zero_interval_and_missing_counters_yield_none():
    assert hm.derive_rate((0, 0), (MB, MB), 0) == (None, None)
    assert hm.derive_rate(None, (MB, MB), 10) == (None, None)
    assert hm.derive_rate((0, 0), None, 10) == (None, None)
    clock = _Clock(100.0)
    s = hm.HostSampler(clock=clock, read_counters=lambda: {"disk": None, "net": None}, media_root="")
    s.rates()
    clock.t = 110.0
    assert all(v is None for v in s.rates().values())


def test_a_counter_reader_that_raises_does_not_take_the_sample_down():
    def boom():
        raise OSError("no /proc")
    s = hm.HostSampler(clock=_Clock(1.0), read_counters=boom, media_root="")
    assert all(v is None for v in s.rates().values())


# --------------------------------------------------------------------------- #
# 4. Health settings: bounds and garbage
# --------------------------------------------------------------------------- #
def test_health_defaults_are_within_their_own_bounds():
    for field, value in settings_store.HEALTH_DEFAULTS.items():
        assert settings_store._health_setting(field, value) == value


@pytest.mark.parametrize("field,value,expected", [
    ("retention_days", "7", 7), ("retention_days", 1, 1), ("retention_days", 365, 365),
    ("retention_days", 30.0, 30),
    ("sample_interval_s", 5, 5), ("sample_interval_s", "60", 60), ("sample_interval_s", 300, 300),
])
def test_health_setting_accepts_whole_numbers_in_range(field, value, expected):
    assert settings_store._health_setting(field, value) == expected


@pytest.mark.parametrize("field,value", [
    ("retention_days", 0), ("retention_days", 366), ("retention_days", -1),
    ("retention_days", "week"), ("retention_days", None), ("retention_days", True),
    ("sample_interval_s", 4), ("sample_interval_s", 301), ("sample_interval_s", ""),
    ("sample_interval_s", False), ("sample_interval_s", [10]),
])
def test_health_setting_refuses_out_of_range_and_garbage(field, value):
    with pytest.raises(ValueError) as ei:
        settings_store._health_setting(field, value)
    # The route answers 400 {detail, code, field}: the field must be on the error, not in prose.
    assert isinstance(ei.value, settings_store.HealthSettingError)
    assert ei.value.field == field


def test_unknown_health_field_is_refused():
    with pytest.raises(ValueError) as ei:
        settings_store._health_setting("purge_now", 1)
    assert ei.value.field == "purge_now"


def test_purge_validates_retention_before_touching_the_database():
    """`purge(0)` must never run: a 0-day cutoff deletes the sample written a second ago."""
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(hm.purge(0))
