"""
Analiza Działki GIS — backend (v2)
====================================
FastAPI application that, given a Polish TERYT parcel identifier, orchestrates
calls to several free, official Polish government (and OpenStreetMap) geo
services and returns a consolidated JSON report.

Sections (v2, after benchmarking against the reference app "Skaner Działek"
at dzialka-info.lovable.app):

  0. GUGiK ULDK              -> parcel geometry + TERYT resolution
  1. Ewidencja gruntów i budynków -> EGiB parcel/classification info (KIEG
                                  GetFeatureInfo) + buildings on/near the
                                  parcel (OpenStreetMap Overpass, spatially
                                  matched against the parcel polygon)
  2. Zagrożenie osuwiskowe    -> PIG-PIB SOPO landslide + hazard-zone polygons
                                  (ArcGIS REST 'identify')
  3. Media / uzbrojenie terenu -> GESUT utility lines presence (KIUT
                                  GetFeatureInfo), per utility type
  4. Hydrologia i zagrożenie powodziowe -> nearby watercourses (Overpass),
                                  official flood-depth zones (Wody Polskie
                                  ISOK MZP20), waterlogging-prone areas
                                  (PIG-PIB hydrogeologia/podtopienia)
  5. Plany zagospodarowania   -> MPZP GetFeatureInfo (KIMPZP), tabular
  6. Pozwolenia na budowę (GUNB/RWDZ) -> deep link only; the RWDZ registry
                                  has no open API (CAPTCHA-protected search)
  7. Wycena statystyczna      -> land value (area x regional avg price/m^2)
                                  + a separate, clearly-caveated rough
                                  buildings value (footprint area x assumed
                                  build cost/m^2)

Map layers (frontend, toggleable):
  - EGiB (parcels + parcel numbers + buildings) — WMS tile overlay, KIEG
  - MPZP (plany zagospodarowania)               — WMS tile overlay, KIMPZP
  SOPO/hydrogeologia are NOT offered as map tile overlays: cbdgmapa.pgi.gov.pl
  is behind an Incapsula bot-mitigation WAF that intermittently blocks plain
  HTTP requests (confirmed live). Panel-only "identify" calls (unaffected by
  the WAF) are used instead of visual overlays for those two sources.
"""

import asyncio
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pyproj import Geod, Transformer
from shapely import wkb, wkt
from shapely.geometry import LineString, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

# --------------------------------------------------------------------------
# Real, verified endpoints
# --------------------------------------------------------------------------

ULDK_URL = "https://uldk.gugik.gov.pl/"

# PIG-PIB SOPO landslide + hazard-zone polygon layers, ArcGIS Server.
# Layer IDs confirmed live via .../rest/services/geozagrozenia/sopo_obszary/
# MapServer?f=json. We use ALL polygon-type hazard layers.
SOPO_BASE_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/geozagrozenia/sopo_obszary/MapServer"
SOPO_LAYERS = {
    9: "rumosze i blokowiska",
    10: "zagłębienie wewnątrzosuwiskowe",
    11: "zbiornik/podmokłość osuwiskowa",
    12: "teren zagrożony",
    13: "strefa aktywności osuwiska",
    14: "osuwisko",
}

# PIG-PIB waterlogging-prone-areas hazard layer (same host/WAF as SOPO, so
# also queried via REST 'identify', layer 0).
PODTOPIENIA_BASE_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/hydrogeologia/podtopienia/MapServer"

# Wody Polskie ISOK — official flood-hazard maps (Mapy Zagrozenia Powodziowego),
# "medium probability" (~1%) flood depth layer. Layers 16 (depth polygons) and
# 17 (river-basin reference info) confirmed via live GetFeatureInfo test.
ISOK_MZP20_URL = "https://wody.isok.gov.pl/gpservices/KZGW/MZP20_Glebokosc_SredniePrawdopodPowodzi/MapServer/WMSServer"

# GUGiK national WMS aggregation services ("Krajowa Integracja ...")
KIUT_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"
KIEG_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
KIMPZP_URL = (
    "https://mapy.geoportal.gov.pl/wss/ext/"
    "KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
)
# NEW (confirmed live, no auth needed, launched alongside "Rejestr Urbanistyczny"
# on 2026-07-01): a modern national aggregator specifically for Akty
# Planowania Przestrzennego (APP) — plans ogólne, MPZP, uchwały krajobrazowe
# etc. As of testing, gminas are still uploading data into it nationally
# (confirmed empty even for Warszawa), so it will start returning real plan
# metadata (nazwa planu, uchwała, data wejścia w życie, status) as more
# gminas populate it through the transition period (until 2026-09-30) and
# beyond. Kept alongside the legacy KIMPZP, whichever answers first wins.
KIAPP_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaAktowPlanowaniaPrzestrzennego"
KIAPP_LAYERS = "app"

