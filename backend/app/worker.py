"""cq-worker — the periodic, out-of-band half of the chat feature.

Why a second process at all: the api process serves one uvicorn worker, and everything it does
sits directly in front of an operator waiting on a suggestion. Anything periodic or long-running
that runs *there* competes for the same event loop and the same asyncpg pool as the turn that a
human is watching a spinner for. So it lives here instead: same image, different CMD.

P1 duty is the stale-suggestion reaper. `copilot_suggestions` rows go `running` the moment a
precompute starts, and every push to `main` auto-deploys (webhook -> `docker compose up -d
--build`), which kills the api mid-precompute. Without a sweep those rows stay `running` forever
and the operator's poll returns "pending" for the rest of time — the failure is silent, permanent
and user-visible, which is exactly the kind that needs a janitor rather than a code fix.

P2 hangs KB curation off this same process, as two more duties:
  * `curation_pass` — the nightly harvest -> cluster -> propose run, staggered per tenant across
    02:00–05:00 UTC so the single-CPU TEI encoder is not hit by every tenant at once.
  * `apply_accepted_proposals` — applies proposals a HUMAN accepted. Nothing auto-applies at any
    confidence; see the comment above CURATION_APPLY_INTERVAL_S for why.

Deliberately NOT done here:
  * **No migrations.** `run_startup_migrations()` stays exclusive to the api process. Two
    processes booting simultaneously against the same database would race on CREATE/ALTER
    (and on the embedding-dimension reconciliation, which is not idempotent under concurrency).
  * **No `analysis.sweep_stuck_jobs()`.** Same reason: it is a startup-only, once-per-boot
    action owned by the api. If the worker also ran it, a worker restart would fail analysis
    jobs the api is actively processing.

Boot order matters: on a brand-new database the worker and the api start together, and the
worker's first sweep can beat the api's first-ever migration pass — which is where P1
verification saw `UndefinedTableError: relation "copilot_suggestions" does not exist`. It
recovered on the next interval, but that is noise in the logs that would mask a real error
later, so the worker now waits for its tables to exist before the first duty runs (see
`_await_schema`). It still does NOT run the migrations itself — see below.

Shutdown is cooperative: SIGTERM/SIGINT set a stop flag, the current sweep is allowed to finish,
then the pool closes. Compose gives it a 60 s grace period; we bail out at 45 s so the process
exits on its own terms rather than being SIGKILLed mid-statement.
"""
import asyncio
import datetime as dt
import hashlib
import logging
import signal
import time

from . import db
from .config import settings
from .services import chat_store
from .services.curation import apply as curation_apply
from .services.curation import runner as curation_runner

log = logging.getLogger("cq")

# How often each duty runs. The reaper is cheap (one UPDATE over a small, indexed set), and
# 30 s is short enough that an operator polling a suggestion orphaned by a deploy sees it fail
# within one human attention span instead of hanging.
REAP_INTERVAL_S = 30.0
# A suggestion still `running` after this long did not survive its process. The precompute
# budget is single-digit seconds (llm.COPILOT times out at 6 s), so 120 s cannot race a live one.
REAP_STALE_AFTER_S = 120

# ---- P2: KB curation ---------------------------------------------------------
# The nightly pass is scheduled per tenant inside a 02:00–05:00 UTC window rather than "all
# tenants at 02:00". Embeddings are fp32 BGE-M3 on a single-CPU TEI container with no CPU
# reservation, shared with the live copilot read path — every tenant starting at the same
# instant is the one way this background job becomes a foreground latency incident.
CURATION_WINDOW_START_S = 2 * 3600      # 02:00 UTC
CURATION_WINDOW_S = 3 * 3600           # ...through 05:00 UTC
# How often we check whether any tenant is due. Cheap: one SELECT over `clients`. Five minutes,
# not one: a tick is allowed to run every due tenant, so a busy night legitimately exceeds a
# 60 s interval and `_run_duty`'s overrun warning would fire nightly and mean nothing. At 300 s
# the warning recovers its meaning (a pass genuinely taking >5 min is worth seeing) while 5-minute
# granularity over a 3-hour window costs nothing.
CURATION_TICK_S = 300.0
# Manual `POST /admin/curation/run` requests picked up per tick. Bounded because each one is a
# full mining run and they are executed sequentially (one CPU-bound encoder, see runner.run_all):
# an operator queueing a month of days must not turn one tick into an all-night job.
CURATION_REQUESTED_BATCH = 5

