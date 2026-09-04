"""Low-level HTTP helpers reused by several services: a generic
retry-on-transient-failure GET wrapper, the multi-mirror Overpass API
client, and a small WMS GetFeatureInfo request builder."""
import asyncio
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
    last_exc: Optional[Exception] = None
    for url in OVERPASS_URLS:
        try:
            resp = await client.post(
                url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=TIMEOUT_OVERPASS
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            continue
    raise last_exc or RuntimeError("Overpass niedostępny")


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, params: dict[str, Any], timeout: float,
    max_retries: int = 1, retry_delay_s: float = 2.0,
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
    server too, and the caller already handles those bodies itself."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        logger.warning(
            "GET %s: próba %d/%d nieudana (%s: %s)",
            url, attempt + 1, max_retries + 1, type(last_exc).__name__, last_exc,
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