# Per-utility-type GESUT layers with human labels (order = display order).
KIUT_LAYERS = [
    ("woda", "Wodociąg", "przewod_wodociagowy"),
    ("kanalizacja", "Kanalizacja", "przewod_kanalizacyjny"),
    ("gaz", "Gaz", "przewod_gazowy"),
    ("prad", "Prąd", "przewod_elektroenergetyczny"),
    ("cieplo", "Ciepłociąg", "przewod_cieplowniczy"),
    ("telekom", "Telekomunikacja", "przewod_telekomunikacyjny"),
]
# NOTE: "budynki" is published as queryable="0" in KIEG's capabilities, so it
# cannot answer GetFeatureInfo directly. We use the sub-layers that ARE
# queryable for the basic EGiB summary; building-level detail instead comes
# from OpenStreetMap (see get_buildings_on_parcel).
KIEG_LAYERS = "dzialki,kontury,uzytki"
KIMPZP_LAYERS = "plany"

# OpenStreetMap Overpass — used for two things ULDK/EGiB/GESUT can't give us
# through any open API: (a) per-building footprints+attributes on the parcel,
# (b) nearby named watercourses. Poland's OSM building layer was bulk-imported
# from geoportal.gov.pl building footprints, so geometry closely tracks EGiB.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Overpass's usage policy expects a descriptive User-Agent identifying the
# client; requests without one are more likely to be rate-limited/blocked
# (confirmed live: identical request failed without a UA, succeeded with one).
OVERPASS_HEADERS = {"User-Agent": "AnalizaDzialkiGIS/2.0 (kontakt: patrz repozytorium)"}