# The accepted-proposal applier. 5 s is a human-perceptible "it happened" after clicking Accept
# in the review UI, without being a busy-wait: the predicate is served by
# idx_curation_proposals_queue and matches nothing the vast majority of the time.
CURATION_APPLY_INTERVAL_S = 5.0
CURATION_APPLY_BATCH = 10

# NOTHING AUTO-APPLIES, AT ANY CONFIDENCE. The applier below only ever picks up proposals a
# human already moved to state='accepted'. This is a product decision, not a missing feature:
# an unreviewed KB write feeds straight into the fact-check and rubric-scoring paths that
# regulated tenants (banks, insurers, clinics) make decisions on, so a model's own confidence
# score is not an acceptable substitute for a reviewer. The product owner asked for
# accept / decline / accept-with-edits, and all three are human actions.

# Shutdown budget. Must stay BELOW the compose `stop_grace_period`, or the "graceful" path is
# never actually taken.
SHUTDOWN_TIMEOUT_S = 45.0

# The tables every duty in this process reads. They are created by the API's startup
# migrations, never here, so on a fresh volume they appear some seconds after the worker's
# pool connects.
REQUIRED_TABLES = ("copilot_suggestions", "curation_runs", "curation_proposals")
# Bounded wait for those tables. Backs off 1 -> 2 -> 4 -> 8 s, capped, and gives up after this
# long: if the api is genuinely broken the worker should start anyway and let each duty fail
# loudly on its own interval, rather than sit silent forever pretending to be healthy.
SCHEMA_WAIT_TIMEOUT_S = 120.0
SCHEMA_POLL_MAX_S = 8.0

_stop = asyncio.Event()


async def _await_schema() -> bool:
    """Block until the api's migrations have created REQUIRED_TABLES (or we give up).

    WHY this exists: on a brand-new database the worker's first sweep raced the api's
    first-ever migration pass and logged `UndefinedTableError: relation
    "copilot_suggestions" does not exist`. It self-healed on the next interval, so the cost is
    purely a scary log line on every fresh deploy — and that is exactly the kind of expected
    noise that trains you to ignore the log where a real error will later appear.

    Deliberately a POLL, not a migration: running the DDL here would race the api on boot (see
    the module docstring). `to_regclass` returns NULL instead of raising for a missing relation,
    so this is a plain query with no exception handling in the happy path.
    """
    started = time.monotonic()
    delay = 1.0
    logged = False
    while not _stop.is_set():
        try:
            async with db.pool().acquire() as conn:
                missing = [t for t in REQUIRED_TABLES
                           if await conn.fetchval("SELECT to_regclass($1)", t) is None]
        except Exception as exc:  # noqa: BLE001 — db not up yet is the normal case here
            missing = list(REQUIRED_TABLES)
            log.debug("schema probe failed (%s); retrying", exc)
        if not missing:
            if logged:
                log.info("worker: schema ready after %.1fs", time.monotonic() - started)
            return True
        if not logged:
            # Once, at info: enough to explain a slow start, not enough to spam a boot loop.
            log.info("worker: waiting for api migrations to create %s", ", ".join(missing))
            logged = True
        if time.monotonic() - started > SCHEMA_WAIT_TIMEOUT_S:
            # Proceed anyway. Each duty catches its own exceptions and retries on its interval,
            # so a still-missing table becomes a loud, recurring error — which is the right
            # outcome if the api never came up.
            log.warning("worker: %s still missing after %.0fs; starting duties anyway",
                        ", ".join(missing), SCHEMA_WAIT_TIMEOUT_S)
            return False
        try:
            await asyncio.wait_for(_stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, SCHEMA_POLL_MAX_S)
    return False


