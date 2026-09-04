"""Section 3 — Media/uzbrojenie terenu (GESUT): presence of each utility
type near the parcel via KIUT's GetMap image-rendering endpoint (its
GetFeatureInfo attribute endpoint is structurally non-functional — see
docstring below)."""
import asyncio
import io
from typing import Any, Optional

import httpx
from PIL import Image

from config import KIUT_LAYERS, KIUT_URL

async def check_utilities(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    """IMPORTANT FINDING (confirmed live, twice, at an urban location with a
    verified real water main AND at this app's rural test parcel): KIUT's
    GetFeatureInfo attribute endpoint ALWAYS returns the generic message
    "Usługa nie udostępnia danych opisowych dla wybranego obiektu" —
    regardless of location, layer, or search radius. This is a structural
    limitation of the national aggregator (it doesn't forward attribute
    queries to the 385 federated county backends at all), not a bug in this
    app — and it affects any client using this endpoint the same way.

    Workaround (verified live): the SAME service's GetMap (image rendering)
    operation DOES draw real utility lines. We render a small tile per layer
    and count non-transparent pixels; a calibrated threshold distinguishes a
    real nearby line (350-950 px in testing) from rendering noise/labels
    (2-8 px when nothing is there). This trades exact attribute text for a
    reliable presence signal, which is what the UI actually needs.

    Distance-to-nearest-line (added 2026-09-04, requested by Klaudia after
    comparing against Działkopedia's "71m dobry dojazd"-style output): since
    the tile's bbox size and pixel dimensions are both known, each pixel
    maps to a fixed real-world size (half_extent_m*2 / size_px). Finding the
    non-transparent pixel closest to the tile's center (the parcel) and
    converting that pixel offset to meters gives an approximate distance —
    not a survey-grade measurement (it's still an image-detection
    heuristic, not vector geometry), but the same kind of "how far" answer
    the nearest-road/waterway checks already give from real geometry.
    """
    half_extent_m = 60.0
    size_px = 240
    threshold_px = 60
    m_per_px = (half_extent_m * 2) / size_px
    center_px = (size_px - 1) / 2

    def _scan_pixels(content: bytes) -> tuple[int, Optional[float]]:
        """Pure CPU-bound image decode + per-pixel scan (up to
        240x240=57600 pixels PER layer, 6 layers = up to ~345600 total for
        one check_utilities() call) — factored out (2026-09-04) so it can
        run via asyncio.to_thread instead of blocking the event loop. This
        is the heaviest per-pixel workload in the app; confirmed live that
        this app's single-threaded Render process, under 12 concurrent
        /api/analyze branches, can starve OTHER unrelated network calls
        (even to a different host) long enough to make them time out — see
        config.py's MAX_CONCURRENT_SECTIONS comment and HANDOFF.md."""
        img = Image.open(io.BytesIO(content)).convert("RGBA")
        non_transparent = 0
        nearest_px: Optional[float] = None
        for idx, rgba in enumerate(img.getdata()):
            if rgba[3] <= 40:
                continue
            non_transparent += 1
            y, x = divmod(idx, size_px)
            dist_px = ((x - center_px) ** 2 + (y - center_px) ** 2) ** 0.5
            if nearest_px is None or dist_px < nearest_px:
                nearest_px = dist_px
        return non_transparent, nearest_px

    async def one(label_key: str, label: str, layer: str) -> dict[str, Any]:
        bbox = (
            f"{x_2180 - half_extent_m},{y_2180 - half_extent_m},"
            f"{x_2180 + half_extent_m},{y_2180 + half_extent_m}"
        )
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
            "LAYERS": layer, "STYLES": "", "SRS": "EPSG:2180", "BBOX": bbox,
            "WIDTH": str(size_px), "HEIGHT": str(size_px),
            "FORMAT": "image/png", "TRANSPARENT": "true",
        }
        try:
            resp = await client.get(KIUT_URL, params=params, follow_redirects=True)
            resp.raise_for_status()
            non_transparent, nearest_px = await asyncio.to_thread(_scan_pixels, resp.content)
            present = non_transparent > threshold_px
            result = {"key": label_key, "label": label, "present": present}
            if present and nearest_px is not None:
                result["distance_m"] = round(nearest_px * m_per_px)
            return result
        except Exception:
            return {"key": label_key, "label": label, "present": False, "error": True}

    results = await asyncio.gather(*[one(k, lbl, layer) for k, lbl, layer in KIUT_LAYERS])
    # Każda warstwa łapie swój własny wyjątek i wraca jako "present: False"
    # (żeby jeden padły typ medium nie ukrywał wyników pozostałych pięciu)
    # — ale to samo z siebie oznacza, że "wszystkie 6 warstw padło" wygląda
    # identycznie jak "sprawdzone, naprawdę brak mediów", zarówno dla
    # werdyktu (punktacja) jak i UI. Fixed 2026-09-04, zgłoszone przez
    # Klaudię jako "media przestały działać": jeśli KIUT padnie CAŁKOWICIE,
    # to teraz osobny status "error" (idzie do incomplete_sections, nie
    # punktowane) zamiast fałszywie pewnego "ok, brak mediów".
    if all(r.get("error") for r in results):
        return {"status": "error", "message": "Usługa KIUT (media/uzbrojenie terenu) niedostępna."}
    return {"status": "ok", "utilities": results}

