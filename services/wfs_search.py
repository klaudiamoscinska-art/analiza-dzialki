"""'Szukaj działki' — search-by-locality pipeline: the per-powiat WFS
registry, axis-order-aware geometry enumeration, and the area/width/length
matching+ranking logic (search_parcels_universal)."""
import asyncio
import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx
from pyproj import Transformer
from shapely.geometry import shape

from config import TIMEOUT_WFS_POWIAT, logger
from geo_utils import geod, to_2180
from http_utils import _get_with_retry
from services.geocoding import geocode_address_points
from services.uldk import find_parcel_by_xy, find_parcel_with_area_by_xy

# --------------------------------------------------------------------------
# "Szukaj działki" — search by locality + target size, ±10% tolerance
# --------------------------------------------------------------------------
#
# The official national aggregated EGiB WFS
# (mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza) was
# confirmed live to be suffering a genuine, ongoing GUGiK-side database
# outage (identical "msPostGISLayerOpen(): Query error. Database connection
# failed" for multiple unrelated locations and both its feature types).
#
# Instead, this queries each powiat's OWN individual WFS server directly,
# using a lookup table (wfs_powiat_registry.json) built from a community-
# maintained snapshot of GUGiK's own official service registry (EZiU —
# Ewidencja Zbiorów i Usług, https://integracja.gugik.gov.pl/eziudp), listing
# 379 of ~380 Polish powiats' own working WFS endpoints, independent of the
# broken national aggregator. Confirmed live end-to-end for powiat suski
# (real parcel geometries returned near Stryszawa, correct location verified
# by coordinate transform) — including discovering and correcting for a
# real, non-obvious quirk: this particular server's declared DefaultCRS is
# the legacy EPSG:2178 zone, but it also supports EPSG:2180 when explicitly
# requested via srsName, and reports coordinate pairs in strict
# (northing, easting) axis order rather than the (easting, northing) order
# used elsewhere in this app — the axis-order auto-correction below exists
# because different third-party servers in this registry are expected to
# vary in this respect, and picking the "sane" interpretation that actually
# lands within Poland is more robust than assuming one fixed convention.
#
# Individual powiat servers are independently operated and will sometimes be
# slow, unreachable, or briefly down (confirmed live: powiat tatrzański
# timed out during testing) — this is normal for a federated system of
# ~380 separate servers and is handled per-request, not treated as fatal.

GML_NS = "{http://www.opengis.net/gml/3.2}"


def _load_wfs_powiat_registry() -> dict[str, dict[str, Any]]:
    # wfs_powiat_registry.json lives at the project root (next to main.py),
    # one level up from this services/ package — NOT next to this file.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, "wfs_powiat_registry.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


WFS_POWIAT_REGISTRY = _load_wfs_powiat_registry()
logger.info("Wczytano rejestr WFS: %d powiatów/gmin", len(WFS_POWIAT_REGISTRY))


def _lookup_wfs_config(teryt_id: str) -> Optional[dict[str, Any]]:
    """teryt_id looks like '121507_2.0004.3692/5'. Try the exact gmina-level
    key first (a handful of cities run their own separate service), then
    fall back to the powiat-level 4-digit prefix (the vast majority of
    entries in the registry)."""
    gmina_part = teryt_id.split(".")[0]  # e.g. "121507_2"
    if gmina_part in WFS_POWIAT_REGISTRY:
        return WFS_POWIAT_REGISTRY[gmina_part]
    powiat_prefix = gmina_part.split("_")[0][:4]  # e.g. "1215"
    return WFS_POWIAT_REGISTRY.get(powiat_prefix)


