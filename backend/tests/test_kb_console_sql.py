"""Static guard: every query in `kb_console.py` is tenant-scoped.

`services/kb_console.py` is now the single implementation behind BOTH KB consoles — the
operator's `/admin/kb/{tenant_id}/*` and the tenant's `/kb/*`. That is why it exists, and it is
also why a mistake in it is worth this file: a dropped `client_id` predicate here is not one
leaky endpoint, it is the same leak on two surfaces at once, one of which is reachable with a
partner's own `X-API-Key` at `/v1/kb/*`.

Multi-tenancy in this codebase has no other enforcement. There is no RLS, no per-tenant schema,
no ORM scope — only the fact that a human typed `AND client_id = $1` in every statement. That is
a convention, and conventions decay: the regression is one `WHERE document_id = $1` written in a
hurry, it passes review because the *function* takes a client_id, and it is invisible until a
tenant reads another tenant's knowledge base.

So this file reads `kb_console.py` as an AST — no database, no imports, milliseconds — and fails
on any `fetch/fetchrow/fetchval/execute/executemany` whose SQL does not mention `client_id`.
Modelled on `test_chat_store_sql.py`, with three deliberate differences forced by this module:

  * **Local SQL variables are resolved.** `list_chunks` builds its statement as
    `sql = "…"; sql += " LIMIT …"` before calling `conn.fetch(sql, *args)`. The chat_store guard
    only resolves module-level constants and would report that as *unreadable*; a guard that
    cries wolf gets its allowlist grown until it means nothing. Both halves are concatenated and
    checked together.
  * **The allowlist is per-STATEMENT, not per-function.** `params()` has two queries: a
    `pg_attribute` column-type lookup that touches no tenant data, and a `kb_chunks` count that
    must stay scoped. Exempting the whole function would silently exempt the second one too.
  * **Two extra invariants** the module's docstring promises and a reviewer cannot check at a
    glance: it must not import `fastapi` (it is called from `cq-worker`, which has no request),
    and it must never hold a pool connection across an embedding await (the encoder is CPU-bound
    and shared with live retrieval — parking a connection behind it starves every other tenant).

Unresolvable SQL is a FAILURE, not a pass. If a statement's text cannot be read statically, this
guard silently stops covering it while the file still looks protected — which is worse than not
having the guard at all. Keep SQL as a literal, an f-string whose literal parts carry the
predicate, or a named string built in the same function.
"""
import ast
from pathlib import Path

import pytest

CONSOLE = Path(__file__).resolve().parent.parent / "app" / "services" / "kb_console.py"

QUERY_METHODS = {"fetch", "fetchrow", "fetchval", "fetchmany", "execute", "executemany",
                 "cursor", "copy_records_to_table"}

# Statements that touch no tenant data at all and so cannot leak one.
NON_TENANT_PREFIXES = ("begin", "commit", "rollback", "set ", "select 1", "savepoint", "release")

# ---------------------------------------------------------------------------
# The allowlist. One entry = (function name, a fragment that must appear in the statement).
# Matching on a fragment rather than a function name is the point: an exemption must not widen
# to cover a *different* statement someone adds to the same function later.
#
# Adding an entry here should be as uncomfortable as it sounds — it is a security decision, and
# the comment is the record of who decided it and why.
# ---------------------------------------------------------------------------
TENANT_AGNOSTIC_STATEMENTS = {
    # kb_console.params(): reads the DECLARED TYPE of kb_chunks.embedding out of pg_catalog to
    # compare the pgvector column dimension against the configured embedding dimension. It
    # returns a type name, never a row of tenant data, and there is no client_id to scope it by
    # — a column has one type for every tenant. Documented inline in kb_console.py as the single
    # deliberate exception.
    ("params", "pg_attribute"),
}

# Awaits that must never happen while a pool connection is held. `retrieval`/`llm` are listed
# pre-emptively: neither is called inside an acquire today, and this is the cheap way to keep it
# that way.
BLOCKING_AWAIT_MODULES = {"kb_ingest", "embeddings", "retrieval", "llm"}


