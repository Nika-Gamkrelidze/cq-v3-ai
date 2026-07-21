"""Static guard: every query in `chat_store.py` is tenant-scoped.

Multi-tenancy in this codebase is not enforced by the database — there is no RLS, no per-tenant
schema, no ORM scope. It is enforced by the fact that a human wrote `AND client_id = $1` in every
statement. That is a convention, and conventions decay: the regression is one `WHERE
conversation_id = $1` in a hurry, it passes review because the *function* takes a client_id, and
it is invisible until a tenant reads another tenant's conversation.

So this file reads `chat_store.py` as an AST — no database, no imports, milliseconds — and fails
on any `fetch/fetchrow/fetchval/execute/executemany` whose SQL does not mention `client_id`. It
is the cheapest test in the repo and it covers the single most dangerous class of change.

Two deliberate design points:

  * **Unresolvable SQL is a failure, not a pass.** If a statement's text cannot be read
    statically (built by string concatenation, or passed in as a parameter), this guard silently
    stops covering it — which is worse than not having the guard, because the file still looks
    protected. Keep SQL as an inline literal or a module-level constant.
  * **The allowlist is per-function and explicit.** `reap_stale_suggestions` is a maintenance
    sweep across all tenants by design; it is named here so that being unscoped is a documented
    decision rather than an omission. Adding a name to this list should be as uncomfortable as
    it sounds.
"""
import ast
from pathlib import Path

import pytest

STORE = Path(__file__).resolve().parent.parent / "app" / "services" / "chat_store.py"

# Functions whose SQL is intentionally NOT client-scoped. Each entry is a security decision.
UNSCOPED_BY_DESIGN = {
    # A cron-style sweep that expires abandoned generations across the whole table. Scoping it
    # per tenant would mean either N queries or a tenant list, and it reads/writes nothing but
    # `copilot_suggestions.state` — see the ADR's reaper note.
    "reap_stale_suggestions",
}

QUERY_METHODS = {"fetch", "fetchrow", "fetchval", "fetchmany", "execute", "executemany",
                 "cursor", "copy_records_to_table"}

# Statements that touch no tenant data at all and so cannot leak one. Matched case-insensitively
# against the start of the stripped SQL.
NON_TENANT_PREFIXES = ("begin", "commit", "rollback", "set ", "select 1", "savepoint", "release")


pytestmark = pytest.mark.skipif(
    not STORE.exists(),
    reason="app/services/chat_store.py has not landed yet (owned by another track)")


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _module() -> ast.Module:
    return ast.parse(STORE.read_text(), filename=str(STORE))


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "..."` bindings, so `conn.execute(_INSERT_SQL, ...)` is readable.

    This is the house style for long statements (see chat_credentials._RESOLVE_SQL), so without
    it the guard would be blind to exactly the biggest queries.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        text = _literal_sql(value, {})
        if text is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = text
    return out


def _literal_sql(node: ast.AST | None, consts: dict[str, str]) -> str | None:
    """Best-effort static text of a SQL expression, or None if it cannot be read."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        # f-string: the interpolations are usually a whitelisted fragment (limits.py builds its
        # guard clause this way). Read the literal parts and let the client_id check apply to
        # what is actually pinned down.
        parts = [v.value for v in node.values
                 if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_sql(node.left, consts)
        right = _literal_sql(node.right, consts)
        if left is not None and right is not None:
            return left + right
        return None
    return None


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    """Map every node to the name of the function that contains it."""
    owner: dict[ast.AST, str] = {}

    def walk(node, name):
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            owner[child] = child_name
            walk(child, child_name)

    walk(tree, "<module>")
    return owner


def _query_calls():
    """-> list of (func_name, lineno, sql_or_None) for every asyncpg query call."""
    tree = _module()
    consts = _module_string_constants(tree)
    owner = _enclosing_functions(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in QUERY_METHODS:
            continue
        sql_arg = node.args[0] if node.args else None
        found.append((owner.get(node, "<module>"), node.lineno, _literal_sql(sql_arg, consts)))
    return found


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
def test_chat_store_has_queries_at_all():
    """A guard that silently matches nothing is worse than no guard: it goes green forever if
    the module is renamed or refactored into another layer."""
    calls = _query_calls()
    assert calls, f"no asyncpg query calls found in {STORE.name} — has the guard gone blind?"


def test_every_query_is_client_scoped():
    offenders = []
    for func, lineno, sql in _query_calls():
        if func in UNSCOPED_BY_DESIGN:
            continue
        if sql is None:
            offenders.append(
                f"{STORE.name}:{lineno} in {func}(): SQL is not statically readable. Keep it as "
                f"an inline literal or a module-level constant so this guard can inspect it.")
            continue
        stripped = sql.strip().lower()
        if stripped.startswith(NON_TENANT_PREFIXES):
            continue
        if "client_id" not in stripped:
            offenders.append(
                f"{STORE.name}:{lineno} in {func}(): SQL has no client_id — "
                f"{' '.join(sql.split())[:120]}")
    assert not offenders, (
        "Tenant-scoping regression in chat_store.py. Every statement must filter (or insert) "
        "client_id; multi-tenancy in this codebase has no other enforcement:\n  "
        + "\n  ".join(offenders))


def test_public_functions_take_client_id_first():
    """The contract is `client_id` as the FIRST positional argument of every public function.

    Position matters, not just presence: a keyword-only or later-positional client_id is easy to
    forget at a call site, and the failure mode is a query scoped to whatever happened to be
    passed. Making it first means a caller cannot invoke the function without naming a tenant.
    """
    tree = _module()
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") or node.name in UNSCOPED_BY_DESIGN:
            continue
        args = node.args.posonlyargs + node.args.args
        first = args[0].arg if args else None
        if first != "client_id":
            offenders.append(f"{STORE.name}:{node.lineno} {node.name}() first arg is {first!r}")
    assert not offenders, (
        "chat_store public functions must take client_id as their first positional argument:\n  "
        + "\n  ".join(offenders))


def test_unscoped_allowlist_only_contains_functions_that_exist():
    """Keeps the allowlist honest: a renamed function must not leave a stale exemption behind
    that silently covers something else later."""
    tree = _module()
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    stale = UNSCOPED_BY_DESIGN - names
    assert not stale, f"allowlisted but no longer defined in chat_store.py: {sorted(stale)}"