async def _overpass_query(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    last_exc: Optional[Exception] = None
    for url in OVERPASS_URLS:
        try:
            resp = await client.post(
                url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=14.0
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            continue
    raise last_exc or RuntimeError("Overpass niedostępny")

# GUNB's official building-permits register (RWDZ) has no public API and its
# search UI is CAPTCHA-protected. We only offer a deep link, never scraped data.
GUNB_SEARCH_URL = "https://wyszukiwarka.gunb.gov.pl/"

HTTP_TIMEOUT = 20.0

OSM_BUILDING_LABELS: dict[str, str] = {
    "house": "budynek mieszkalny jednorodzinny",
    "detached": "budynek mieszkalny jednorodzinny",
    "residential": "budynek mieszkalny",
    "apartments": "budynek wielorodzinny",
    "hut": "budynek gospodarczy / altana",
    "shed": "budynek gospodarczy (szopa)",
    "garage": "garaż",
    "garages": "garaże",
    "barn": "stodoła",
    "farm_auxiliary": "budynek gospodarczy",
    "outbuilding": "budynek gospodarczy",
    "service": "budynek usługowy",
    "industrial": "budynek przemysłowy",
    "commercial": "budynek handlowo-usługowy",
    "greenhouse": "szklarnia",
    "yes": "budynek (typ nieokreślony)",
}

WATERWAY_LABELS: dict[str, str] = {
    "river": "rzeka",
    "stream": "strumień / potok",
    "brook": "strumyk",
    "ditch": "rów",
    "drain": "rów melioracyjny",
    "canal": "kanał",
}

GUS_PRICE_PER_M2: dict[str, float] = {
    "02": 78.0, "04": 52.0, "06": 41.0, "08": 45.0, "10": 58.0,
    "12": 121.0, "14": 143.0, "16": 44.0, "18": 49.0, "20": 38.0,
    "22": 112.0, "24": 95.0, "26": 36.0, "28": 40.0, "30": 86.0, "32": 61.0,
}

VOIVODESHIP_NAMES: dict[str, str] = {
    "02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie",
    "08": "lubuskie", "10": "łódzkie", "12": "małopolskie",
    "14": "mazowieckie", "16": "opolskie", "18": "podkarpackie",
    "20": "podlaskie", "22": "pomorskie", "24": "śląskie",
    "26": "świętokrzyskie", "28": "warmińsko-mazurskie",
    "30": "wielkopolskie", "32": "zachodniopomorskie",
}

ROUGH_BUILD_COST_PER_M2 = 4500.0

app = FastAPI(title="Analiza Działki GIS")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_api_responses(request, call_next):
    """Confirmed live bug: without an explicit no-store header, some mobile
    browsers cache GET /api/analyze and /api/resolve responses (keyed by the
    exact query string), so re-visiting the same parcel after a backend
    change can silently serve a stale cached JSON body missing new fields —
    even though the live server itself already returns the new data. API
    responses must never be cached; only /static/ files should be."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


geod = Geod(ellps="WGS84")
to_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

_EWKT_SRID_PREFIX = re.compile(r"^SRID=\d+;\s*", re.IGNORECASE)


def _parse_uldk_geometry(raw: str) -> BaseGeometry:
    raw = raw.strip()
    stripped = _EWKT_SRID_PREFIX.sub("", raw)
    try:
        return wkt.loads(stripped)
    except Exception:
        pass
    try:
        return wkb.loads(bytes.fromhex(raw))
    except Exception as exc:
        raise ValueError(f"Nie udało się sparsować geometrii ULDK: {exc}")


def _clean_feature_info_text(raw_html: str) -> str:
    if not raw_html or not raw_html.strip():
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" | ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\|\s*){2,}", "| ", text).strip(" |")
    return text


def _parse_feature_info_table(raw_html: str) -> list[dict[str, str]]:
    """GUGiK's MapServer GetFeatureInfo templates (confirmed live for both
    KIEG and KIMPZP) render one <tr><td>label</td><td>value</td></tr> per
    field — i.e. each row IS a label:value pair, not a header row followed by
    data rows. Parse accordingly into [{"label": ..., "value": ...}, ...],
    with a blank "label" cell (feature separator rows some services emit)
    used to start a new group when multiple features are returned."""
    if not raw_html or not raw_html.strip():
        return []
    soup = BeautifulSoup(raw_html, "html.parser")
    rows_out: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0]:
                rows_out.append({"label": cells[0], "value": " ".join(cells[1:])})
    return rows_out


GEOCODER_URL = "https://capap.gugik.gov.pl/api/fts/gc/pkt"
MAX_OBREB_SCAN = 40


async def geocode_address_points(client: httpx.AsyncClient, query: str, max_results: int = 15) -> list[dict[str, Any]]:
    """Free-text address search (street + number + city) using the same
    official GUGiK geocoder as the gmina lookup above — confirmed live with
    the generic 'q' field, e.g. 'Kraków Floriańska 5' resolves to an exact
    address point with coordinates. An ambiguous query (e.g. a common street
    name with no city) returns many candidates instead of one 'single' match;
    we cap how many we follow up on to keep the response fast."""
    try:
        resp = await client.post(GEOCODER_URL, json={"reqs": [{"q": query}]}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    points: list[dict[str, Any]] = []
    for group in data:
        items = group.get("others") or ([group.get("single")] if group.get("single") else [])
        for item in items:
            if not item:
                continue
            geom = item.get("geometry", {})
            coords = geom.get("coordinates")
            if not coords or len(coords) != 2:
                continue
            points.append({
                "lon": coords[0],
                "lat": coords[1],
                "description": item.get("shortDesc") or item.get("desc") or query,
            })
            if len(points) >= max_results:
                break
        if len(points) >= max_results:
            break
    return points


async def _uldk_get_by_xy_raw(
    client: httpx.AsyncClient, lon: float, lat: float, result_fields: str, max_retries: int = 2
) -> Optional[list[str]]:
    """Shared GetParcelByXY caller with a short retry for transient backend
    failures. Confirmed live: individual powiat backends behind ULDK's
    aggregation occasionally fail with 'Brak połączenia ze zbiorczą bazą'
    (no connection to the aggregate database) even when the same query
    succeeds moments later or moments before — this is intermittent
    upstream flakiness, not a real 'no parcel here' answer (which instead
    comes back as a plain '-1 brak wyników' with no such connection-error
    text), so only THAT specific failure mode is retried."""
    params = {
        "request": "GetParcelByXY",
        "xy": f"{lon},{lat},4326",
        "result": result_fields,
        "srid": "4326",
    }
    last_text = ""
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(ULDK_URL, params=params)
            resp.raise_for_status()
            last_text = resp.text
            lines = [ln for ln in resp.text.strip().split("\n") if ln.strip()]
            if len(lines) >= 2 and lines[0].strip() == "0":
                return [f.strip() for f in lines[1].split("|")]
            # A genuine "no parcel here" (no connection-error text) — retrying won't help.
            if "połączenia" not in resp.text and "zbiorcz" not in resp.text:
                return None
        except Exception:
            pass
        if attempt < max_retries:
            await asyncio.sleep(1.5)
    return None


async def find_parcel_by_xy(client: httpx.AsyncClient, lon: float, lat: float) -> Optional[dict[str, str]]:
    """Given coordinates, finds the cadastral parcel at that point via ULDK's
    GetParcelByXY — the same official service used everywhere else in this
    app, just a different lookup mode."""
    fields = await _uldk_get_by_xy_raw(client, lon, lat, "id,voivodeship,county,commune,parcel")
    if fields is None or len(fields) < 5:
        return None
    teryt_id, voivodeship, county, commune, parcel_no = fields[:5]
    return {
        "teryt_id": teryt_id,
        "voivodeship": voivodeship,
        "county": county,
        "commune": commune,
        "parcel_no": parcel_no,
    }


def _rectangle_side_lengths(geometry_2180) -> tuple[float, float]:
    """Minimum-rotated-rectangle side lengths (meters) for an irregular
    parcel polygon — the standard way to give a meaningful 'width x length'
    for a shape that usually isn't a true rectangle. Returns (short, long)."""
    mrr = geometry_2180.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)[:4]
    d1 = ((coords[0][0] - coords[1][0]) ** 2 + (coords[0][1] - coords[1][1]) ** 2) ** 0.5
    d2 = ((coords[1][0] - coords[2][0]) ** 2 + (coords[1][1] - coords[2][1]) ** 2) ** 0.5
    return (min(d1, d2), max(d1, d2))