pytestmark = pytest.mark.skipif(
    not CONSOLE.exists(),
    reason="app/services/kb_console.py has not landed yet")


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _module() -> ast.Module:
    return ast.parse(CONSOLE.read_text(), filename=str(CONSOLE))


def _literal_sql(node: ast.AST | None, names: dict[str, str]) -> str | None:
    """Best-effort static text of a SQL expression, or None if it cannot be read."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        # f-string. The interpolations in this module are always a pre-validated fragment (an
        # optional filter, a parameter index, a column list constant) that can only NARROW the
        # query; the client_id predicate is always in the literal part, which is exactly the
        # property being checked. CPython merges adjacent implicit concatenation into this node,
        # so `"SELECT …" f"WHERE client_id = $1{extra}"` reads as one string here.
        parts = [v.value for v in node.values
                 if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_sql(node.left, names)
        right = _literal_sql(node.right, names)
        return left + right if left is not None and right is not None else None
    return None


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "..."` bindings (e.g. `_JOB_COLS`) so f-strings using them read."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        text = _literal_sql(node.value, out)
        if text is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = text
    return out


def _local_string_bindings(fn: ast.AST, consts: dict[str, str]) -> dict[str, str]:
    """`name = "..."` and `name += "..."` inside one function, concatenated.

    Exists for `list_chunks`, which assembles its statement across an assignment and a
    conditional `+=`. Both halves must be judged together: the base carries `client_id`, the
    appended fragment carries the LIMIT/OFFSET.
    """
    out: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            text = _literal_sql(node.value, {**consts, **out})
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = text if text is not None else None
        elif (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add)
                and isinstance(node.target, ast.Name)):
            frag = _literal_sql(node.value, {**consts, **out})
            base = out.get(node.target.id)
            if base is not None and frag is not None:
                out[node.target.id] = base + frag
    return {k: v for k, v in out.items() if v is not None}


def _is_query_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in QUERY_METHODS)


def _functions(tree: ast.Module) -> list[ast.AST]:
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _query_calls() -> list[tuple[str, int, str | None]]:
    """-> [(function name, lineno, sql or None)] for every asyncpg query call in the module."""
    tree = _module()
    consts = _module_string_constants(tree)
    found = []
    for fn in _functions(tree):
        scope = {**consts, **_local_string_bindings(fn, consts)}
        for node in ast.walk(fn):
            if _is_query_call(node):
                arg = node.args[0] if node.args else None
                found.append((fn.name, node.lineno, _literal_sql(arg, scope)))
    return found


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
def test_the_guard_can_see_every_query_in_the_module():
    """A guard that silently matches nothing goes green forever after a refactor.

    Two failure modes are pinned: no queries found at all (the module was renamed or its SQL
    moved), and queries found by a module-wide scan that the per-function walk missed (someone
    nested a helper function, whose local scope would not be resolved).
    """
    attributed = _query_calls()
    assert attributed, f"no asyncpg query calls found in {CONSOLE.name} — has the guard gone blind?"

    everywhere = [n for n in ast.walk(_module()) if _is_query_call(n)]
    assert len(everywhere) == len(attributed), (
        f"{len(everywhere)} query calls exist in {CONSOLE.name} but only {len(attributed)} were "
        "attributed to a top-level function. A nested helper is not covered by this guard — "
        "move its SQL to a module-level function.")


def test_every_query_is_client_scoped():
    offenders = []
    for func, lineno, sql in _query_calls():
        if sql is None:
            offenders.append(
                f"{CONSOLE.name}:{lineno} in {func}(): SQL is not statically readable. Keep it as "
                f"a literal, an f-string whose literal part carries the predicate, or a named "
                f"string built in the same function, so this guard can inspect it.")
            continue
        stripped = " ".join(sql.split()).lower()
        if stripped.startswith(NON_TENANT_PREFIXES):
            continue
        if "client_id" in stripped:
            continue
        if any(func == fn and frag.lower() in stripped
               for fn, frag in TENANT_AGNOSTIC_STATEMENTS):
            continue
        offenders.append(
            f"{CONSOLE.name}:{lineno} in {func}(): SQL has no client_id — {stripped[:140]}")
    assert not offenders, (
        "Tenant-scoping regression in kb_console.py. Every statement must filter (or insert) "
        "client_id: this module is the shared implementation behind BOTH the operator console "
        "and the tenant console (which is also mounted at /v1/kb for partner API keys), so one "
        "unscoped statement leaks on two surfaces at once. If a statement genuinely touches no "
        "tenant data, add it to TENANT_AGNOSTIC_STATEMENTS with a comment saying why:\n  "
        + "\n  ".join(offenders))


def test_allowlisted_statements_still_exist():
    """Keeps the allowlist honest.

    A stale exemption is worse than no exemption: it survives the rename or rewrite of the thing
    it was granted for and then quietly covers whatever ends up matching next.
    """
    calls = _query_calls()
    stale = []
    for fn, frag in TENANT_AGNOSTIC_STATEMENTS:
        hit = any(func == fn and sql and frag.lower() in " ".join(sql.split()).lower()
                  for func, _, sql in calls)
        if not hit:
            stale.append(f"{fn}() / {frag!r}")
    assert not stale, (
        "TENANT_AGNOSTIC_STATEMENTS exempts statements that no longer exist in kb_console.py — "
        f"remove them rather than leaving a blank cheque: {stale}")


def test_public_functions_take_client_id_first():
    """`client_id` must be the FIRST positional argument of every async operation.

    Position, not merely presence: a keyword-only or later-positional tenant argument is easy to
    omit at a call site, and the failure mode is a query scoped to whatever was passed instead.
    First means a caller cannot invoke the operation without naming a tenant. Sync helpers
    (`check_visibility`) are not tenant-scoped and are excluded by construction.
    """
    offenders = []
    for node in _functions(_module()):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name.startswith("_"):
            continue
        args = node.args.posonlyargs + node.args.args
        first = args[0].arg if args else None
        if first != "client_id":
            offenders.append(f"{CONSOLE.name}:{node.lineno} {node.name}() first arg is {first!r}")
    assert not offenders, (
        "kb_console operations must take client_id as their first positional argument:\n  "
        + "\n  ".join(offenders))


def test_kb_console_does_not_import_fastapi():
    """The service raises `KBConsoleError`, never `HTTPException`.

    Not a style rule: `cq-worker` drains the queued full-KB re-embed by calling this module with
    no request in flight. An `HTTPException` raised there is an unhandled exception that kills a
    job, and importing fastapi is the first step towards raising one.
    """
    imported = set()
    for node in ast.walk(_module()):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "fastapi" not in imported, (
        "kb_console.py imports fastapi. It is called from cq-worker, which has no request — "
        "raise KBConsoleError(status, detail) and let the routers translate it.")


def test_no_pool_connection_is_held_across_an_embedding_await():
    """`async with pool().acquire()` must not wrap an await on the encoder or on ingestion.

    The api runs ONE uvicorn worker against a ten-connection pool, and embeddings are fp32
    BGE-M3 on a single CPU-bound TEI container shared with live retrieval. Parking a connection
    behind that encoder — for one document, let alone a bulk re-embed — starves every other
    request in the process. The module docstring promises this; without a check, the promise
    lasts exactly until the first "why is this two blocks instead of one" cleanup.
    """
    offenders = []
    for node in ast.walk(_module()):
        if not isinstance(node, ast.AsyncWith):
            continue
        holds_connection = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "acquire"
            for item in node.items)
        if not holds_connection:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Await) or not isinstance(inner.value, ast.Call):
                continue
            func = inner.value.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in BLOCKING_AWAIT_MODULES):
                offenders.append(
                    f"{CONSOLE.name}:{inner.lineno} awaits {func.value.id}.{func.attr}() while "
                    f"holding a pool connection (acquired at line {node.lineno})")
    assert not offenders, (
        "A pool connection is held across a CPU-bound encoder await:\n  " + "\n  ".join(offenders)
        + "\nRelease the connection first (see kb_console.update_document / bulk for the shape).")
