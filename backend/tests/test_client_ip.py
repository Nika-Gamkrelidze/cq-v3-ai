"""The address a request is attributed to — and the anonymous allowance keyed on it.

This file exists because of one production bug: every anonymous visitor on every device shared
a SINGLE daily quota, so the first person to spend it locked out the world. The cause was not
the trust rule but the value it was fed — the container is reached through the host's NAT, so
`$remote_addr` (and therefore X-Real-IP) is the bridge gateway, one constant for all callers.
`anon_usage` had exactly one row, keyed `172.22.0.1`.

So there are two properties here, and they are different:

  * **The trust rule** (`client_ip`) — X-Real-IP first, then the LAST X-Forwarded-For element,
    then the socket peer. Never the first XFF element: our own edge replaces XFF now, but any
    proxy that *appends* leaves that element under the caller's control, and reading it let
    anyone mint a fresh quota bucket per request. The app must be safe even if the edge is
    misconfigured, so this is pinned independently of nginx.
  * **The identity rule** (`visitor_key`) — the address is a quota key ONLY if it can single
    out one visitor. An address our own network could have substituted (loopback, RFC1918 —
    where every docker bridge lives) cannot, and the key is then None so the anonymous tier
    fails closed. Pooling is the one outcome that must not survive.

The regression that matters is the last group: two callers with different addresses get
different buckets, end to end, through the real `GET /limits`.

No keys, no model calls, no network. The pure-function half runs without a database; the HTTP
half uses conftest's `api` fixture and skips when Postgres is absent.
"""
import datetime as dt

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.services import limits
from app.services.auth import (Principal, can_identify_visitor, client_ip, resolve_principal,
                               visitor_key)
from conftest import sql  # loop-independent SQL; see its module docstring

# Documentation ranges (RFC 5737): routable-looking, guaranteed never to be real traffic, and
# distinct from anything a developer's own network could produce.
VISITOR_A = "203.0.113.7"
VISITOR_B = "198.51.100.9"
SPOOFED = "203.0.113.250"        # what an attacker puts in their own X-Forwarded-For
NAT_GATEWAY = "172.22.0.1"       # what this deployment actually hands the app today
TEST_KEYS = (VISITOR_A, VISITOR_B, NAT_GATEWAY)


def _request(headers: dict | None = None, peer: str | None = "192.0.2.55") -> Request:
    """A bare ASGI request. Built by hand rather than through TestClient because the socket
    peer is one of the three inputs under test, and TestClient always reports 'testclient'."""
    scope = {
        "type": "http", "method": "GET", "path": "/limits", "query_string": b"",
        "scheme": "http", "root_path": "", "http_version": "1.1",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 51234) if peer else None,
    }
    return Request(scope)


# --------------------------------------------------------------------------- #
# The trust rule: which of the three inputs wins
# --------------------------------------------------------------------------- #
def test_x_real_ip_wins_over_everything():
    """nginx sets X-Real-IP from $remote_addr, so it is the one value our own edge vouches for."""
    req = _request({"x-real-ip": VISITOR_A,
                    "x-forwarded-for": f"{SPOOFED}, {VISITOR_B}"}, peer="10.1.2.3")
    assert client_ip(req) == VISITOR_A


def test_last_forwarded_for_element_is_used_never_the_first():
    """A proxy that appends writes ITS peer last; everything before it came from the caller."""
    req = _request({"x-forwarded-for": f"{SPOOFED}, {VISITOR_A}"}, peer="10.1.2.3")
    assert client_ip(req) == VISITOR_A


def test_a_single_forwarded_for_element_is_still_the_last_one():
    req = _request({"x-forwarded-for": VISITOR_B}, peer="10.1.2.3")
    assert client_ip(req) == VISITOR_B


def test_forwarded_for_whitespace_and_empty_elements_are_ignored():
    req = _request({"x-forwarded-for": f" {SPOOFED} , , {VISITOR_A} , "}, peer="10.1.2.3")
    assert client_ip(req) == VISITOR_A


def test_socket_peer_is_the_last_resort():
    assert client_ip(_request({}, peer=VISITOR_B)) == VISITOR_B


def test_no_headers_and_no_peer_is_unknown_not_a_crash():
    assert client_ip(_request({}, peer=None)) == "unknown"


