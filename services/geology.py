"""Section 6 (competitor analysis, item 9 — see HANDOFF.md) — PIG-PIB
MIDAS mining areas (obszary/tereny górnicze), a real legal encumbrance on
a parcel. Added 2026-09-04. Same ArcGIS REST 'identify' pattern already
confirmed live for SOPO in services/hazards.py, applied to the 'midas'
service on the same host — but unlike SOPO (whose layer IDs were
confirmed live via ?f=json), MIDAS's URL and response shape are NOT
verified live here (see HANDOFF.md)."""
import json
from typing import Any

import httpx
from shapely.geometry.base import BaseGeometry

from config import MIDAS_BASE_URL, TIMEOUT_MIDAS, logger


async def check_mining_areas(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    """Uses the ArcGIS REST 'identify' operation's 'value' field — a
    protocol-level field in Esri's identify response contract (the
    feature's primary display value), not a guessed attribute name —
    since MIDAS's actual attribute schema isn't verifiable from here the
    way SOPO_LAYERS was."""
    rings = [list(coord) for coord in geometry.exterior.coords]
    minx, miny, maxx, maxy = geometry.bounds
    params = {
        "geometry": json.dumps({"rings": [rings], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon",
        "sr": "4326",
        "layers": "all",
        "tolerance": "0",
        "mapExtent": f"{minx},{miny},{maxx},{maxy}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = await client.get(f"{MIDAS_BASE_URL}/identify", params=params, timeout=TIMEOUT_MIDAS)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "błąd usługi MIDAS"))
        results = data.get("results", [])
        names = sorted({r.get("value") for r in results if r.get("value")})
        return {"status": "ok", "has_mining_area": len(results) > 0, "names": names}
    except Exception as exc:
        logger.warning("check_mining_areas: usługa MIDAS PIG-PIB niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa MIDAS PIG-PIB niedostępna: {exc}"}
