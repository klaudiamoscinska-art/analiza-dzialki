"""Low-level HTTP helpers reused by several services: a generic
retry-on-transient-failure GET wrapper, the multi-mirror Overpass API
client, and a small WMS GetFeatureInfo request builder."""
import asyncio
import time
from typing import Any, Optional

import httpx

from config import OVERPASS_HEADERS, OVERPASS_URLS, TIMEOUT_OVERPASS, logger


def describe_exc(exc: BaseException) -> str:
    """str(exc) is EMPTY for several common httpx exceptions raised without
    an explicit message (confirmed live 2026-09-04 — a real
    'Usługa ... niedostępna: ' with nothing after the colon, reported by
    Klaudia for a genuine Overpass timeout) — httpx.ConnectTimeout(),
    httpx.ReadTimeout() etc. carry no args, so f'{exc}' silently loses the
    one piece of information ('what actually happened') these error
    messages exist to show. Falls back to the exception's class name,
    which is always non-empty and still says *something* useful
    (ConnectTimeout vs ReadTimeout vs ConnectError point at different
    problems even with no further detail)."""
    return str(exc) or type(exc).__name__


async def _overpass_query(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    """Races every configured mirror CONCURRENTLY instead of trying them
    one after another — fixed 2026-09-04, after Klaudia's nearest-road
    check kept failing even once TIMEOUT_OVERPASS was corrected to exceed
    the query's own [timeout:25] (see config.py). A sequential retry means
    every earlier mirror must first exhaust its own full timeout before the
    next is even attempted — if one mirror is slow, rate-limited, or
    silently blocked (a real risk for shared hosting IPs like Render's
    against free public Overpass instances), that alone was enough to make
    the whole call time out long before the second, healthy mirror ever
    got a chance. Racing them fixes both axes at once: worst-case latency
    drops to a single TIMEOUT_OVERPASS window (not one per mirror), and a
    blocked mirror no longer delays a healthy one — whichever answers
    first wins, the rest are cancelled."""
    async def _try(url: str) -> dict[str, Any]:
        resp = await client.post(url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=TIMEOUT_OVERPASS)
        resp.raise_for_status()
        # resp.json() is synchronous stdlib json.loads — offloaded to a
        # worker thread (added 2026-09-04) since a data-heavy gmina's
        # Overpass response can be large enough to parse for real CPU time,
        # which would otherwise block the event loop (and, confirmed live,
        # everything else this app is concurrently waiting on) for that
        # long. See config.py's MAX_CONCURRENT_SECTIONS comment.
        return await asyncio.to_thread(resp.json)

    pending = {asyncio.create_task(_try(url)) for url in OVERPASS_URLS}
    last_exc: Optional[Exception] = None
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                result = task.result()
            except Exception as exc:
                last_exc = exc
                continue
            for other in pending:
                other.cancel()
            return result
    raise last_exc or RuntimeError("Overpass niedostępny")


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, params: dict[str, Any], timeout: float,
    max_retries: int = 1, retry_delay_s: float = 2.0, follow_redirects: bool = False,
) -> httpx.Response:
    """Thin retry wrapper for connection/timeout failures — the failure mode
    confirmed (HANDOFF.md section 4) to be transient for individual powiat
    WFS servers, which are independently operated and occasionally slow or
    briefly unreachable. Also retries a 5xx HTTP status (fixed 2026-09-04 —
    an overloaded powiat server returning 503/500 used to fail immediately,
    skipping the one retry this helper exists to provide), but NOT a 4xx —
    that's a real, presumably permanent answer about this specific request
    (bad params, not found), not the kind of flakiness worth retrying. Also
    does NOT retry a response that came back HTTP 200 but with an error body
    (e.g. WFS ExceptionReport) — that's a real answer from a reachable
    server too, and the caller already handles those bodies itself.

    Logs elapsed wall-clock time per failed attempt (added 2026-09-04,
    diagnostic step 1 from HANDOFF.md's MPZP ConnectTimeout investigation)
    — a failure that lands in milliseconds (e.g. a connection actively
    refused/reset, or a DNS failure surfaced as a timeout) points at a
    different root cause than one that genuinely burns the whole `timeout`
    budget, and there was previously no way to tell the two apart from the
    logs alone."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        started = time.monotonic()
        try:
            resp = await client.get(url, params=params, timeout=timeout, follow_redirects=follow_redirects)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        elapsed_s = time.monotonic() - started
        logger.warning(
            "GET %s: próba %d/%d nieudana po %.1fs (%s: %s)",
            url, attempt + 1, max_retries + 1, elapsed_s, type(last_exc).__name__, last_exc,
        )
        if attempt < max_retries:
            await asyncio.sleep(retry_delay_s)
    assert last_exc is not None
    raise last_exc


async def wms_get_feature_info(
    client: httpx.AsyncClient, base_url: str, layers: str,
    x_2180: float, y_2180: float, half_extent_m: float = 12.0,
) -> httpx.Response:
    bbox = (
        f"{x_2180 - half_extent_m},{y_2180 - half_extent_m},"
        f"{x_2180 + half_extent_m},{y_2180 + half_extent_m}"
    )
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
        "LAYERS": layers, "QUERY_LAYERS": layers, "STYLES": "",
        "SRS": "EPSG:2180", "BBOX": bbox, "WIDTH": "101", "HEIGHT": "101",
        "X": "50", "Y": "50", "INFO_FORMAT": "text/html",
        "FEATURE_COUNT": "10", "FORMAT": "image/png",
    }
    return await client.get(base_url, params=params)
