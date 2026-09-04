"""Section 1 — Ewidencja gruntów i budynków: basic KIEG cadastre summary
plus building footprints/attributes sourced from OpenStreetMap (KIEG/BDOT
don't expose per-building attributes through any open API)."""
from typing import Any

import httpx
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from config import KIEG_LAYERS, KIEG_URL, OSM_BUILDING_LABELS, logger
from geo_utils import _clean_feature_info_text, _parse_feature_info_table, geod
from http_utils import _overpass_query, describe_exc, wms_get_feature_info

async def get_buildings_on_parcel(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    centroid = geometry.centroid
    query = (
        f'[out:json][timeout:25];(way(around:60,{centroid.y},{centroid.x})["building"];);'
        f"out geom;"
    )
    try:
        data = await _overpass_query(client, query)
    except Exception as exc:
        logger.warning("get_buildings_on_parcel: Overpass niedostępny", exc_info=True)
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {describe_exc(exc)}"}
    buildings = []
    for el in data.get("elements", []):
        coords = el.get("geometry", [])
        if len(coords) < 3:
            continue
        ring = [(pt["lon"], pt["lat"]) for pt in coords]
        try:
            poly = shape({"type": "Polygon", "coordinates": [ring]})
        except Exception:
            continue
        if not poly.is_valid or poly.area == 0:
            continue
        if not geometry.intersects(poly):
            continue
        area_m2, _ = geod.geometry_area_perimeter(poly)
        tags = el.get("tags", {})
        tag = tags.get("building", "yes")
        buildings.append({
            "label": OSM_BUILDING_LABELS.get(tag, f"budynek ({tag})"),
            "area_m2": round(abs(area_m2), 1),
            "fully_within_parcel": geometry.contains(poly),
            "levels_above_ground": tags.get("building:levels"),
            "levels_below_ground": tags.get("building:levels:underground"),
            "osm_id": el.get("id"),
        })

    return {
        "status": "ok",
        "found": "yes" if buildings else "no",
        "buildings": buildings,
        "source": "OpenStreetMap (Overpass API), dopasowane przestrzennie do granic działki",
    }


async def get_cadastre_basic(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    try:
        resp = await wms_get_feature_info(client, KIEG_URL, KIEG_LAYERS, x_2180, y_2180)
        table = _parse_feature_info_table(resp.text)
        text = _clean_feature_info_text(resp.text)
        return {
            "status": "ok",
            "table": table,
            "summary": text if text else "Brak danych w tej lokalizacji.",
        }
    except Exception as exc:
        logger.warning("get_cadastre_basic: usługa KIEG niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa niedostępna: {describe_exc(exc)}"}

