"""`routers/chat.py::_reserve` — a cap's 429 must say WHICH cap, as a `code` beside `detail`.

Why this deserves a file: the consumer (Swift Chat, CHAT_INTEGRATION.md §1 "Consumer") has to
react to the two caps in OPPOSITE ways. The tenant-per-minute cap means a runaway integration —
pause the bot for the whole tenant. The end-user-per-hour cap means one abusive customer — hand
off that single conversation and keep answering everyone else. Until 2026-09-09 both arrived as
the same bare `{"detail": "Rate limit reached for …"}` and the consumer told them apart by
grepping `per minute` out of English prose; every 429 then opened the tenant's circuit and took
the copilot mirrors down with the bot (worklist W-08).

No database, no network, no model: `limits.reserve_counter` is replaced by a recorder that
refuses the bucket it is told to, so the file runs everywhere, in milliseconds. It runs the
coroutines through `asyncio.run` like the rest of the suite (see conftest) rather than as async
test functions.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app.routers import chat
from app.services.auth import Principal

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
INTEGRATION_ID = "22222222-2222-4222-8222-222222222222"


def _principal() -> Principal:
    return Principal(kind="integration", client_id=CLIENT_ID, via="integration",
                     integration_id=INTEGRATION_ID, scopes=["chat:turn", "chat:answer"])


class _Counter:
    """Stand-in for `limits.reserve_counter`: records every call, refuses one bucket.

    The refusal is the exact prose `limits._counter_exhausted` produces — the consumer build that
    predates the codes matches `per minute` in it, so the text is part of the contract too.
    """
    def __init__(self, refuse: str | None = None, status: int = 429) -> None:
        self.refuse = refuse
        self.status = status
        self.calls: list[tuple[str, str, int, str]] = []

    async def __call__(self, scope_key: str, kind: str, limit: int, bucket: str = "day") -> None:
        self.calls.append((scope_key, kind, limit, bucket))
        if bucket == self.refuse:
            raise HTTPException(status_code=self.status,
                                detail=f"Rate limit reached for {kind} ({limit} per {bucket}).")


@pytest.fixture
def counter(monkeypatch):
    def install(refuse: str | None = None, status: int = 429) -> _Counter:
        c = _Counter(refuse, status)
        monkeypatch.setattr(chat.limits, "reserve_counter", c)
        return c
    return install


# --------------------------------------------------------------------------- #
# 1. The two caps carry two different codes
# --------------------------------------------------------------------------- #
def test_tenant_per_minute_cap_is_rate_limited_tenant(counter):
    c = counter(refuse="minute")

    with pytest.raises(chat.RateLimited) as ei:
        asyncio.run(chat._reserve_turn(_principal(), {}, "end-user-1"))

    assert ei.value.code == chat.RATE_LIMITED_TENANT == "rate_limited_tenant"
    assert "per minute" in ei.value.detail                     # prose kept for the old consumer
    # The refusal stops the walk: the end-user counter is never touched, so one refused message
    # does not also spend an hour-bucket unit against the customer.
    assert [call[3] for call in c.calls] == ["minute"]
    assert c.calls[0][0] == f"tenant:{CLIENT_ID}"
    assert c.calls[0][1] == "chat_turns"
    assert c.calls[0][2] == chat.DEFAULT_TENANT_PER_MINUTE     # `{}` config → the default cap


def test_enduser_per_hour_cap_is_rate_limited_enduser(counter):
    c = counter(refuse="hour")

    with pytest.raises(chat.RateLimited) as ei:
        asyncio.run(chat._reserve_turn(_principal(), {}, "end-user-1"))

    assert ei.value.code == chat.RATE_LIMITED_ENDUSER == "rate_limited_enduser"
    assert "per hour" in ei.value.detail
    assert [call[3] for call in c.calls] == ["minute", "hour"]
    scope = c.calls[1][0]
    assert scope.startswith(f"enduser:{INTEGRATION_ID}:")
    # `usage_counters.scope_key` is long-lived plaintext; the chat site's end-user id is hashed
    # into it, never stored as-is.
    assert "end-user-1" not in scope


def test_answer_meters_its_own_kind_with_the_same_codes(counter):
    c = counter(refuse="minute")

    with pytest.raises(chat.RateLimited) as ei:
        asyncio.run(chat._reserve_answer(_principal(), {}, "end-user-1"))

    assert ei.value.code == chat.RATE_LIMITED_TENANT
    assert c.calls[0][1] == "chat_answer"                      # not the copilot's counter
    assert c.calls[0][2] == chat.DEFAULT_ANSWER_PER_MINUTE


# --------------------------------------------------------------------------- #
# 2. What the route sends: `code` is a sibling of `detail`, and `detail` is unchanged
# --------------------------------------------------------------------------- #
def test_err_body_carries_the_code_beside_the_verbatim_detail(counter):
    counter(refuse="hour")
    with pytest.raises(chat.RateLimited) as ei:
        asyncio.run(chat._reserve_answer(_principal(), {}, "end-user-1"))

    resp = chat._err(429, ei.value.detail, ei.value.code)       # exactly what the routes do
    body = json.loads(resp.body)

    assert resp.status_code == 429
    assert body == {"detail": "Rate limit reached for chat_answer "
                              f"({chat.DEFAULT_ANSWER_ENDUSER_PER_HOUR} per hour).",
                    "code": "rate_limited_enduser"}
    assert isinstance(body["detail"], str)                     # never nested in `detail`


def test_the_three_429_codes_are_distinct():
    """`llm_busy` (admission control) is the third 429 on `/answer`; the consumer branches on
    all three, so they must never collapse into one string."""
    assert len({chat.RATE_LIMITED_TENANT, chat.RATE_LIMITED_ENDUSER, "llm_busy"}) == 3


# --------------------------------------------------------------------------- #
# 3. Edges: no end user, and a non-429 out of the counter
# --------------------------------------------------------------------------- #
def test_no_end_user_consults_only_the_tenant_counter(counter):
    c = counter()
    asyncio.run(chat._reserve_turn(_principal(), {}, None))
    asyncio.run(chat._reserve_turn(_principal(), {}, "   "))   # whitespace is "no end user"

    assert [call[3] for call in c.calls] == ["minute", "minute"]


def test_a_non_429_from_the_counter_is_not_relabelled(counter):
    """Only a cap is ours to name. Anything else the counter raises keeps its own status so a
    genuine outage is not reported to the consumer as "pause the bot for a minute"."""
    counter(refuse="minute", status=503)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat._reserve_turn(_principal(), {}, "end-user-1"))

    assert ei.value.status_code == 503
    assert not isinstance(ei.value, chat.RateLimited)
