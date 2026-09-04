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
import time
from typing import Any, Awaitable, Callable, Optional

from config import CACHE_DB_PATH, logger

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS cache_entries ("
            "service TEXT NOT NULL, cache_key TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, fetched_at REAL NOT NULL, "
            "PRIMARY KEY (service, cache_key))"
        )
        _conn.commit()
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
    conn = _get_conn()
    return conn.execute(
        "SELECT payload_json, fetched_at FROM cache_entries WHERE service=? AND cache_key=?",
        (service, key),
    ).fetchone()


def _write_row(service: str, key: str, payload_json: str, fetched_at: float) -> None:
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
