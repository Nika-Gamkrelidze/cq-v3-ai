"""Guards on `crm_store.py`: every query is tenant-scoped, and every writer of `is_primary`
serialises per tenant without ever holding two pool connections at once.

The same idea as `test_chat_store_sql.py`, for the same reason: isolation here is a
`client_id = $n` someone typed, and the regression is one query without it. The file is read
as an AST — no database — and any `fetch*/execute` whose SQL does not mention `client_id`
fails, as does SQL that cannot be read statically (that would silently escape the guard).

The locking tests below are the other half: both of those invariants are liveness and race
properties that no sequential test, and no amount of clicking, can reach.
"""
import ast
from pathlib import Path

from app.services import crm_presets, crm_store, settings_store

STORE = Path(__file__).resolve().parent.parent / "app" / "services" / "crm_store.py"
QUERY_METHODS = {"fetch", "fetchrow", "fetchval", "execute", "executemany"}
# Statements that read or write no tenant table.
NON_TENANT_PREFIXES = ("select pg_advisory_xact_lock",)
# The three entry points that move `is_primary`; `uq_crm_forms_primary` allows one active
# primary row per tenant, so a lock any one of them skips is a lock that buys nothing.
PRIMARY_WRITERS = ("save_form", "set_primary", "delete_form")


def _consts(tree: ast.Module) -> dict[str, str]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _text(node, consts) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _queries():
    tree = ast.parse(STORE.read_text(encoding="utf-8"))
    consts = _consts(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in QUERY_METHODS and node.args):
            yield node.lineno, _text(node.args[0], consts)


def test_there_are_queries_to_check():
    assert len(list(_queries())) >= 15


def test_every_query_is_tenant_scoped():
    bad = []
    for line, sql in _queries():
        if sql is None:
            bad.append(f"line {line}: SQL is not a readable literal")
        elif sql.strip().lower().startswith(NON_TENANT_PREFIXES):
            continue
        elif "client_id" not in sql:
            bad.append(f"line {line}: no client_id in {sql.strip()[:60]!r}")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------- #
# Serialising the writers of `is_primary`
# --------------------------------------------------------------------------- #
def _functions() -> dict:
    tree = ast.parse(STORE.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}, _consts(tree)


def _sql_of(fn, consts):
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in QUERY_METHODS and node.args):
            yield (_text(node.args[0], consts) or "").lower()


def _calls(fn, name: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
               for n in ast.walk(fn))


def test_every_primary_writer_takes_the_tenant_lock():
    """`save_form` serialised itself and the other two did not, which left the serialisation
    worth nothing: a save retires the active row and inserts a replacement, so an unlocked
    `set_primary` re-checks a row that is already inactive, never sees the new one, and lands
    a second active primary — `uq_crm_forms_primary`, an unhandled 500 — or, from the delete
    side, promotes a row the saver just retired and leaves the tenant with no primary at all."""
    fns, consts = _functions()
    missing = [name for name in PRIMARY_WRITERS
               if not (_calls(fns[name], "_lock_tenant")
                       or any("pg_advisory_xact_lock" in s for s in _sql_of(fns[name], consts)))]
    assert not missing, f"no per-tenant lock in: {', '.join(missing)}"


def test_only_the_known_writers_move_is_primary():
    """`_insert` writes the column too, but it never acquires a connection of its own — it
    writes on the caller's, inside the caller's lock. Any other function that learns to move
    `is_primary` has to join PRIMARY_WRITERS and take the lock with them."""
    fns, consts = _functions()
    allowed = set(PRIMARY_WRITERS) | {"_insert"}
    rogue = [name for name, fn in fns.items()
             if name not in allowed
             and any("is_primary" in s and ("update " in s or "insert " in s)
                     for s in _sql_of(fn, consts))]
    assert not rogue, f"these move is_primary without the tenant lock: {', '.join(rogue)}"
    assert not _calls(fns["_insert"], "pool"), "_insert must write on the caller's connection"


# --------------------------------------------------------------------------- #
# One connection at a time
# --------------------------------------------------------------------------- #
class _PoolExhausted(RuntimeError):
    """What asyncpg would do instead is wait forever: `acquire()` is called with no timeout."""


class _Nothing:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _Conn:
    """Just enough of an asyncpg connection for a first save by a tenant with no forms."""

    def __init__(self):
        self.sql = []

    def transaction(self):
        return _Nothing()

    async def execute(self, sql, *args):
        self.sql.append(sql)

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "app_settings" in sql:
            return None          # no default form was ever saved — the built-in one is used
        if "MAX(version)" in sql:
            return 1
        return False             # EXISTS(… crm_forms …): the tenant owns nothing yet

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return None


class _OnePool:
    """A pool of exactly one connection, which is `db_pool_max` once every other connection
    is held by a save queued on this tenant's advisory lock. Raising on the second acquire
    turns a deadlock — which a test can only observe as a hang — into a failure."""

    def __init__(self):
        self.conn, self.held = _Conn(), 0

    def acquire(self):
        return _Acquire(self)


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        if self.pool.held:
            raise _PoolExhausted("a second pool connection was asked for while one was held")
        self.pool.held += 1
        return self.pool.conn

    async def __aexit__(self, *exc):
        self.pool.held -= 1
        return False


async def test_save_form_reads_the_default_before_it_takes_a_connection(monkeypatch):
    """The copy-the-default path used to call `get_default_form()` from inside the advisory
    lock, and that read takes a pool connection of its own. The savers queued on the lock are
    holding the connections it waits for and cannot release them until the lock holder
    commits, so `db_pool_max` concurrent first saves for one tenant starve the whole process
    — every endpoint for every tenant, until the container restarts."""
    p = _OnePool()
    monkeypatch.setattr(crm_store, "pool", lambda: p)
    monkeypatch.setattr(settings_store, "pool", lambda: p)

    await crm_store.save_form("11111111-1111-1111-1111-111111111111", "sales",
                              crm_presets.BUILTIN_DEFAULT, "tester")

    # The branch under test really ran: the inherited default was copied in beside the form.
    assert sum("INSERT INTO crm_forms" in s for s in p.conn.sql) == 2
    assert any("pg_advisory_xact_lock" in s for s in p.conn.sql)