async def enumerate_parcel_points_in_area(
    client: httpx.AsyncClient, teryt_id: str, x_2180: float, y_2180: float,
    anchor_lon: float, anchor_lat: float,
    radius_m: float = 2000.0, max_features: int = 500,
) -> list[tuple[float, float]]:
    """Queries the specific powiat's own direct WFS server (looked up from
    the registry via teryt_id) for parcel geometries within a square area,
    returning one safe interior (lon, lat) point per geometry found.

    Axis order (northing,easting vs easting,northing) is determined ONCE per
    batch by comparing distance-to-anchor for both interpretations of the
    first parseable geometry — confirmed live to be necessary: a naive
    "does it land somewhere in Poland" check is not discriminating enough,
    since both interpretations can each land on a real (but very different,
    hundreds of km apart) Polish location. Distance-to-anchor is reliable
    because every genuine match must be within `radius_m` of the anchor by
    construction of the bounding box."""
    config = _lookup_wfs_config(teryt_id)
    if config is None:
        raise RuntimeError(
            "Ten powiat nie jest jeszcze w naszym rejestrze bezpośrednich usług WFS "
            f"(obecnie obsługujemy {len(WFS_POWIAT_REGISTRY)} z ok. 380 powiatów)."
        )

    is_v2 = config["version"].startswith("2.")
    typenames_param = "typenames" if is_v2 else "typename"
    bbox = f"{y_2180-radius_m},{x_2180-radius_m},{y_2180+radius_m},{x_2180+radius_m},urn:ogc:def:crs:EPSG::2180"
    params = {
        "service": "WFS",
        "version": config["version"],
        "request": "GetFeature",
        typenames_param: config["layer"],
        "srsName": "urn:ogc:def:crs:EPSG::2180",
        "bbox": bbox,
        ("count" if is_v2 else "maxFeatures"): str(max_features),
    }
    resp = await _get_with_retry(client, config["url"], params=params, timeout=TIMEOUT_WFS_POWIAT)
    if "ExceptionReport" in resp.text[:1000] or "ExceptionText" in resp.text[:1000]:
        logger.warning("WFS powiat %s zwrócił ExceptionReport: %.300s", config["url"], resp.text)
        raise RuntimeError(f"Serwer powiatu ({config['url']}) zwrócił błąd dla tego zapytania.")

    root = ET.fromstring(resp.text)
    to_4326 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
    pos_lists = list(root.iter(f"{GML_NS}posList"))

    def parse_nums(el) -> Optional[list[float]]:
        raw = (el.text or "").split()
        if len(raw) < 6:
            return None
        try:
            return [float(v) for v in raw]
        except ValueError:
            return None

    def try_representative_point(nums: list[float], swapped: bool) -> Optional[tuple[float, float]]:
        coords_2180 = (
            list(zip(nums[1::2], nums[0::2])) if swapped else list(zip(nums[0::2], nums[1::2]))
        )
        try:
            poly = shape({"type": "Polygon", "coordinates": [coords_2180]})
            if not poly.is_valid or poly.area == 0:
                return None
            rp = poly.representative_point()
            return to_4326.transform(rp.x, rp.y)
        except Exception:
            return None

    # Determine axis order once, from whichever early geometry gives a
    # decisive (much closer) match to the anchor under one interpretation.
    swapped = None
    for el in pos_lists[:5]:
        nums = parse_nums(el)
        if nums is None:
            continue
        p_unswapped = try_representative_point(nums, swapped=False)
        p_swapped = try_representative_point(nums, swapped=True)
        d_unswapped = (
            geod.inv(anchor_lon, anchor_lat, p_unswapped[0], p_unswapped[1])[2] if p_unswapped else None
        )
        d_swapped = (
            geod.inv(anchor_lon, anchor_lat, p_swapped[0], p_swapped[1])[2] if p_swapped else None
        )
        max_plausible_m = radius_m * 3  # generous margin over the query radius
        if d_unswapped is not None and d_unswapped < max_plausible_m and (d_swapped is None or d_unswapped < d_swapped):
            swapped = False
            break
        if d_swapped is not None and d_swapped < max_plausible_m and (d_unswapped is None or d_swapped < d_unswapped):
            swapped = True
            break

    if swapped is None:
        # Couldn't confidently determine axis order (e.g. no geometries at
        # all near the anchor) — nothing reliable to return.
        return []

    points: list[tuple[float, float]] = []
    for el in pos_lists:
        nums = parse_nums(el)
        if nums is None:
            continue
        p = try_representative_point(nums, swapped=swapped)
        if p is not None:
            points.append(p)
    return points