async def find_parcel_with_area_by_xy(client: httpx.AsyncClient, lon: float, lat: float) -> Optional[dict[str, Any]]:
    """Same as find_parcel_by_xy, but also fetches geometry and computes the
    authoritative geodesic area (m^2), plus approximate width/length (via the
    minimum rotated bounding rectangle, in meters) — same calculation method
    used by the main /api/analyze endpoint for area, so figures are
    consistent across the whole app regardless of which search path found
    the parcel."""
    fields = await _uldk_get_by_xy_raw(client, lon, lat, "id,voivodeship,county,commune,parcel,geom_wkt")
    if fields is None or len(fields) < 6:
        return None
    try:
        teryt_id, voivodeship, county, commune, parcel_no, geom_raw = fields[:6]
        geometry = _parse_uldk_geometry(geom_raw)
        area_m2, _ = geod.geometry_area_perimeter(geometry)
        geometry_2180 = shapely_transform(to_2180.transform, geometry)
        short_side, long_side = _rectangle_side_lengths(geometry_2180)
        return {
            "teryt_id": teryt_id,
            "voivodeship": voivodeship,
            "county": county,
            "commune": commune,
            "parcel_no": parcel_no,
            "area_m2": abs(area_m2),
            "short_side_m": short_side,
            "long_side_m": long_side,
        }
    except Exception:
        return None


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
_POLAND_BOUNDS = (13.5, 48.8, 24.7, 55.0)  # lon_min, lat_min, lon_max, lat_max


def _load_wfs_powiat_registry() -> dict[str, dict[str, Any]]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wfs_powiat_registry.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


WFS_POWIAT_REGISTRY = _load_wfs_powiat_registry()


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


