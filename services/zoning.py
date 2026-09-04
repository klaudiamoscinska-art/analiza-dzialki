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

# Keyword-based, best-effort detection of Plan Ogólny / OUZ mentions in
# whatever KIAPP's GetFeatureInfo happens to return — added 2026-09-03.
# NOT a structured parse of KIAPP's actual attribute schema (that schema
# isn't verifiable live in this environment — see HANDOFF.md), just a
# conservative text search so the UI can point out "this looks relevant"
# without asserting a plan type or OUZ membership we can't actually confirm.
# False negatives (missing a real mention) are acceptable; false positives
# (claiming plan ogólny/OUZ when the text doesn't actually say so) are not
# — hence keyword matching, never inference.
_PLAN_OGOLNY_KEYWORDS = ("plan ogólny", "planu ogólnego", "planie ogólnym", "planem ogólnym")
_OUZ_KEYWORDS = ("obszar uzupełnienia zabudowy", "obszaru uzupełnienia zabudowy", "obszarze uzupełnienia zabudowy", "(ouz)")

_NO_PLAN_OUZ_NOTE = (
    "Brak planu miejscowego (MPZP) dla tej działki. Od 1 września 2026 to już NIE oznacza "
    "automatycznie, że można ubiegać się o warunki zabudowy — decyzję WZ dla zwykłej zabudowy "
    "jednorodzinnej można dziś uzyskać tylko dla działek leżących w obszarze uzupełnienia "
    "zabudowy (OUZ) wyznaczonym w planie ogólnym gminy. Plan ogólny to osobny akt od MPZP — "
    "sprawdź go w Rejestrze Urbanistycznym (plany.gov.pl) lub w urzędzie gminy, zanim założysz, "
    "że warunki zabudowy będą możliwe."
)


def _mentions_any(text: str, keywords: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(kw in low for kw in keywords)


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
        result = {"status": "ok", "found": "yes" if has_plan else "no", "table": table, "source": source_label}
        if has_plan:
            result["mentions_plan_ogolny"] = _mentions_any(text, _PLAN_OGOLNY_KEYWORDS)
            result["mentions_ouz"] = _mentions_any(text, _OUZ_KEYWORDS)
        return result
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
    """Queries the new national APP aggregator (KIAPP) — richer, act-level
    metadata (nazwa planu, uchwała, data wejścia w życie, status) once gminas
    populate it — and the legacy KIMPZP zoning-symbol service CONCURRENTLY
    (added 2026-09-04, performance investigation — see HANDOFF.md and the
    'Plan Pamięci Podręcznej' artifact: this was previously sequential,
    KIMPZP only tried after KIAPP came back empty, making this branch alone
    responsible for most of /api/analyze's worst-case latency ceiling).
    KIAPP's result wins whenever it has one (same precedence as before,
    just no longer paying for it in wall-clock time) — it's the richer,
    newer source; KIMPZP is the fallback only when KIAPP found nothing at
    all. Both use the same fast-GetMap-probe strategy (see
    _mpzp_has_plan_drawn) since KIMPZP's GetFeatureInfo has been confirmed
    live to hang indefinitely for gminas without their own backend.

    When NEITHER source finds a plan, attaches a 'note' explaining the
    Plan Ogólny / OUZ rule that took effect 2026-09-01 (added 2026-09-03,
    see HANDOFF.md) — 'no MPZP' used to mean 'ask for warunki zabudowy
    freely', which is no longer true, and this is the one place in the app
    where someone would otherwise walk away with that outdated assumption.

    'KIAPP wins whenever it has one' means a REAL result (ok/partial), not
    merely 'not None' — fixed 2026-09-04: a transient KIAPP failure (an
    'error' dict, also not None) used to short-circuit here and mask a
    KIMPZP result that had concurrently succeeded with real plan data,
    throwing away the one section the module docstring calls out as most
    decision-relevant. An error is now returned only if NEITHER source
    produced real data — and even then, preferred over the plain 'no plan'
    note, since 'a source failed' and 'both sources confirmed nothing here'
    are different findings that shouldn't collapse into the same message."""
    kiapp_result, kimpzp_result = await asyncio.gather(
        _try_zoning_source(client, KIAPP_URL, KIAPP_LAYERS, x_2180, y_2180, "Rejestr Urbanistyczny/APP"),
        _try_zoning_source(client, KIMPZP_URL, KIMPZP_LAYERS, x_2180, y_2180, "MPZP (KIMPZP)"),
    )

    def _has_real_data(result: Optional[dict[str, Any]]) -> bool:
        return result is not None and result.get("status") != "error"

    if _has_real_data(kiapp_result):
        return kiapp_result
    if _has_real_data(kimpzp_result):
        return kimpzp_result
    if kiapp_result is not None:
        return kiapp_result
    if kimpzp_result is not None:
        return kimpzp_result

    return {"status": "ok", "found": "no", "table": [], "note": _NO_PLAN_OUZ_NOTE}

