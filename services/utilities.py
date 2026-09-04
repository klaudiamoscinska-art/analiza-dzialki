"""Section 3 — Media/uzbrojenie terenu (GESUT): presence of each utility
type near the parcel via KIUT's GetMap image-rendering endpoint (its
GetFeatureInfo attribute endpoint is structurally non-functional — see
docstring below)."""
import asyncio
import io
from typing import Any

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
    """
    half_extent_m = 60.0
    size_px = 240
    threshold_px = 60

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
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            non_transparent = sum(1 for px in img.getdata() if px[3] > 40)
            return {"key": label_key, "label": label, "present": non_transparent > threshold_px}
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

