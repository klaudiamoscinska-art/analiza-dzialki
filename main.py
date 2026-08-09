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
import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pyproj import Geod, Transformer
from shapely import wkb, wkt
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

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
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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

    fields = data_line.split("|")
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
        resp = await client.post(OVERPASS_URL, data={"data": query}, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
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
        tag = el.get("tags", {}).get("building", "yes")
        buildings.append({
            "label": OSM_BUILDING_LABELS.get(tag, f"budynek ({tag})"),
            "area_m2": round(abs(area_m2), 1),
            "fully_within_parcel": geometry.contains(poly),
            "osm_id": el.get("id"),
        })

    return {
        "status": "ok",
        "found": "yes" if buildings else "no",
        "buildings": buildings,
        "source": "OpenStreetMap (Overpass API), dopasowane przestrzennie do granic działki",
    }


async def check_landslide(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
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
    async def one(label_key: str, label: str, layer: str) -> dict[str, Any]:
        try:
            resp = await wms_get_feature_info(client, KIUT_URL, layer, x_2180, y_2180)
            text = _clean_feature_info_text(resp.text)
            return {"key": label_key, "label": label, "present": _feature_info_has_data(text)}
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
        resp = await client.post(OVERPASS_URL, data={"data": query}, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
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


async def get_zoning(client: httpx.AsyncClient, x_2180: float, y_2180: float) -> dict[str, Any]:
    try:
        resp = await wms_get_feature_info(client, KIMPZP_URL, KIMPZP_LAYERS, x_2180, y_2180, half_extent_m=15.0)
        table = _parse_feature_info_table(resp.text)
        text = _clean_feature_info_text(resp.text)
        has_plan = _feature_info_has_data(text)
        return {"status": "ok", "found": "yes" if has_plan else "no", "table": table}
    except httpx.TimeoutException:
        # Confirmed live: this gmina-federated service can take >25s to
        # respond for some locations. Match the reference app's own wording.
        return {
            "status": "error",
            "message": "Serwer MPZP nie odpowiedział w wyznaczonym czasie — spróbuj ponownie za chwilę.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Usługa MPZP niedostępna: {exc}"}


def get_gunb_link(parcel_no: str) -> str:
    last_segment = parcel_no.split(".")[-1] if "." in parcel_no else parcel_no
    return f"{GUNB_SEARCH_URL}?ew_parcel={last_segment}"


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
        )
    (landslide, utilities, cadastre, zoning, buildings,
     waterways, flood_zone, waterlogging) = results

    building_list = buildings.get("buildings", []) if buildings.get("status") == "ok" else []
    valuation = estimate_value(area_m2, parcel["voivodeship_code"], building_list)
    gunb_link = get_gunb_link(parcel["parcel_no"])

    return {
        "parcel": {
            "teryt_id": parcel["teryt_id"],
            "voivodeship": parcel["voivodeship_name"],
            "county": parcel["county"],
            "commune": parcel["commune"],
            "parcel_no": parcel["parcel_no"],
            "multiple_found": parcel["multiple_found"],
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
        "permits": {"gunb_link": gunb_link},
        "valuation": valuation,
        "map_layers": {
            "egib": {"url": KIEG_URL, "layers": "dzialki,numery_dzialek,budynki"},
            "mpzp": {"url": KIMPZP_URL, "layers": "plany"},
        },
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/service-worker.js")
async def service_worker():
    return FileResponse("static/service-worker.js", media_type="application/javascript")