async def _reap_stale_suggestions() -> None:
    n = await chat_store.reap_stale_suggestions(REAP_STALE_AFTER_S)
    if n:
        # Loud on purpose: a non-zero count means suggestions died mid-flight, which is normally
        # a deploy but can also be a crash. It is the signal you want in `docker logs cq-worker`.
        log.warning("reaper: failed %s stale suggestion(s) older than %ss", n, REAP_STALE_AFTER_S)


def _curation_offset_s(client_id: str) -> int:
    """Deterministic per-tenant start offset inside the 02:00–05:00 UTC window.

    md5, NOT the builtin `hash()`: str hashing is salted per process (PYTHONHASHSEED), so with
    `hash()` every tenant's slot would move on every deploy — and this stack redeploys on every
    push to main. A stable offset means a tenant's run lands at the same time each night, which
    is what makes "did last night's pass run?" answerable.
    """
    return int(hashlib.md5(client_id.encode()).hexdigest()[:8], 16) % CURATION_WINDOW_S


async def _requested_runs() -> None:
    """Execute the tenant-days an operator asked for via `POST /admin/curation/run`.

    WHY this is separate from the nightly tick: the tick mines exactly *yesterday*, only inside
    the 02:00–05:00 window, and only for tenants with no `done` row for that day. A manual
    request satisfies none of those — it names its own day, it is made at 14:00, and re-running a
    day that is already `done` is the whole point of asking. Without this the route wrote a
    `requested` row that nothing ever read: a 202, a button in kb-admin, and no run, ever.

    The row is the queue. `run_for_client` claims it (the `requested` row carries a backdated
    heartbeat precisely so it satisfies the runner's verbatim claim statement) and leaves it
    `done` or `error`, so a request is consumed exactly once and nothing is left to expire.
    """
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT client_id, day FROM curation_runs WHERE state = 'requested' "
            "ORDER BY day, client_id LIMIT $1",
            CURATION_REQUESTED_BATCH)
    for row in rows:
        if _stop.is_set():
            log.info("curation: stop requested, deferring remaining manual runs")
            return
        client_id, day = str(row["client_id"]), row["day"]
        log.info("curation: running requested tenant-day client=%s day=%s", client_id, day)
        result = await curation_runner.run_for_client(client_id, day)
        if result.get("skipped") == "disabled":
            # Curation is off for this tenant, so nothing will ever claim the row. Retire it
            # rather than re-reading it every tick until someone notices.
            async with db.pool().acquire() as conn:
                await conn.execute(
                    "UPDATE curation_runs SET state = 'error', finished_at = now(), "
                    "error = 'curation is disabled for this tenant' "
                    "WHERE client_id = $1 AND day = $2 AND state = 'requested'",
                    row["client_id"], day)
        elif result.get("proposals"):
            log.info("curation: client=%s produced %s proposal(s)", client_id, result["proposals"])


async def _curation_pass() -> None:
    """Manual requests first, then the previous UTC day for every tenant whose slot has arrived.

    Mines *yesterday* rather than today-so-far: a partial day would be re-mined the next night,
    and while the run ledger makes that harmless it also makes it pointless.

    Idempotency is the run ledger's job, not this tick's — `run_for_client` claims (client, day)
    and returns a skip if it is already done or owned. The pre-filter here exists only to keep a
    once-a-minute tick from issuing one upsert per tenant per minute, all night.
    """
    await _requested_runs()

    now = dt.datetime.now(dt.timezone.utc)
    since_midnight = now.hour * 3600 + now.minute * 60 + now.second
    if since_midnight < CURATION_WINDOW_START_S:
        return
    day = (now - dt.timedelta(days=1)).date()

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id FROM clients c
             WHERE c.is_active
               AND NOT EXISTS (SELECT 1 FROM curation_runs r
                                WHERE r.client_id = c.id AND r.day = $1 AND r.state = 'done')
             ORDER BY c.id
            """,
            day)

    for row in rows:
        if _stop.is_set():
            log.info("curation: stop requested, deferring remaining tenants to the next tick")
            return
        client_id = str(row["id"])
        due_at = CURATION_WINDOW_START_S + _curation_offset_s(client_id)
        if since_midnight < due_at:
            continue
        # Sequential and awaited: see runner.run_all's docstring — one CPU-bound encoder.
        result = await curation_runner.run_for_client(client_id, day)
        if result.get("proposals"):
            log.info("curation: client=%s produced %s proposal(s)", client_id, result["proposals"])


async def _apply_accepted_proposals() -> None:
    """Apply proposals a human accepted. Never selects anything a human has not decided.

    Claiming lives in `apply.apply_accepted` (a conditional UPDATE on `applied_at`), so this
    poll can be a plain SELECT and can safely overlap with a manual admin-triggered apply.
    """
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM curation_proposals
             WHERE state = 'accepted'
               AND applied_document_id IS NULL
               AND apply_error IS NULL
             ORDER BY reviewed_at NULLS LAST
             LIMIT $1
            """,
            CURATION_APPLY_BATCH)
    for row in rows:
        # Checked between items, never mid-item: an apply that is interrupted after the
        # kb_documents INSERT but before ingest would leave a half-built document.
        if _stop.is_set():
            log.info("curation apply: stop requested, remaining proposals deferred")
            return
        await curation_apply.apply_accepted(str(row["id"]))


