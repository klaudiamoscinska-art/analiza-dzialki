"""Section 5 — Plany zagospodarowania: tries the new national APP
aggregator (Rejestr Urbanistyczny) first, falls back to the legacy KIMPZP
service. Both use a fast GetMap visual pre-check before the much less
reliable GetFeatureInfo call, since some gmina backends hang indefinitely
on GetFeatureInfo for undigitized plans."""
import asyncio
import io
from typing import Any, Optional

import httpx
from PIL import Image

from config import (
    KIAPP_LAYERS, KIAPP_URL, KIMPZP_LAYERS, KIMPZP_URL,
    TIMEOUT_MPZP_DETAIL, TIMEOUT_MPZP_PROBE, logger,
)
from geo_utils import _clean_feature_info_text, _feature_info_has_data, _parse_feature_info_table
from http_utils import wms_get_feature_info

async def _mpzp_has_plan_drawn(
    client: httpx.AsyncClient, url: str, layer: str, x_2180: float, y_2180: float, half_extent_m: float = 15.0
) -> bool:
    """GetMap (rendering) responds fast and reliably even for gminas with no
    digitized plan (confirmed live: 0 non-transparent pixels, ~2s). Use it as
    a cheap pre-check before attempting the much less reliable GetFeatureInfo
    call below."""
    bbox = f"{x_2180-half_extent_m},{y_2180-half_extent_m},{x_2180+half_extent_m},{y_2180+half_extent_m}"
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": layer, "STYLES": "", "SRS": "EPSG:2180", "BBOX": bbox,
        "WIDTH": "150", "HEIGHT": "150", "FORMAT": "image/png", "TRANSPARENT": "true",
    }
    resp = await client.get(url, params=params, follow_redirects=True, timeout=TIMEOUT_MPZP_PROBE)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    return any(px[3] > 10 for px in img.getdata())


async def _try_zoning_source(
    client: httpx.AsyncClient, url: str, layer: str, x_2180: float, y_2180: float, source_label: str
) -> Optional[dict[str, Any]]:
    """Returns None if this source has no plan here (so the caller can try
    the next source), or a result dict if it does (found, or a partial/error
    that should still be surfaced to the user rather than silently skipped)."""
    try:
        has_plan_visually = await _mpzp_has_plan_drawn(client, url, layer, x_2180, y_2180)
    except Exception as exc:
        logger.warning("_try_zoning_source(%s): probe GetMap nieudany", source_label, exc_info=True)
        return {"status": "error", "message": f"Usługa {source_label} niedostępna: {exc}"}

    if not has_plan_visually:
        return None

    try:
        resp = await asyncio.wait_for(
            wms_get_feature_info(client, url, layer, x_2180, y_2180, half_extent_m=15.0),
            timeout=TIMEOUT_MPZP_DETAIL,
        )
        table = _parse_feature_info_table(resp.text)
        text = _clean_feature_info_text(resp.text)
        has_plan = _feature_info_has_data(text)
        return {"status": "ok", "found": "yes" if has_plan else "no", "table": table, "source": source_label}
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return {
            "status": "partial", "found": "yes", "table": [], "source": source_label,
            "message": (
                f"Działka jest objęta planem (widoczny na mapie, {source_label}), ale "
                "serwer gminy nie zwrócił szczegółów w wyznaczonym czasie — "
                "spróbuj ponownie za chwilę."
            ),
        }
    except Exception as exc:
        return {
            "status": "partial", "found": "yes", "table": [], "source": source_label,
            "message": f"Działka jest objęta planem ({source_label}), ale nie udało się pobrać szczegółów: {exc}",
        }


async def get_zoning(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    """Tries the new national APP aggregator (KIAPP) first — richer, act-level
    metadata (nazwa planu, uchwała, data wejścia w życie, status) once gminas
    populate it — then falls back to the legacy KIMPZP zoning-symbol service
    if KIAPP has nothing here. Both use the same fast-GetMap-probe strategy
    (see _mpzp_has_plan_drawn) since KIMPZP's GetFeatureInfo has been
    confirmed live to hang indefinitely for gminas without their own backend."""
    result = await _try_zoning_source(client, KIAPP_URL, KIAPP_LAYERS, x_2180, y_2180, "Rejestr Urbanistyczny/APP")
    if result is not None:
        return result

    result = await _try_zoning_source(client, KIMPZP_URL, KIMPZP_LAYERS, x_2180, y_2180, "MPZP (KIMPZP)")
    if result is not None:
        return result

    return {"status": "ok", "found": "no", "table": []}

