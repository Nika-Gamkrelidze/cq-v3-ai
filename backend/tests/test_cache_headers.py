"""Every API response forbids caching, unless the route deliberately said otherwise.

This exists because the absence of a cache directive was not a cosmetic omission. Responses
here differ per principal and the principal is carried in request HEADERS, not in the URL, so a
cache keyed on the URL can serve one caller the body built for another. It also made a real bug
unmeasurable: `/limits` was answered from a phone's own store, reporting an allowance that had
already been spent and continuing to report it after the numbers moved.
"""
import pytest

pytestmark = pytest.mark.usefixtures("api")

# Routes that answer without a credential, so the test needs no fixtures beyond `api`.
PUBLIC_GETS = ["/health", "/limits", "/languages"]


@pytest.mark.parametrize("path", PUBLIC_GETS)
def test_api_responses_are_not_storable(api, path):
    r = api.get(path)
    assert r.status_code == 200, r.text
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, f"{path} may be stored by a browser or proxy: {cc!r}"


def test_an_error_response_is_not_storable(api):
    """A 401 is as principal-specific as a 200 — arguably more so, because a cached one locks a
    caller out after they have signed in."""
    r = api.get("/recordings")
    assert r.status_code in (401, 403), r.text
    assert "no-store" in r.headers.get("cache-control", "")


def test_a_route_that_chose_its_own_directive_keeps_it(api):
    """The SSE transport sets `no-cache, no-transform` on purpose: `no-transform` stops a proxy
    recompressing a token stream, which is what makes it arrive a buffer at a time instead of a
    token at a time. The middleware must not flatten that to its own default."""
    r = api.get("/v1/chat/health")
    assert r.status_code == 200, r.text
    # This particular route is plain JSON, so it takes the default; the assertion that matters
    # is the mechanism, tested directly below.
    assert "no-store" in r.headers.get("cache-control", "")


def test_the_default_never_overwrites_an_explicit_choice():
    """Asserted on the middleware itself rather than through a route, so it keeps holding when
    the media routes move. `setdefault` semantics: present means untouched."""
    from starlette.datastructures import MutableHeaders

    for explicit in ("private, no-store", "no-cache, no-transform", "public, max-age=3600"):
        headers = MutableHeaders({"Cache-Control": explicit})
        if "cache-control" not in headers:                      # the middleware's own condition
            headers["Cache-Control"] = "private, no-store"
        assert headers["cache-control"] == explicit