def test_a_client_supplied_forwarded_for_cannot_choose_the_key():
    """The whole spoofing scenario in one assertion: the caller sends their own XFF hoping to
    be metered as somebody else (or as a fresh, unspent bucket). Our proxy's element is what
    counts, and with X-Real-IP present the header is not consulted at all."""
    honest = _request({"x-real-ip": VISITOR_A}, peer="10.1.2.3")
    spoofing = _request({"x-real-ip": VISITOR_A,
                         "x-forwarded-for": f"{SPOOFED}, {SPOOFED}"}, peer="10.1.2.3")
    assert client_ip(spoofing) == client_ip(honest) == VISITOR_A
    assert visitor_key(spoofing) == visitor_key(honest) == VISITOR_A


# --------------------------------------------------------------------------- #
# The identity rule: can this address single out a visitor at all
# --------------------------------------------------------------------------- #
@pytest.fixture
def public_deployment(monkeypatch):
    """Pin the knob a LAN/local deployment would flip, so these tests assert the production
    policy regardless of what the developer's .env happens to say."""
    monkeypatch.setattr(settings, "anon_trust_private_client_ips", False)


@pytest.mark.parametrize("addr", [VISITOR_A, VISITOR_B, "8.8.8.8",
                                  "100.64.3.9",          # CGNAT: shared, but a REAL visitor
                                  "2001:db8::1",
                                  "testclient"])         # an ASGI transport, not an address
def test_addresses_that_can_identify_a_visitor(addr, public_deployment):
    assert can_identify_visitor(addr) is True


@pytest.mark.parametrize("addr", [NAT_GATEWAY, "172.17.0.1", "10.0.0.1", "192.168.1.1",
                                  "127.0.0.1", "169.254.1.1", "0.0.0.0", "::1", "fe80::1",
                                  "", "   ", "unknown"])
def test_addresses_that_cannot(addr, public_deployment):
    assert can_identify_visitor(addr) is False


def test_carrier_grade_nat_is_a_visitor_not_a_gateway(public_deployment):
    """100.64.0.0/10 is where a lot of this product's mobile audience lives. Sharing one
    allowance with the rest of that CGNAT pool is the accepted trade; being refused outright
    is not."""
    assert visitor_key(_request({"x-real-ip": "100.100.7.7"})) == "100.100.7.7"


def test_the_nat_gateway_yields_no_key_rather_than_a_shared_one(public_deployment):
    """The bug, at its source: this is the value production actually hands the app."""
    assert visitor_key(_request({"x-real-ip": NAT_GATEWAY})) is None


def test_a_lan_deployment_can_opt_back_in(monkeypatch):
    monkeypatch.setattr(settings, "anon_trust_private_client_ips", True)
    assert visitor_key(_request({"x-real-ip": "192.168.1.50"})) == "192.168.1.50"


def test_resolve_principal_keys_the_anonymous_principal_on_the_visitor(public_deployment):
    import asyncio

    def resolve(headers):
        return asyncio.run(resolve_principal(_request(headers), "", "", "", "", "", ""))

    assert resolve({"x-real-ip": VISITOR_A}).anon_key == VISITOR_A
    assert resolve({"x-real-ip": VISITOR_B}).anon_key == VISITOR_B
    assert resolve({"x-real-ip": NAT_GATEWAY}).anon_key is None
    assert resolve({"x-real-ip": NAT_GATEWAY}).kind == "anonymous"   # still a principal


# --------------------------------------------------------------------------- #
# Fail closed: an unidentifiable visitor is refused, never pooled
# --------------------------------------------------------------------------- #
UNCAPPED = {"enabled": True, "max_analyses_per_day": 0, "max_tts_per_day": 0,
            "max_conversions_per_day": 0, "max_audio_mb": 0,
            "features": {"analyze": True, "tts": True, "convert": True}}


@pytest.fixture
def anon_config(monkeypatch):
    """Serve the anonymous settings blob from memory. With every cap uncapped, `check()`
    returns before it touches the database — so this whole group runs with no Postgres and no
    event-loop juggling, and any DB access would be a bug rather than a dependency."""
    async def _cfg():
        return dict(UNCAPPED)
    monkeypatch.setattr(limits.settings_store, "get_anonymous_config", _cfg)


def _check(anon_key, kind="tts"):
    import asyncio
    return asyncio.run(limits.check(
        Principal(kind="anonymous", via="none", anon_key=anon_key), kind))


