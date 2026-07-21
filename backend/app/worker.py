"""cq-worker — the periodic, out-of-band half of the chat feature.

Why a second process at all: the api process serves one uvicorn worker, and everything it does
sits directly in front of an operator waiting on a suggestion. Anything periodic or long-running
that runs *there* competes for the same event loop and the same asyncpg pool as the turn that a
human is watching a spinner for. So it lives here instead: same image, different CMD.

P1 duty is the stale-suggestion reaper. `copilot_suggestions` rows go `running` the moment a
precompute starts, and every push to `main` auto-deploys (webhook -> `docker compose up -d
--build`), which kills the api mid-precompute. Without a sweep those rows stay `running` forever
and the operator's poll returns "pending" for the rest of time — the failure is silent, permanent
and user-visible, which is exactly the kind that needs a janitor rather than a code fix. P2 hangs
KB curation off this same process.

Deliberately NOT done here:
  * **No migrations.** `run_startup_migrations()` stays exclusive to the api process. Two
    processes booting simultaneously against the same database would race on CREATE/ALTER
    (and on the embedding-dimension reconciliation, which is not idempotent under concurrency).
  * **No `analysis.sweep_stuck_jobs()`.** Same reason: it is a startup-only, once-per-boot
    action owned by the api. If the worker also ran it, a worker restart would fail analysis
    jobs the api is actively processing.

Shutdown is cooperative: SIGTERM/SIGINT set a stop flag, the current sweep is allowed to finish,
then the pool closes. Compose gives it a 60 s grace period; we bail out at 45 s so the process
exits on its own terms rather than being SIGKILLed mid-statement.
"""
import asyncio
import logging
import signal
import time

from . import db
from .config import settings
from .services import chat_store

log = logging.getLogger("cq")

# How often each duty runs. The reaper is cheap (one UPDATE over a small, indexed set), and
# 30 s is short enough that an operator polling a suggestion orphaned by a deploy sees it fail
# within one human attention span instead of hanging.
REAP_INTERVAL_S = 30.0
# A suggestion still `running` after this long did not survive its process. The precompute
# budget is single-digit seconds (llm.COPILOT times out at 6 s), so 120 s cannot race a live one.
REAP_STALE_AFTER_S = 120

# Shutdown budget. Must stay BELOW the compose `stop_grace_period`, or the "graceful" path is
# never actually taken.
SHUTDOWN_TIMEOUT_S = 45.0

_stop = asyncio.Event()


async def _reap_stale_suggestions() -> None:
    n = await chat_store.reap_stale_suggestions(REAP_STALE_AFTER_S)
    if n:
        # Loud on purpose: a non-zero count means suggestions died mid-flight, which is normally
        # a deploy but can also be a crash. It is the signal you want in `docker logs cq-worker`.
        log.warning("reaper: failed %s stale suggestion(s) older than %ss", n, REAP_STALE_AFTER_S)


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
        "db_pool=%s-%s | migrations=api-only sweep_stuck_jobs=api-only",
        "reap_stale_suggestions", REAP_INTERVAL_S, REAP_STALE_AFTER_S,
        settings.db_pool_min, settings.db_pool_max,
    )

    duties = [
        asyncio.create_task(_run_duty("reap_stale_suggestions", REAP_INTERVAL_S,
                                      _reap_stale_suggestions),
                            name="reap_stale_suggestions"),
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