async def _gather_nearby_parcels(client: httpx.AsyncClient, place_query: str) -> dict[str, Any]:
    """Shared first half of both 'Szukaj działki' search modes (by area, by
    width+length): geocode the place name to a stable anchor point, find
    which powiat it's in, and enumerate+resolve every parcel found nearby
    via that powiat's own direct WFS server + ULDK. Returns either
    {'status': 'error', 'message': ...} or {'status': 'ok', 'parcels': {...},
    'search_center': ...} with parcels keyed by teryt_id (deduplicated,
    each with area_m2, short_side_m, long_side_m already computed).

    IMPORTANT — confirmed live bug fix: a bare place name (no street/number)
    matches MANY individual house address points scattered across that
    locality (e.g. 40 different addresses for one village), and the
    geocoder does not return them in a stable order — picking address_points[0]
    meant the exact same search could silently anchor on a different house
    each time, shifting the whole 2km search radius and changing the
    results. The median of ALL matched points is used instead — this stays
    put run-to-run (order-independent) and, being a median rather than a
    mean, is also robust to the rare case of an unrelated same-named place
    being mixed into the results."""
    address_points = await geocode_address_points(client, place_query, max_results=40)
    if not address_points:
        return {"status": "error", "message": f"Nie znaleziono miejscowości '{place_query}'."}

    lons = sorted(p["lon"] for p in address_points)
    lats = sorted(p["lat"] for p in address_points)
    mid = len(address_points) // 2
    anchor = {"lon": lons[mid], "lat": lats[mid], "description": place_query}
    x_2180, y_2180 = to_2180.transform(anchor["lon"], anchor["lat"])

    anchor_parcel = await find_parcel_by_xy(client, anchor["lon"], anchor["lat"])
    if anchor_parcel is None:
        return {
            "status": "error",
            "message": (
                f"Nie udało się sprawdzić '{place_query}' — serwer odpowiedzialny za tę okolicę "
                "(prowadzony osobno przez dany powiat) jest teraz chwilowo niedostępny. To nie błąd "
                "w aplikacji, tylko przejściowa awaria po stronie urzędu. Spróbuj ponownie za "
                "kilka-kilkanaście minut, albo w międzyczasie sprawdź inną miejscowość."
            ),
        }

    try:
        candidate_points = await enumerate_parcel_points_in_area(
            client, anchor_parcel["teryt_id"], x_2180, y_2180, anchor["lon"], anchor["lat"]
        )
    except Exception as exc:
        if "nie jest jeszcze w naszym rejestrze" in str(exc):
            return {
                "status": "error",
                "message": (
                    f"{exc} Ta okolica nie jest jeszcze obsługiwana — spróbuj innej miejscowości "
                    "(np. większej, sąsiedniej)."
                ),
            }
        return {
            "status": "error",
            "message": (
                f"Serwer odpowiedzialny za tę okolicę jest teraz chwilowo niedostępny "
                f"({exc}). To nie błąd w aplikacji, tylko przejściowa awaria po stronie urzędu — "
                "spróbuj ponownie za kilka-kilkanaście minut, albo w międzyczasie sprawdź inną "
                "miejscowość."
            ),
        }

    if not candidate_points:
        return {"status": "ok", "parcels": {}, "search_center": place_query}

    parcels = await asyncio.gather(
        *[find_parcel_with_area_by_xy(client, lon, lat) for lon, lat in candidate_points]
    )

    seen: dict[str, dict[str, Any]] = {}
    for parcel in parcels:
        if not parcel:
            continue
        teryt_id = parcel["teryt_id"]
        if teryt_id not in seen:
            seen[teryt_id] = parcel

    return {"status": "ok", "parcels": seen, "search_center": place_query}