def test_an_identified_visitor_passes_the_gate(anon_config):
    assert _check(VISITOR_A) is None


def test_an_unidentifiable_visitor_is_refused_with_503(anon_config):
    """503, not 429: this caller's allowance is fine — the SERVER cannot meter, which is an
    operator-fixable condition. The refusal must never be silent success on a shared bucket."""
    with pytest.raises(HTTPException) as exc:
        _check(None)
    assert exc.value.status_code == 503
    assert "sign in" in exc.value.detail.lower()


@pytest.mark.parametrize("kind", ["analyses", "tts", "conversions"])
def test_every_metered_kind_fails_closed(anon_config, kind):
    with pytest.raises(HTTPException) as exc:
        _check(None, kind)
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------- #
# The regression, end to end: different addresses -> different buckets
# --------------------------------------------------------------------------- #
@pytest.fixture
def usage_rows(api):
    """Today's counters for two distinct visitors and for the NAT gateway, written straight to
    `anon_usage` — the same rows `reserve()` would have written, without spending real quota on
    ElevenLabs or Claude to produce them."""
    today = dt.date.today()

    def _seed(conn):
        return conn.execute(
            """
            INSERT INTO anon_usage (anon_key, day, analyses, tts, conversions)
            VALUES ($1, $4, 1, 11, 0), ($2, $4, 0, 3, 0), ($3, $4, 0, 20, 0)
            ON CONFLICT (anon_key, day) DO UPDATE
              SET analyses = EXCLUDED.analyses, tts = EXCLUDED.tts,
                  conversions = EXCLUDED.conversions
            """, VISITOR_A, VISITOR_B, NAT_GATEWAY, today)
    sql(_seed)
    yield today
    sql(lambda c: c.execute("DELETE FROM anon_usage WHERE anon_key = ANY($1::text[])",
                            list(TEST_KEYS)))


def _limits(api, headers):
    r = api.get("/limits", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_two_visitors_do_not_share_one_allowance(api, usage_rows, public_deployment):
    """THE regression. Before the fix both callers were keyed on the same NAT address, so the
    second one saw the first one's 11 spent TTS calls — from a machine that had never used the
    service — and was locked out when they reached the cap."""
    a = _limits(api, {"X-Real-IP": VISITOR_A})
    b = _limits(api, {"X-Real-IP": VISITOR_B})
    assert a["used"]["tts"] == 11
    assert b["used"]["tts"] == 3
    assert a["remaining"]["tts"] != b["remaining"]["tts"]


def test_a_visitor_who_has_never_called_starts_at_zero(api, usage_rows, public_deployment):
    fresh = _limits(api, {"X-Real-IP": "203.0.113.42"})
    assert fresh["used"] == {"analyses": 0, "tts": 0, "conversions": 0}
    assert fresh["anonymous"] is True


def test_a_spoofed_forwarded_for_cannot_open_a_fresh_bucket(api, usage_rows, public_deployment):
    """No X-Real-IP (a proxy that only appends XFF), and the caller prepends an address of
    their own choosing. They are still metered on the element OUR proxy added."""
    spoofed = _limits(api, {"X-Forwarded-For": f"{SPOOFED}, {VISITOR_A}"})
    assert spoofed["used"]["tts"] == 11


def test_the_nat_gateway_reports_unavailable_instead_of_a_strangers_usage(
        api, usage_rows, public_deployment):
    """A seeded row exists for the gateway with the day's cap fully spent. A visitor arriving
    that way must be told the tier is unavailable — not handed somebody else's 20 spent calls
    as their own."""
    degraded = _limits(api, {"X-Real-IP": NAT_GATEWAY})
    assert degraded["visitor_identified"] is False
    assert degraded["enabled"] is False
    assert degraded["used"] == {"analyses": 0, "tts": 0, "conversions": 0}
    # Shape is unchanged for the page that renders it: same keys, same nesting.
    for key in ("anonymous", "features", "max_tts_per_day", "max_audio_mb", "remaining"):
        assert key in degraded


def test_health_reports_the_addressing_state(api, public_deployment):
    """The one-curl diagnosis, for a server nobody can SSH into without the VPN."""
    assert api.get("/health", headers={"X-Real-IP": VISITOR_A}
                   ).json()["client_addressing"] == "ok"
    assert api.get("/health", headers={"X-Real-IP": NAT_GATEWAY}
                   ).json()["client_addressing"] == "nat-masked"
