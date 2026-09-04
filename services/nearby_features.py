"""OSM-derived physical context around a parcel that no official registry
exposes through an open API: distance to the nearest classified road, and
nearby named watercourses."""
from typing import Any

import httpx
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from config import WATERWAY_LABELS, logger
from geo_utils import geod, to_2180
from http_utils import _overpass_query, describe_exc

async def get_nearest_municipal_road(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    """Distance to the nearest gmina (municipal) road.

    IMPORTANT CAVEAT: OpenStreetMap does not reliably tag Poland's official
    road-management category (droga krajowa/wojewódzka/powiatowa/gminna).
    There is no free, open API that exposes this classification directly
    either (GUGiK's BDOT10k topographic database — the one dataset that DOES
    carry this attribute — returns the same "usługa nie udostępnia danych
    opisowych" non-answer as every other attribute query we tested; see the
    buildings/GESUT notes above). We therefore use the standard, widely-used
    OSM tagging convention for Poland as an approximation:
        highway=unclassified or highway=residential  ->  droga gminna
        highway=tertiary                              ->  usually powiatowa
    and fall back to tertiary only if no unclassified/residential road is
    found nearby, clearly labelling that fallback as such.
    """
    centroid = geometry.centroid
    radius_m = 3000
    query = (
        f'[out:json][timeout:25];'
        f'(way(around:{radius_m},{centroid.y},{centroid.x})'
        f'["highway"~"^(unclassified|residential)$"];);'
        f"out tags geom;"
    )
    try:
        data = await _overpass_query(client, query)
    except Exception as exc:
        logger.warning("get_nearest_municipal_road: Overpass niedostępny", exc_info=True)
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {describe_exc(exc)}"}

    fallback_used = False
    elements = data.get("elements", [])
    if not elements:
        fallback_used = True
        query2 = (
            f'[out:json][timeout:25];'
            f'(way(around:{radius_m},{centroid.y},{centroid.x})["highway"="tertiary"];);'
            f"out tags geom;"
        )
        try:
            data = await _overpass_query(client, query2)
            elements = data.get("elements", [])
        except Exception as exc:
            logger.warning("get_nearest_municipal_road: Overpass (fallback tertiary) niedostępny", exc_info=True)
            return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {describe_exc(exc)}"}

    if not elements:
        return {
            "status": "ok", "found": "no",
            "message": f"Brak dróg w promieniu {radius_m} m w danych OpenStreetMap.",
        }

    parcel_2180 = shapely_transform(to_2180.transform, geometry)

    best_dist = None
    best_road = None
    for el in elements:
        coords = el.get("geometry", [])
        if len(coords) < 2:
            continue
        line_wgs84 = LineString([(pt["lon"], pt["lat"]) for pt in coords])
        try:
            line_2180 = shapely_transform(to_2180.transform, line_wgs84)
        except Exception:
            continue
        dist = parcel_2180.distance(line_2180)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            tags = el.get("tags", {})
            best_road = {
                "name": tags.get("name") or "droga bez nazwy",
                "ref": tags.get("ref"),
                "highway_class": tags.get("highway"),
            }

    if best_dist is None or best_road is None:
        return {
            "status": "ok", "found": "no",
            "message": f"Brak dróg w promieniu {radius_m} m w danych OpenStreetMap.",
        }

    return {
        "status": "ok",
        "found": "yes",
        "distance_m": round(best_dist),
        "road_name": best_road["name"],
        "road_ref": best_road["ref"],
        "is_fallback_powiatowa": fallback_used,
        "source": "OpenStreetMap (Overpass API) — przybliżenie na podstawie klasyfikacji highway=unclassified/residential, GUGiK nie udostępnia kategorii zarządzania drogą (gminna/powiatowa) przez żadne otwarte API",
    }


async def get_waterways(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    centroid = geometry.centroid
    query = (
        f'[out:json][timeout:25];(way(around:400,{centroid.y},{centroid.x})["waterway"];);'
        f"out geom;"
    )
    try:
        data = await _overpass_query(client, query)
    except Exception as exc:
        logger.warning("get_waterways: Overpass niedostępny", exc_info=True)
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {describe_exc(exc)}"}

    seen: dict[str, dict[str, Any]] = {}
    for el in data.get("elements", []):
        coords = el.get("geometry", [])
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        kind_raw = tags.get("waterway", "ciek")
        kind = WATERWAY_LABELS.get(kind_raw, kind_raw)
        name = tags.get("name", "ciek bez nazwy")
        try:
            dist_m = min(
                geod.inv(centroid.x, centroid.y, pt["lon"], pt["lat"])[2]
                for pt in coords
            )
        except Exception:
            dist_m = None
        key = f"{name}-{kind}"
        if key not in seen or (dist_m is not None and dist_m < seen[key].get("distance_m", 1e9)):
            seen[key] = {
                "name": name, "kind": kind,
                "distance_m": round(dist_m) if dist_m is not None else None,
            }
    waters = sorted(seen.values(), key=lambda w: w["distance_m"] or 1e9)[:5]
    return {"status": "ok", "waterways": waters}