async def search_parcels_universal(
    client: httpx.AsyncClient, place_query: str,
    target_area_m2: Optional[float] = None,
    target_width_m: Optional[float] = None, target_length_m: Optional[float] = None,
    dims_as_maximum: bool = False,
    area_tolerance: float = 0.10, dim_tolerance: float = 0.20, max_results: int = 10,
    min_rectangularity: float = 0.65,
) -> dict[str, Any]:
    """'Szukaj działki': one universal search accepting any combination of a
    target area (m², ±10%) and/or width/length (m). Width/length work in one
    of two modes:
      - Approximate (default): each side within ±20% of the target — a
        single side is accepted here as long as area is also given.
      - Maximum (dims_as_maximum=True): BOTH width and length are required,
        and treated as a hard ceiling — a parcel's short/long sides must
        each be ≤ the corresponding target, with no lower bound. Ranking is
        by how fully the parcel uses the given envelope (closer to the
        ceiling on both sides ranks first) rather than by closeness to a
        target, since there's no single 'target' value to measure distance
        from in this mode.
    A candidate must satisfy EVERY criterion supplied to be included; when
    not in maximum mode, ranking is by combined error across whichever
    criteria were supplied, closest first.

    Three refinements over a naive width/length match, since 'width×length'
    is only a meaningful description for a shape that's actually roughly
    rectangular (this applies in both approximate and maximum mode):
      1. Rectangularity filter — reject parcels whose real area is below
         min_rectangularity × (short_side × long_side). A bounding rectangle
         can technically satisfy both side-length checks while wrapping an
         L-shaped or triangular parcel that's nothing like what the person
         pictured — this filters those out rather than reporting a falsely
         confident match.
      2. RMS (root-mean-square) instead of a plain average to combine the
         individual relative errors in approximate mode — this penalises an
         uneven match (e.g. short side spot-on but long side way off) more
         than an average would.
      3. Implied-area cross-check — in approximate mode, when width+length
         are given without an explicit target area, target_width×target_length
         is compared against the parcel's real area as an extra RMS term.
         This catches cases the side-length + rectangularity checks alone
         might still miss, since it's a genuinely independent signal
         computed from the person's own two numbers."""
    gathered = await _gather_nearby_parcels(client, place_query)
    if gathered["status"] != "ok":
        return gathered

    want_area = target_area_m2 is not None
    have_width = target_width_m is not None
    have_length = target_length_m is not None
    want_dims_full = have_width and have_length
    want_single_dim = (have_width or have_length) and not want_dims_full and not dims_as_maximum
    single_dim_value = target_width_m if have_width else target_length_m
    want_any_dim = want_dims_full or want_single_dim

    target_short = min(target_width_m, target_length_m) if want_dims_full else None
    target_long = max(target_width_m, target_length_m) if want_dims_full else None
    implied_area = (
        (target_width_m * target_length_m) if (want_dims_full and not want_area and not dims_as_maximum) else None
    )

    matches = []
    for p in gathered["parcels"].values():
        sq_errors = []

        if want_area:
            a = p["area_m2"]
            area_err = abs(a - target_area_m2) / target_area_m2
            if area_err > area_tolerance:
                continue
            sq_errors.append(area_err ** 2)

        if want_dims_full and dims_as_maximum:
            s, l = p["short_side_m"], p["long_side_m"]
            if s > target_short or l > target_long:
                continue
            # Ranking signal: how fully the parcel fills the given envelope
            # (closer to the ceiling on both sides = higher fill = better).
            fill_short = s / target_short
            fill_long = l / target_long
            p["_fill"] = ((fill_short ** 2 + fill_long ** 2) / 2) ** 0.5

        elif want_dims_full:
            s, l = p["short_side_m"], p["long_side_m"]
            short_err = abs(s - target_short) / target_short
            long_err = abs(l - target_long) / target_long
            if short_err > dim_tolerance or long_err > dim_tolerance:
                continue
            sq_errors.append(short_err ** 2)
            sq_errors.append(long_err ** 2)
            if implied_area is not None:
                implied_err = abs(p["area_m2"] - implied_area) / implied_area
                sq_errors.append(implied_err ** 2)

        elif want_single_dim:
            s, l = p["short_side_m"], p["long_side_m"]
            err_vs_short = abs(s - single_dim_value) / single_dim_value
            err_vs_long = abs(l - single_dim_value) / single_dim_value
            if err_vs_short <= err_vs_long:
                best_err, p["_matched_side"] = err_vs_short, "short"
            else:
                best_err, p["_matched_side"] = err_vs_long, "long"
            if best_err > dim_tolerance:
                continue
            sq_errors.append(best_err ** 2)

        if want_dims_full or want_single_dim:
            s, l = p["short_side_m"], p["long_side_m"]
            rect_area = s * l
            rectangularity = p["area_m2"] / rect_area if rect_area > 0 else 0
            if rectangularity < min_rectangularity:
                continue
            p["rectangularity"] = rectangularity

        if dims_as_maximum and want_dims_full:
            # No target to measure "error" against in this mode — rank by
            # fill (computed above), highest first, so sort key is negated
            # to reuse the same ascending sort below.
            p["_combined_err"] = 1 - p["_fill"]
            del p["_fill"]
        else:
            p["_combined_err"] = (sum(sq_errors) / len(sq_errors)) ** 0.5 if sq_errors else 0.0
        matches.append(p)

    matches.sort(key=lambda p: p["_combined_err"])
    matches = matches[:max_results]

    for m in matches:
        if want_area:
            m["diff_pct"] = round((m["area_m2"] - target_area_m2) / target_area_m2 * 100, 1)
        if want_dims_full and dims_as_maximum:
            m["short_margin_pct"] = round((target_short - m["short_side_m"]) / target_short * 100, 1)
            m["long_margin_pct"] = round((target_long - m["long_side_m"]) / target_long * 100, 1)
        elif want_dims_full:
            m["short_diff_pct"] = round((m["short_side_m"] - target_short) / target_short * 100, 1)
            m["long_diff_pct"] = round((m["long_side_m"] - target_long) / target_long * 100, 1)
        elif want_single_dim:
            matched_val = m["short_side_m"] if m["_matched_side"] == "short" else m["long_side_m"]
            m["matched_side_diff_pct"] = round((matched_val - single_dim_value) / single_dim_value * 100, 1)
            m["matched_side_label"] = "krótszy bok" if m["_matched_side"] == "short" else "dłuższy bok"
            del m["_matched_side"]
        if want_any_dim or (want_dims_full and dims_as_maximum):
            m["rectangularity_pct"] = round(m["rectangularity"] * 100, 0)
            del m["rectangularity"]
        m["short_side_m"] = round(m["short_side_m"], 1)
        m["long_side_m"] = round(m["long_side_m"], 1)
        m["area_m2"] = round(m["area_m2"], 1)
        del m["_combined_err"]

    return {
        "status": "ok",
        "matches": matches,
        "candidates_checked": len(gathered["parcels"]),
        "search_center": gathered["search_center"],
        "criteria": {
            "area": want_area, "dimensions": want_dims_full, "single_dimension": want_single_dim,
            "dims_as_maximum": want_dims_full and dims_as_maximum,
        },
    }

