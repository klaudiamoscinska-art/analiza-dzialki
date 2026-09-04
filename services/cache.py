"""Generic SQLite pull-through cache for per-parcel enrichment data — added
2026-09-04 after investigating performance: most external services this app
queries return data that changes on timescales of weeks to years (see
'Plan Pamięci Podręcznej', the artifact report referenced in HANDOFF.md),
yet were being re-fetched on every single /api/analyze call.

Cache-aside pattern, deliberately the "lazy" kind rather than a background
poller: a background refresher would periodically re-fetch entries for
parcels nobody will ever look at again (the parcel key space is huge and
long-tailed — most parcels get checked once, not repeatedly), which would
waste requests against already-flaky government services instead of
reducing load on them. Refreshing only happens on demand, when a real
request for that exact (service, key) arrives after its TTL has passed.

No persistent disk configured on Render yet (deliberate v1 scope — see
HANDOFF.md and the cache-plan artifact, section 5): the SQLite file lives
in the container's ephemeral filesystem, so it resets on every deploy.
This still helps within a single deploy's uptime (repeat visits to the
same parcel, multiple people checking the same real-world parcel) — it
just doesn't accumulate value across deploys until/unless a persistent
disk is added later."""
import asyncio
import json
import sqlite3
import threading
import time
from typing import Any, Awaitable, Callable, Optional

from config import CACHE_DB_PATH, logger

_conn: Optional[sqlite3.Connection] = None
# _read_row/_write_row each run on a worker thread via asyncio.to_thread (see
# get_or_fetch's docstring), and main.py fans out up to 12 of these calls
# concurrently per /api/analyze — found live 2026-09-04 while testing the
# persistent-httpx-client optimization. Two related, but distinct, races
# showed up on a completely fresh cache.db (which is the NORMAL state after
# every deploy, since the cache resets — see the module docstring):
#   1. Several worker threads can all see `_conn is None` at once and race
#      to create it, so one can start reading before another's own CREATE
#      TABLE has committed ("no such table: cache_entries").
#   2. `check_same_thread=False` only lifts sqlite3's "same thread" guard —
#      it does NOT make a single Connection object safe for genuinely
#      concurrent use from multiple threads. Two threads calling
#      conn.execute()/conn.commit() at the same moment on the SAME
#      connection raced each other's implicit transactions ("cannot commit
#      - no transaction is active").
# A single, plain threading.Lock (not asyncio.Lock — this guards access
# from separate OS threads, not coroutines) held for the FULL duration of
# every connection creation, read, and write closes both races at once by
# making all sqlite3 access here strictly one-at-a-time. Each individual
# operation is a fast, already-fast SQLite statement, so serializing them
# behind a lock costs microseconds — nowhere near enough to undo the
# concurrency gains asyncio.gather/asyncio.to_thread give the REST of
# /api/analyze (the actual network calls, which run outside this lock).
# RLock (not a plain Lock) because _read_row/_write_row acquire it and then
# call _get_conn(), which acquires it again on the same thread — a plain
# Lock would deadlock there.
_conn_lock = threading.RLock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _conn_lock:
            if _conn is None:
                conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS cache_entries ("
                    "service TEXT NOT NULL, cache_key TEXT NOT NULL, "
                    "payload_json TEXT NOT NULL, fetched_at REAL NOT NULL, "
                    "PRIMARY KEY (service, cache_key))"
                )
                conn.commit()
                _conn = conn
    return _conn


def _reset_for_tests() -> None:
    """Closes the module-level connection so the next _get_conn() call
    reopens against whatever config.CACHE_DB_PATH currently points at —
    tests monkeypatch that to a fresh tempfile per test."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _read_row(service: str, key: str) -> Optional[tuple[str, float]]:
    with _conn_lock:
        conn = _get_conn()
        return conn.execute(
            "SELECT payload_json, fetched_at FROM cache_entries WHERE service=? AND cache_key=?",
            (service, key),
        ).fetchone()


def _write_row(service: str, key: str, payload_json: str, fetched_at: float) -> None:
    with _conn_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO cache_entries (service, cache_key, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (service, key, payload_json, fetched_at),
        )
        conn.commit()


async def get_or_fetch(
    service: str, key: str, ttl_seconds: float, fetch: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Cache-aside lookup keyed by (service, key) — key is meant to be a
    parcel's teryt_id, since that's the natural identity shared by every
    person who looks up the same real-world parcel. Returns the cached
    payload if fresher than ttl_seconds; otherwise awaits fetch(), and —
    ONLY if the result's 'status' is 'ok' — stores it before returning.
    A non-'ok' result (a government service erroring or timing out) is
    returned as-is but never cached, so a transient failure never freezes
    into a cached error for the whole TTL window.

    Every 'ok' result the caller gets back (hit or fresh fetch) carries
    'cached' (bool) and 'fetched_at' (unix timestamp) so the UI can show
    data age — see app.js's dataAgeNote(). This is not optional
    bookkeeping: showing stale-looking data as if it were fetched this
    second is the one thing this cache must never silently do.

    The actual sqlite3 read/write is synchronous (the stdlib driver has no
    async mode) and offloaded to a worker thread via asyncio.to_thread —
    fixed 2026-09-04, main.py fans out ~9 of these calls concurrently per
    /api/analyze via asyncio.gather, and a blocking conn.execute() run
    directly on the event loop thread would serialize otherwise-independent
    concurrent requests instead of letting them interleave during I/O
    waits, defeating the point of that concurrency."""
    row = await asyncio.to_thread(_read_row, service, key)
    now = time.time()
    if row is not None:
        payload_json, fetched_at = row
        if now - fetched_at < ttl_seconds:
            payload = json.loads(payload_json)
            payload["cached"] = True
            payload["fetched_at"] = fetched_at
            return payload

    result = await fetch()
    if isinstance(result, dict) and result.get("status") == "ok":
        try:
            await asyncio.to_thread(_write_row, service, key, json.dumps(result), now)
        except Exception:
            logger.warning("cache.get_or_fetch: zapis do cache'u nie powiódł się dla %s/%s", service, key, exc_info=True)
        result = dict(result)
        result["cached"] = False
        result["fetched_at"] = now
    return result