async def _run_duty(name: str, interval_s: float, fn) -> None:
    """Run one duty forever on a fixed interval until the stop flag is set.

    The stop check sits between iterations and the sleep is an interruptible `Event.wait()`, so
    SIGTERM neither aborts work in progress nor makes shutdown wait out a full interval.
    One iteration's failure never kills the loop — a worker that dies on a transient database
    blip stops reaping, which is the very failure it exists to prevent.
    """
    while not _stop.is_set():
        started = time.monotonic()
        try:
            await fn()
        except Exception:  # noqa: BLE001 — a duty must never take the process down
            log.exception("duty %s failed; continuing", name)
        elapsed = time.monotonic() - started
        if elapsed > interval_s:
            log.warning("duty %s took %.1fs, longer than its %.0fs interval", name, elapsed, interval_s)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=max(interval_s - elapsed, 0.0))
        except asyncio.TimeoutError:
            pass
    log.info("duty %s stopped", name)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop.set)
        except NotImplementedError:  # pragma: no cover — non-POSIX
            signal.signal(sig, lambda *_: _stop.set())


async def main() -> None:
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop)

    await db.connect()
    log.info(
        "cq-worker started | duties=%s | reap_interval=%ss stale_after=%ss | "
        "curation_window=%02d:00-%02d:00 UTC tick=%ss apply_poll=%ss auto_apply=never | "
        "db_pool=%s-%s | migrations=api-only sweep_stuck_jobs=api-only",
        "reap_stale_suggestions,curation_pass,apply_accepted_proposals",
        REAP_INTERVAL_S, REAP_STALE_AFTER_S,
        CURATION_WINDOW_START_S // 3600, (CURATION_WINDOW_START_S + CURATION_WINDOW_S) // 3600,
        CURATION_TICK_S, CURATION_APPLY_INTERVAL_S,
        settings.db_pool_min, settings.db_pool_max,
    )

    # Before the first sweep, not between duties: every duty touches tables the api creates.
    await _await_schema()
    if _stop.is_set():
        await db.disconnect()
        log.info("cq-worker stopped before first sweep")
        return

    duties = [
        asyncio.create_task(_run_duty("reap_stale_suggestions", REAP_INTERVAL_S,
                                      _reap_stale_suggestions),
                            name="reap_stale_suggestions"),
        asyncio.create_task(_run_duty("curation_pass", CURATION_TICK_S, _curation_pass),
                            name="curation_pass"),
        asyncio.create_task(_run_duty("apply_accepted_proposals", CURATION_APPLY_INTERVAL_S,
                                      _apply_accepted_proposals),
                            name="apply_accepted_proposals"),
    ]
    try:
        await _stop.wait()
        log.info("cq-worker: stop requested, finishing in-flight duties")
        done, pending = await asyncio.wait(duties, timeout=SHUTDOWN_TIMEOUT_S)
        for task in pending:
            log.warning("duty %s did not finish within %.0fs; cancelling",
                        task.get_name(), SHUTDOWN_TIMEOUT_S)
            task.cancel()
    finally:
        await db.disconnect()
        log.info("cq-worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
