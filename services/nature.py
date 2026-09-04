"""Section 5 (competitor analysis, item 8 — see HANDOFF.md) — GDOŚ
protected nature areas (parki narodowe, rezerwaty, parki krajobrazowe,
obszary chronionego krajobrazu, Natura 2000) via GDOŚ's own WFS server.
Added 2026-09-04.

NOT verified live — government domains are blocked in the sandbox this
was written in. The URL (sdi.gdos.gov.pl/wfs) and the six layer
(typeName) names below are corroborated from several independent
open-source projects that already query this exact service (see
HANDOFF.md for citations) — NOT from GDOŚ's own live GetCapabilities,
which this sandbox can't reach. Confirm live before trusting this
further if it ever needs debugging."""
from typing import Any, Optional

import httpx
from pyproj import Transformer
from shapely.geometry import Point, shape

from config import GDOS_LAYERS, GDOS_WFS_URL, TIMEOUT_GDOS, logger

_to_4326 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)

# Poland's rough bounding box in each CRS this response might plausibly
# come back in — used only to figure out which CRS a given response
# actually used (see _looks_like_wgs84 below), not to validate anything.
_WGS84_LON_RANGE = (13.0, 25.0)
_WGS84_LAT_RANGE = (48.5, 55.5)


def _first_coord(geometry: dict) -> Optional[tuple[float, float]]:
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords:
        first = coords[0]
        if isinstance(first, (int, float)):
            return tuple(coords[:2])
        coords = first
    return None


def _looks_like_wgs84(coord: tuple[float, float]) -> bool:
    x, y = coord
    return _WGS84_LON_RANGE[0] <= x <= _WGS84_LON_RANGE[1] and _WGS84_LAT_RANGE[0] <= y <= _WGS84_LAT_RANGE[1]


async def get_protected_areas(
    client: httpx.AsyncClient, x_2180: float, y_2180: float, half_extent_m: float = 150.0,
) -> dict[str, Any]:
    """Queries all six GDOŚ layers in one WFS GetFeature call (a bbox
    around the parcel centroid), then keeps only the features that
    actually CONTAIN the point — a bbox hit alone isn't enough, since the
    bbox is deliberately generous and a real polygon boundary could pass
    nearby without covering the parcel.

    GeoServer's outputFormat=application/json is requested with
    srsName=EPSG:2180, but some GeoServer configurations ignore that and
    return WGS84 (CRS84) coordinates in GeoJSON regardless — this can't
    be verified live from here, so rather than assume one behaviour, the
    first returned coordinate is inspected and classified as WGS84-like
    or EPSG:2180-like by its actual magnitude (the same defensive
    principle already used for axis-order detection in
    wfs_search.enumerate_parcel_points_in_area, applied to a CRS guess
    instead of an axis-order guess)."""
    typenames = ",".join(name for name, _ in GDOS_LAYERS)
    bbox = (
        f"{x_2180 - half_extent_m},{y_2180 - half_extent_m},"
        f"{x_2180 + half_extent_m},{y_2180 + half_extent_m},EPSG:2180"
    )
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typenames, "srsName": "EPSG:2180", "bbox": bbox,
        "outputFormat": "application/json", "count": "50",
    }
    try:
        resp = await client.get(GDOS_WFS_URL, params=params, timeout=TIMEOUT_GDOS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("get_protected_areas: usługa GDOŚ WFS niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa GDOŚ (obszary chronione) niedostępna: {exc}"}

    features = data.get("features", [])
    if not features:
        return {"status": "ok", "areas": []}

    first_coord = None
    for f in features:
        first_coord = _first_coord(f.get("geometry") or {})
        if first_coord is not None:
            break
    if first_coord is None:
        return {"status": "ok", "areas": []}

    if _looks_like_wgs84(first_coord):
        query_lon, query_lat = _to_4326.transform(x_2180, y_2180)
        query_point = Point(query_lon, query_lat)
    else:
        query_point = Point(x_2180, y_2180)

    layer_labels = dict(GDOS_LAYERS)
    seen: set[str] = set()
    areas: list[dict[str, str]] = []
    for feature in features:
        try:
            geom = shape(feature["geometry"])
            if not geom.is_valid or not geom.contains(query_point):
                continue
        except Exception:
            continue

        feature_id = str(feature.get("id", ""))
        layer_key = feature_id.split(".")[0] if "." in feature_id else ""
        label = next((lbl for name, lbl in GDOS_LAYERS if name.endswith(layer_key)), None) or "obszar chroniony"

        props = feature.get("properties") or {}
        name = (
            props.get("nazwa") or props.get("NAZWA") or props.get("name")
            or props.get("form_nazwa") or label
        )
        if name in seen:
            continue
        seen.add(name)
        areas.append({"name": name, "kind": label})

    return {"status": "ok", "areas": areas}