def _within_poland(lon: float, lat: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = _POLAND_BOUNDS
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


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
    resp = await client.get(config["url"], params=params, timeout=45.0)
    resp.raise_for_status()
    if "ExceptionReport" in resp.text[:1000] or "ExceptionText" in resp.text[:1000]:
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


async def resolve_address_to_parcels(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    """Full pipeline: free-text address -> geocoded point(s) -> parcel(s) at
    those point(s), deduplicated by TERYT id. Each candidate's 'commune'
    field is annotated with the matched address text, since multiple
    geocoded points can resolve to the same or different parcels and the
    person needs to tell them apart in the picker."""
    address_points = await geocode_address_points(client, query)
    if not address_points:
        return []

    parcels = await asyncio.gather(
        *[find_parcel_by_xy(client, p["lon"], p["lat"]) for p in address_points]
    )

    seen: dict[str, dict[str, str]] = {}
    for point, parcel in zip(address_points, parcels):
        if not parcel:
            continue
        teryt_id = parcel["teryt_id"]
        if teryt_id not in seen:
            parcel = dict(parcel)
            parcel["commune"] = f"{parcel['commune']} ({point['description']})"
            seen[teryt_id] = parcel
    return list(seen.values())


async def geocode_gmina_candidates(client: httpx.AsyncClient, name: str) -> list[dict[str, str]]:
    """Resolves a plain gmina/place name to its gmina TERYT code(s) using
    GUGiK's own official free-text geocoding API (capap.gugik.gov.pl/api/fts —
    confirmed live, free for commercial/non-commercial use). Querying the
    structured 'gm_nazwa' field (rather than free-text 'miejsc_nazwa') targets
    the gmina level specifically — confirmed live: 'gm_nazwa=Milówka' finds
    the real Gmina Milówka (śląskie), while a free-text search for the same
    string can instead match an unrelated same-named village in a different
    gmina (Milówka, a village inside Gmina Wojnicz)."""
    try:
        resp = await client.post(
            GEOCODER_URL, json={"reqs": [{"gm_nazwa": name}]}, timeout=15.0
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    seen: dict[str, dict[str, str]] = {}
    for group in data:
        for item in group.get("others", []) or ([group.get("single")] if group.get("single") else []):
            if not item:
                continue
            gmina_teryt = item.get("teryt")
            if not gmina_teryt or len(gmina_teryt) != 7:
                continue
            if gmina_teryt not in seen:
                seen[gmina_teryt] = {
                    "gmina_teryt": gmina_teryt,
                    "gmina_prefix": f"{gmina_teryt[:6]}_{gmina_teryt[6]}",
                    "gm_nazwa": item.get("gm_nazwa", ""),
                    "pow_nazwa": item.get("pow_nazwa", ""),
                    "woj_nazwa": item.get("woj_nazwa", ""),
                }
    return list(seen.values())


async def scan_gmina_obreby_for_parcel(
    client: httpx.AsyncClient, gmina_prefix: str, parcel_no: str
) -> list[dict[str, str]]:
    """Brute-force scan across a gmina's cadastral precincts (obręby) for a
    given parcel number. Parcel numbers are unique only within a single
    obręb, not across a whole gmina, but a gmina rarely has more than a
    couple dozen obręby, so scanning all of them concurrently for one
    specific number is fast and reliable — this is the mechanism that
    resolves cases like 'Milówka 2994/4' where the parcel is actually
    registered under a different village's obręb (here: 'Laliki') within
    the same gmina."""

    async def try_obreb(n: int) -> Optional[dict[str, str]]:
        obreb_id = f"{gmina_prefix}.{n:04d}.{parcel_no}"
        try:
            params = {
                "request": "GetParcelById",
                "id": obreb_id,
                "result": "id,voivodeship,county,commune,region,parcel",
                "srid": "4326",
            }
            resp = await client.get(ULDK_URL, params=params, timeout=10.0)
            resp.raise_for_status()
            lines = [ln for ln in resp.text.strip().split("\n") if ln.strip()]
            # Confirmed live format: line 1 is "0" (found) or "-1 ..." (not found);
            # when found, line 2 holds the pipe-delimited data.
            if len(lines) < 2 or lines[0].strip() != "0":
                return None
            fields = [f.strip() for f in lines[1].split("|")]
            if len(fields) < 6:
                return None
            teryt_id, voivodeship, county, commune, region, p_no = fields[:6]
            return {
                "teryt_id": teryt_id,
                "voivodeship": voivodeship,
                "county": county,
                "commune": f"{commune} (obręb {region})",
                "parcel_no": p_no,
            }
        except Exception:
            return None

    results = await asyncio.gather(*[try_obreb(n) for n in range(1, MAX_OBREB_SCAN + 1)])
    return [r for r in results if r is not None]


async def uldk_search_candidates(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    """Lightweight lookup (no geometry) used by /api/resolve for the
    'type a place name + parcel number' flow. ULDK's GetParcelByIdOrNr
    natively supports free-text 'ObrębName Numer' search and — confirmed
    live — returns ALL matches with their gmina/powiat/województwo when the
    name exists in more than one place in Poland (e.g. 'Wola 1' matches 5
    different villages), which is exactly the disambiguation data we need."""
    params = {
        "request": "GetParcelByIdOrNr",
        "id": query,
        "result": "id,voivodeship,county,commune,parcel",
        "srid": "4326",
    }
    resp = await client.get(ULDK_URL, params=params)
    resp.raise_for_status()
    lines = [ln for ln in resp.text.strip().split("\n") if ln != ""]
    if not lines:
        return []
    first = lines[0].strip()
    if first.startswith("-1") or first == "0":
        return []
    try:
        count = int(first)
        data_lines = lines[1 : 1 + count] if count >= 1 else []
    except ValueError:
        data_lines = [first]

    candidates = []
    for line in data_lines:
        fields = [f.strip() for f in line.split("|")]
        if len(fields) < 5:
            continue
        teryt_id, voivodeship, county, commune, parcel_no = fields[:5]
        candidates.append({
            "teryt_id": teryt_id,
            "voivodeship": voivodeship,
            "county": county,
            "commune": commune,
            "parcel_no": parcel_no,
        })
    return candidates


async def uldk_get_parcel(client: httpx.AsyncClient, parcel_id: str) -> dict[str, Any]:
    params = {
        "request": "GetParcelByIdOrNr",
        "id": parcel_id,
        "result": "id,geom_wkt,voivodeship,county,commune,parcel",
        "srid": "4326",
    }
    resp = await client.get(ULDK_URL, params=params)
    resp.raise_for_status()
    lines = [ln for ln in resp.text.strip().split("\n") if ln != ""]

    if not lines:
        raise HTTPException(502, "Usługa ULDK nie zwróciła odpowiedzi.")

    first = lines[0].strip()
    if first.startswith("-1") or first == "0":
        raise HTTPException(404, f"Nie znaleziono działki dla identyfikatora '{parcel_id}'.")

    try:
        count = int(first)
        data_line = lines[1] if count >= 1 and len(lines) > 1 else None
    except ValueError:
        count = None
        data_line = first

    if not data_line:
        raise HTTPException(404, f"Brak danych geometrii dla '{parcel_id}'.")

    fields = [f.strip() for f in data_line.split("|")]
    if len(fields) < 6:
        raise HTTPException(502, f"Nieoczekiwany format odpowiedzi ULDK: {data_line}")

    teryt_id, geom_raw, voivodeship, county, commune, parcel_no = fields[:6]
    geometry = _parse_uldk_geometry(geom_raw)

    return {
        "teryt_id": teryt_id,
        "voivodeship_code": teryt_id[:2] if teryt_id[:2].isdigit() else None,
        "voivodeship_name": voivodeship,
        "county": county,
        "commune": commune,
        "parcel_no": parcel_no,
        "geometry": geometry,
        "multiple_found": bool(count and count > 1),
        "found_count": count,
    }


async def get_buildings_on_parcel(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    centroid = geometry.centroid
    query = (
        f'[out:json][timeout:25];(way(around:60,{centroid.y},{centroid.x})["building"];);'
        f"out geom;"
    )
    try:
        data = await _overpass_query(client, query)
    except Exception as exc:
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {exc}"}
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
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {exc}"}

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
            return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {exc}"}

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
        return {"status": "error", "message": f"Usługa SOPO PIG-PIB niedostępna: {exc}"}


async def wms_get_feature_info(
    client: httpx.AsyncClient, base_url: str, layers: str,
    x_2180: float, y_2180: float, half_extent_m: float = 12.0,
) -> httpx.Response:
    bbox = (
        f"{x_2180 - half_extent_m},{y_2180 - half_extent_m},"
        f"{x_2180 + half_extent_m},{y_2180 + half_extent_m}"
    )
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
        "LAYERS": layers, "QUERY_LAYERS": layers, "STYLES": "",
        "SRS": "EPSG:2180", "BBOX": bbox, "WIDTH": "101", "HEIGHT": "101",
        "X": "50", "Y": "50", "INFO_FORMAT": "text/html",
        "FEATURE_COUNT": "10", "FORMAT": "image/png",
    }
    return await client.get(base_url, params=params)


def _feature_info_has_data(text: str) -> bool:
    if not text or len(text) < 25:
        return False
    return not re.search(
        r"no features|brak (danych|obiekt|wyniku)|nie udostępnia danych|search returned no results",
        text, re.IGNORECASE,
    )


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
    return {"status": "ok", "utilities": results}


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
        return {"status": "error", "message": f"Usługa niedostępna: {exc}"}


async def get_waterways(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    centroid = geometry.centroid
    query = (
        f'[out:json][timeout:25];(way(around:400,{centroid.y},{centroid.x})["waterway"];);'
        f"out geom;"
    )
    try:
        data = await _overpass_query(client, query)
    except Exception as exc:
        return {"status": "error", "message": f"Usługa OpenStreetMap/Overpass niedostępna: {exc}"}

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


async def get_flood_zone(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    bbox = f"{x_2180-30},{y_2180-30},{x_2180+30},{y_2180+30}"
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:2180", "LAYERS": "16,17", "QUERY_LAYERS": "16,17",
        "BBOX": bbox, "WIDTH": "101", "HEIGHT": "101", "I": "50", "J": "50",
        "FEATURE_COUNT": "10", "INFO_FORMAT": "application/geojson",
    }
    try:
        resp = await client.get(ISOK_MZP20_URL, params=params, timeout=30.0)
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
        resp = await client.get(f"{PODTOPIENIA_BASE_URL}/identify", params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        return {"status": "ok", "at_risk": len(data.get("results", [])) > 0}
    except Exception as exc:
        return {"status": "error", "message": f"Usługa PIG-PIB niedostępna: {exc}"}


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
    resp = await client.get(url, params=params, follow_redirects=True, timeout=15.0)
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
        return {"status": "error", "message": f"Usługa {source_label} niedostępna: {exc}"}

    if not has_plan_visually:
        return None

    try:
        resp = await asyncio.wait_for(
            wms_get_feature_info(client, url, layer, x_2180, y_2180, half_extent_m=15.0),
            timeout=12.0,
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


def get_gunb_link(parcel_no: str) -> str:
    last_segment = parcel_no.split(".")[-1] if "." in parcel_no else parcel_no
    return f"{GUNB_SEARCH_URL}?ew_parcel={last_segment}"


def get_geoportal_link(teryt_id: str) -> str:
    """Deep link to the specific parcel on Polska mapa / Geoportal Krajowy.
    Confirmed live: the modern 'imapnext' viewer no longer supports this
    (identifyParcel is absent from its current JS bundle — dead parameter,
    confirmed by inspecting the live main.js), but the older, still-live
    'imap' viewer at mapy.geoportal.gov.pl/imap/ DOES actively handle it —
    confirmed by finding the actual handling code
    (`checkParametersExist()` checking `url.indexOf("identifyparcel")`)
    directly in that page's live inline script. This matches GUGiK's own
    2018 official announcement of this exact feature
    (mapy.geoportal.gov.pl/imap/?identifyParcel=<TERYT_ID>)."""
    return f"https://mapy.geoportal.gov.pl/imap/?identifyParcel={teryt_id}"


def get_emapa_link(teryt_id: str) -> str:
    """Deep link to the specific parcel on polska.e-mapa.net (Geo-System's
    portal, independent of GUGiK's own geoportal). Confirmed via the site's
    own live 'share view' feature — screenshotted by the user, generating
    exactly 'https://polska.e-mapa.net?identifyParcel=<TERYT_ID>' — and
    separately verified live (HTTP 200, no redirect) for our own test
    parcel."""
    return f"https://polska.e-mapa.net?identifyParcel={teryt_id}"


def estimate_value(area_m2: float, voivodeship_code: Optional[str], buildings: list[dict]) -> dict[str, Any]:
    price = GUS_PRICE_PER_M2.get(voivodeship_code) if voivodeship_code else None
    if price is None:
        return {"status": "error", "message": "Nie udało się ustalić województwa dla wyceny statystycznej."}

    land_value = round(area_m2 * price, 2)
    buildings_footprint = sum(b["area_m2"] for b in buildings)
    buildings_value = round(buildings_footprint * ROUGH_BUILD_COST_PER_M2, 2) if buildings else 0.0

    return {
        "status": "ok",
        "land": {
            "area_m2": round(area_m2, 2),
            "price_per_m2": price,
            "voivodeship_name": VOIVODESHIP_NAMES.get(voivodeship_code),
            "value_pln": land_value,
        },
        "buildings": {
            "footprint_area_m2": round(buildings_footprint, 1),
            "building_count": len(buildings),
            "assumed_cost_per_m2": ROUGH_BUILD_COST_PER_M2,
            "value_pln": buildings_value,
        } if buildings else None,
    }


async def try_numbered_precinct_variants(
    client: httpx.AsyncClient, name: str, parcel_no: str, max_n: int = 20
) -> list[dict[str, str]]:
    """Some larger towns are split into several numbered cadastral precincts
    named '{Town}-{n}' (confirmed live: Bochnia is split into 'Bochnia-1'
    through at least 'Bochnia-9'; the city itself has NO obręb literally
    named just 'Bochnia' — ULDK returns zero results for that). This doesn't
    depend on the gmina geocoder at all (which has a separate, confirmed gap:
    it fails to surface single-city gminas like 'Miasto Bochnia' for a bare
    gmina-name query) — it just tries the naming pattern directly against
    ULDK's own free-text obręb search, which already returns full multi-match
    candidate data on its own."""
    async def try_variant(n: int) -> list[dict[str, str]]:
        try:
            return await uldk_search_candidates(client, f"{name}-{n} {parcel_no}")
        except Exception:
            return []

    results = await asyncio.gather(*[try_variant(n) for n in range(1, max_n + 1)])
    combined: list[dict[str, str]] = []
    for hits in results:
        combined.extend(hits)
    return combined


@app.get("/api/search-by-parcel-size")
async def search_by_parcel_size(
    place: str = Query(default=""),
    area_m2: Optional[float] = Query(default=None),
    width_m: Optional[float] = Query(default=None),
    length_m: Optional[float] = Query(default=None),
    dims_as_maximum: bool = Query(default=False),
):
    """'Szukaj działki' tab: one universal search. Given a locality name and
    ANY combination of a target area (m²) and/or width/length (m), finds up
    to 10 real nearby parcels matching ALL supplied criteria, ranked by
    combined closeness across whichever criteria were given. A single side
    length (just width, or just length) is accepted as long as area is also
    given. If dims_as_maximum=true, width AND length are both required and
    treated as a hard ceiling (parcel's sides must each be ≤ the given
    value) rather than an approximate ±20% target — see
    search_parcels_universal for the full pipeline."""
    place = place.strip()
    if len(place) < 2:
        raise HTTPException(400, "Podaj nazwę miejscowości.")

    have_area = area_m2 is not None and area_m2 > 0
    have_width = width_m is not None and width_m > 0
    have_length = length_m is not None and length_m > 0
    have_any_dim = have_width or have_length

    if not have_area and not have_any_dim:
        raise HTTPException(
            400,
            "Podaj powierzchnię (m²) i/lub szerokość i/lub długość działki (m) — przynajmniej jedno z tych kryteriów.",
        )
    if dims_as_maximum and not (have_width and have_length):
        raise HTTPException(
            400,
            "Przy wyszukiwaniu 'nie większa niż' podaj oba wymiary — maksymalną szerokość i maksymalną długość.",
        )
    if have_any_dim and not have_area and not (have_width and have_length):
        raise HTTPException(
            400,
            "Sam jeden wymiar (bez powierzchni) to za mało — podaj też powierzchnię, albo drugi wymiar.",
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialkiGIS/2.0"}) as client:
        result = await search_parcels_universal(
            client, place,
            target_area_m2=area_m2 if have_area else None,
            target_width_m=width_m if have_width else None,
            target_length_m=length_m if have_length else None,
            dims_as_maximum=dims_as_maximum,
        )

    if result["status"] != "ok":
        raise HTTPException(502, result["message"])
    return result


@app.get("/api/resolve-address")
async def resolve_address(query: str = Query(default="")):
    """Search by street address (e.g. 'Kraków, Floriańska 5') instead of by
    place name + parcel number. Same response shape as /api/resolve (single
    resolved match, or a list of candidates to disambiguate), so the
    frontend reuses the exact same picker/switcher UI — just fed from a
    different, address-based lookup path (geocode -> GetParcelByXY) rather
    than the name+number -> obręb path."""
    query = query.strip()
    if len(query) < 5:
        raise HTTPException(400, "Podaj adres (miejscowość, ulica i numer).")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialkiGIS/2.0"}) as client:
        candidates = await resolve_address_to_parcels(client, query)

    if not candidates:
        raise HTTPException(
            404,
            f"Nie znaleziono działki dla adresu '{query}'. Sprawdź pisownię miejscowości i ulicy, "
            "i podaj numer domu — samo miasto lub sama ulica bez numeru może dać zbyt wiele wyników.",
        )
    if len(candidates) == 1:
        return {"resolved": True, "teryt_id": candidates[0]["teryt_id"]}
    return {"resolved": False, "candidates": candidates}


@app.get("/api/resolve")
async def resolve_parcel(query: str = Query(default="")):
    """Given free text like 'Limanowa 123' or a full TERYT id, returns either
    a single resolved match (frontend proceeds straight to /api/analyze) or a
    list of candidates to disambiguate (frontend shows a picker).

    Three-stage lookup:
      1. Direct ULDK free-text 'ObrębName Numer' search (exact obręb name).
      2. If that finds nothing AND the query looks like 'Name Number': treat
         "Name" as a gmina/village name, resolve it to gmina TERYT code(s)
         via GUGiK's official geocoder (capap.gugik.gov.pl), then brute-force
         scan every obręb in that gmina for the given parcel number. This is
         what resolves cases like 'Milówka 2994/4', where the parcel is
         actually registered under a neighbouring village's obręb ('Laliki')
         within the same gmina — confirmed live.
      3. If that STILL finds nothing: try the '{Name}-{n}' numbered-precinct
         naming pattern directly via ULDK (see try_numbered_precinct_variants)
         — this is what resolves cases like 'Bochnia 6312/1', a city whose
         own gmina isn't surfaced by the geocoder used in stage 2 at all."""
    query = query.strip()
    if len(query) < 3:
        raise HTTPException(400, "Podaj nazwę miejscowości i numer działki (lub pełny identyfikator TERYT).")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialkiGIS/2.0"}) as client:
        candidates = await uldk_search_candidates(client, query)

        if not candidates and " " in query:
            name_part, parcel_part = query.rsplit(" ", 1)
            name_part = name_part.strip()
            if name_part and parcel_part:
                gminas = await geocode_gmina_candidates(client, name_part)
                scan_results = await asyncio.gather(
                    *[scan_gmina_obreby_for_parcel(client, g["gmina_prefix"], parcel_part) for g in gminas]
                )
                for hits in scan_results:
                    candidates.extend(hits)

                if not candidates:
                    candidates = await try_numbered_precinct_variants(client, name_part, parcel_part)

    if not candidates:
        raise HTTPException(
            404,
            f"Nie znaleziono działki dla '{query}'. Sprawdzono dokładną nazwę obrębu oraz "
            "przeszukano wszystkie obręby gminy o tej nazwie (jeśli taka gmina istnieje) — "
            "działka o tym numerze nie została znaleziona w żadnym z nich. Sprawdź numer "
            "działki i nazwę miejscowości.",
        )
    if len(candidates) == 1:
        return {"resolved": True, "teryt_id": candidates[0]["teryt_id"]}
    return {"resolved": False, "candidates": candidates}


@app.get("/api/analyze")
async def analyze(parcel_id: str = Query(default="")):
    parcel_id = parcel_id.strip()
    if len(parcel_id) < 3:
        raise HTTPException(400, "Podaj poprawny numer działki (identyfikator TERYT).")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialki/2.0"}) as client:
        parcel = await uldk_get_parcel(client, parcel_id)
        geometry = parcel["geometry"]

        centroid = geometry.centroid
        cx2180, cy2180 = to_2180.transform(centroid.x, centroid.y)

        area_m2, _perimeter_m = geod.geometry_area_perimeter(geometry)
        area_m2 = abs(area_m2)

        results = await asyncio.gather(
            check_landslide(client, geometry),
            check_utilities(client, cx2180, cy2180),
            get_cadastre_basic(client, cx2180, cy2180),
            get_zoning(client, cx2180, cy2180),
            get_buildings_on_parcel(client, geometry),
            get_waterways(client, geometry),
            get_flood_zone(client, cx2180, cy2180),
            get_waterlogging_risk(client, geometry),
            get_nearest_municipal_road(client, geometry),
        )
    (landslide, utilities, cadastre, zoning, buildings,
     waterways, flood_zone, waterlogging, nearest_road) = results

    building_list = buildings.get("buildings", []) if buildings.get("status") == "ok" else []
    valuation = estimate_value(area_m2, parcel["voivodeship_code"], building_list)
    gunb_link = get_gunb_link(parcel["parcel_no"])
    geoportal_link = get_geoportal_link(parcel["teryt_id"])
    emapa_link = get_emapa_link(parcel["teryt_id"])

    return {
        "parcel": {
            "teryt_id": parcel["teryt_id"],
            "voivodeship": parcel["voivodeship_name"],
            "county": parcel["county"],
            "commune": parcel["commune"],
            "parcel_no": parcel["parcel_no"],
            "multiple_found": parcel["multiple_found"],
            "geoportal_link": geoportal_link,
            "emapa_link": emapa_link,
        },
        "geometry_geojson": mapping(geometry),
        "centroid": {"lat": centroid.y, "lon": centroid.x},
        "area_m2": round(area_m2, 2),
        "landslide": landslide,
        "utilities": utilities,
        "cadastre": cadastre,
        "buildings": buildings,
        "zoning": zoning,
        "hydrology": {
            "waterways": waterways,
            "flood_zone": flood_zone,
            "waterlogging": waterlogging,
        },
        "nearest_road": nearest_road,
        "permits": {"gunb_link": gunb_link},
        "valuation": valuation,
        "map_layers": {
            "egib": {"url": KIEG_URL, "layers": "dzialki,numery_dzialek,budynki"},
            "mpzp": {"url": KIMPZP_URL, "layers": "plany"},
            "app": {"url": KIAPP_URL, "layers": "app"},
        },
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/service-worker.js")
async def service_worker():
    return FileResponse("static/service-worker.js", media_type="application/javascript")
