"""Section 2/4 — hazard checks against PIG-PIB and Wody Polskie ISOK:
landslide risk (SOPO), official flood-depth zones, and waterlogging risk."""
import json
from typing import Any

import httpx
from shapely.geometry.base import BaseGeometry

from config import (
    ISOK_MZP20_URL, PODTOPIENIA_BASE_URL, SOPO_BASE_URL, SOPO_LAYERS,
    TIMEOUT_ISOK_FLOOD, TIMEOUT_PIG_WATERLOGGING, logger,
)

async def check_landslide(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    """The 'query' capability is disabled on the individual SOPO feature
    layers, AND the host's WMSServer GetFeatureInfo endpoint is behind an
    Incapsula WAF that intermittently blocks plain server-side HTTP requests
    (both confirmed live). The ArcGIS REST 'identify' operation is enabled
    and NOT WAF-blocked, and was verified against a known Carpathian
    landslide polygon, a known-clear control parcel, and the real sample
    parcel 121507_2.0004.3692/5."""
    rings = [list(coord) for coord in geometry.exterior.coords]
    minx, miny, maxx, maxy = geometry.bounds
    params = {
        "geometry": json.dumps({"rings": [rings], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon",
        "sr": "4326",
        "layers": f"all:{','.join(str(k) for k in SOPO_LAYERS)}",
        "tolerance": "0",
        "mapExtent": f"{minx},{miny},{maxx},{maxy}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = await client.get(f"{SOPO_BASE_URL}/identify", params=params)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "błąd usługi SOPO"))
        results = data.get("results", [])
        matched = sorted({SOPO_LAYERS.get(r.get("layerId"), "nieznana kategoria") for r in results})
        return {"status": "ok", "has_landslide": len(results) > 0, "matched_categories": matched}
    except Exception as exc:
        logger.warning("check_landslide: usługa SOPO PIG-PIB niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa SOPO PIG-PIB niedostępna: {exc}"}


async def get_flood_zone(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    bbox = f"{x_2180-30},{y_2180-30},{x_2180+30},{y_2180+30}"
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:2180", "LAYERS": "16,17", "QUERY_LAYERS": "16,17",
        "BBOX": bbox, "WIDTH": "101", "HEIGHT": "101", "I": "50", "J": "50",
        "FEATURE_COUNT": "10", "INFO_FORMAT": "application/geojson",
    }
    try:
        resp = await client.get(ISOK_MZP20_URL, params=params, timeout=TIMEOUT_ISOK_FLOOD)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        depth_feature = next((f for f in features if f.get("properties", {}).get("GLEBOKOSC")), None)
        if depth_feature:
            return {
                "status": "ok", "in_flood_zone": True,
                "depth_range": depth_feature["properties"]["GLEBOKOSC"],
            }
        return {"status": "ok", "in_flood_zone": False, "depth_range": None}
    except Exception as exc:
        logger.warning("get_flood_zone: usługa ISOK niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa ISOK (Wody Polskie) niedostępna: {exc}"}


async def get_waterlogging_risk(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    rings = [list(coord) for coord in geometry.exterior.coords]
    minx, miny, maxx, maxy = geometry.bounds
    params = {
        "geometry": json.dumps({"rings": [rings], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon", "sr": "4326", "layers": "all:0",
        "tolerance": "0", "mapExtent": f"{minx},{miny},{maxx},{maxy}",
        "imageDisplay": "400,400,96", "returnGeometry": "false", "f": "json",
    }
    try:
        resp = await client.get(f"{PODTOPIENIA_BASE_URL}/identify", params=params, timeout=TIMEOUT_PIG_WATERLOGGING)
        resp.raise_for_status()
        data = resp.json()
        return {"status": "ok", "at_risk": len(data.get("results", [])) > 0}
    except Exception as exc:
        logger.warning("get_waterlogging_risk: usługa PIG-PIB niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa PIG-PIB niedostępna: {exc}"}

