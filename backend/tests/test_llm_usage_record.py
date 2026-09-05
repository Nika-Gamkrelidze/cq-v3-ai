"""The accounting layer must never be what breaks an AI call.

These exist because it already did. `call_tool` was changed to pass `actor=` and `job_id=` to
`_record()` without `_record()` growing the parameters, and because one of those calls sits in
an `except anthropic.APIError` handler, the resulting TypeError REPLACED the real error and
took down every forced-tool feature at once — analysis, fact-check, scoring, restructure,
rubric import, summarise, sentiment, chat tools. Nothing caught it: no test called `_record`,
and the mismatch is invisible to a linter that does not resolve keyword arguments.

So the contract under test is not "usage is recorded correctly" but the stronger, duller
"telemetry cannot raise": whatever the call sites pass, and whatever the database does,
the caller still gets its answer or its real error.
"""
import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from app.services import ai_config, llm

LLM_SRC = Path(llm.__file__).read_text(encoding="utf-8")


def test_every_record_call_matches_the_signature():
    """Static check: no `_record(...)` may pass a keyword `_record` does not accept.

    This is the exact defect that shipped, and it is worth checking structurally rather than
    by calling each site, because the failing sites are error paths that only run when
    Anthropic is already unreachable — the case least likely to be exercised in a test run.
    """
    tree = ast.parse(LLM_SRC)
    accepted = {
        a.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_record"
        for a in node.args.kwonlyargs
    }
    assert accepted, "_record not found — did it move?"

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_record"
    ]
    assert calls, "no _record call sites found — did they move?"

    for call in calls:
        passed = {kw.arg for kw in call.keywords if kw.arg}
        assert not (passed - accepted), (
            f"llm.py:{call.lineno} passes {sorted(passed - accepted)} which _record does not "
            f"accept; this raises TypeError instead of recording usage"
        )
        assert not any(kw.arg is None for kw in call.keywords), (
            f"llm.py:{call.lineno} uses **kwargs, which defeats this check"
        )


def test_record_tolerates_a_dead_database(monkeypatch):
    """A failing usage write is logged, not raised: cost accounting cannot fail a turn."""
    async def boom(_row):
        raise RuntimeError("database is down")

    monkeypatch.setattr(llm, "_write_usage", boom)

    async def go():
        llm._record(feature="test", client_id=None, integration_id=None, model="m",
                    message=None, latency_ms=1, ok=True, actor="tenant:x", job_id=None)
        # The write is a task; let it run and confirm it swallowed its own failure.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(go())


def test_record_row_matches_the_insert():
    """The tuple `_record` builds and the INSERT `_write_usage` runs must agree in width.

    They are written metres apart in the file and a column added to one but not the other
    fails only at runtime, inside a fire-and-forget task, where the failure is a log line
    nobody reads.
    """
    src = inspect.getsource(llm._write_usage)
    columns = src.split("INSERT INTO llm_usage (", 1)[1].split(")", 1)[0]
    n_columns = len([c for c in columns.replace("\n", " ").split(",") if c.strip()])
    placeholders = src.split("VALUES (", 1)[1].split(")", 1)[0]
    n_placeholders = len([p for p in placeholders.split(",") if p.strip()])
    assert n_columns == n_placeholders, f"{n_columns} columns vs {n_placeholders} placeholders"

    # And the row `_record` assembles has to be that wide too.
    record_src = inspect.getsource(llm._record)
    row_body = record_src.split("row = (", 1)[1].split("\n    )", 1)[0]
    n_fields = len([
        line for line in row_body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ])
    assert n_fields == n_columns, f"_record builds {n_fields} fields for {n_columns} columns"


# --------------------------------------------------------------------------- #
# The tenant overlay: llm.py is the ONLY place a tenant's AI config is applied.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("row, expect", [
    (None,                                          ("default-key", "default-model", None, False)),
    ({},                                            ("default-key", "default-model", None, False)),
    # A row that exists but is switched off changes nothing — that is the whole point of
    # `enabled`: an operator can stage a tenant's settings before cutting them over.
    ({"enabled": False, "model": "other", "api_key": "theirs"},
                                                    ("default-key", "default-model", None, False)),
    # Model only, still on our key — the common request.
    ({"enabled": True, "model": "claude-cheap"},    ("default-key", "claude-cheap", None, False)),
    # Their key only: byo flips, so the usage report can keep their spend out of our costs.
    ({"enabled": True, "api_key": "theirs"},        ("theirs", "default-model", None, True)),
    # Blank strings are not overrides.
    ({"enabled": True, "model": "", "api_key": ""}, ("default-key", "default-model", None, False)),
    ({"enabled": True, "base_url": "https://gw.example/v1"},
                                     ("default-key", "default-model", "https://gw.example/v1", False)),
])
def test_overlay(monkeypatch, row, expect):
    async def fake_get_config(_cid):
        return row

    monkeypatch.setattr(ai_config, "get_config", fake_get_config)
    ai_config.forget()
    got = asyncio.run(ai_config.overlay("c1", "default-key", "default-model"))
    assert tuple(got) == expect


def test_overlay_survives_a_broken_config_lookup(monkeypatch):
    """A tenant whose config cannot be read runs on the default rather than losing the call."""
    async def boom(_cid):
        raise RuntimeError("no database")

    monkeypatch.setattr(ai_config, "get_config", boom)
    ai_config.forget()
    got = asyncio.run(ai_config.overlay("c1", "default-key", "default-model"))
    assert tuple(got) == ("default-key", "default-model", None, False)


def test_overlay_is_a_no_op_without_a_tenant(monkeypatch):
    """Anonymous and operator-scoped work has no tenant row to apply, and must not look."""
    async def fail(_cid):
        raise AssertionError("should not query for a null client_id")

    monkeypatch.setattr(ai_config, "get_config", fail)
    got = asyncio.run(ai_config.overlay(None, "default-key", "default-model"))
    assert tuple(got) == ("default-key", "default-model", None, False)


def test_both_entry_points_apply_the_overlay():
    """`call_tool` and `stream_text` are the only ways into Anthropic, so both must overlay.

    A new entry point added without this line would silently ignore every tenant's
    configuration while appearing to work perfectly.
    """
    tree = ast.parse(LLM_SRC)
    for name in ("call_tool", "stream_text"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
        body = ast.dump(fn)
        assert "'overlay'" in body, f"{name} does not apply the tenant AI overlay"
