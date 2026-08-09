"""
Analiza Działki GIS — backend
==============================
FastAPI application that, given a Polish TERYT parcel identifier, orchestrates
calls to several free, official Polish government geo-services and returns a
consolidated JSON report:

  KROK 0 — GUGiK ULDK          -> parcel geometry + TERYT resolution
  KROK 1 — PIG-PIB SOPO        -> landslide risk (ArcGIS REST 'query', intersects)
  KROK 2 — GUGiK KI-WMS x3     -> utilities (KIUT), cadastre/buildings (KIEG),
                                  zoning / MPZP (KIMPZP) via GetFeatureInfo
  KROK 4 — GUS-style estimator -> area * regional avg price/m^2

All three KROK-2 calls and the KROK-1 call are fired concurrently with
asyncio.gather, each wrapped so that one failing county/gmina service does not
take down the whole response.
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
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

# --------------------------------------------------------------------------
# Real, verified endpoints (see chat message for how these were confirmed)
# --------------------------------------------------------------------------

ULDK_URL = "https://uldk.gugik.gov.pl/"

# PIG-PIB SOPO landslide layers are served by an ArcGIS Server instance.
# Service + layer IDs confirmed live against the server's own REST directory
# (.../rest/services/geozagrozenia/sopo_obszary/MapServer?f=json):
#   layer 14 = "SOPO – osuwiska"        (documented landslide polygons)
#   layer 12 = "SOPO – tereny zagrożone" (threatened-area polygons)
SOPO_BASE_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/geozagrozenia/sopo_obszary/MapServer"
SOPO_LAYERS = {14: "osuwisko", 12: "teren zagrożony"}

# GUGiK national WMS aggregation services ("Krajowa Integracja ...")
KIUT_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"
KIEG_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
KIMPZP_URL = (
    "https://mapy.geoportal.gov.pl/wss/ext/"
    "KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
)

# Layers confirmed queryable via each service's own GetCapabilities response.
KIUT_LAYERS = "przewod_wodociagowy,przewod_kanalizacyjny,przewod_gazowy,przewod_elektroenergetyczny"
# NOTE: "budynki" is published as queryable="0" in KIEG's capabilities, so it
# cannot answer GetFeatureInfo directly. We use the sub-layers that ARE
# queryable and still describe the plot: parcels, land-use "kontury"
# (classification contours) and "uzytki" (land use categories).
KIEG_LAYERS = "dzialki,kontury,uzytki"
KIMPZP_LAYERS = "plany"

HTTP_TIMEOUT = 20.0

# --------------------------------------------------------------------------
# GUS-style average land price per m^2, by 2-digit TERYT voivodeship code.
# These are ILLUSTRATIVE baseline figures for a statistical estimate only —
# NOT an official valuation. Update periodically from GUS's published
# "Ceny gruntów" report (stat.gov.pl) to keep them current.
# --------------------------------------------------------------------------
GUS_PRICE_PER_M2: dict[str, float] = {
    "02": 55.0,   # dolnośląskie
    "04": 28.0,   # kujawsko-pomorskie
    "06": 30.0,   # lubelskie
    "08": 24.0,   # lubuskie
    "10": 38.0,   # łódzkie
    "12": 60.0,   # małopolskie
    "14": 70.0,   # mazowieckie
    "16": 26.0,   # opolskie
    "18": 27.0,   # podkarpackie
    "20": 22.0,   # podlaskie
    "22": 58.0,   # pomorskie
    "24": 45.0,   # śląskie
    "26": 25.0,   # świętokrzyskie
    "28": 21.0,   # warmińsko-mazurskie
    "30": 42.0,   # wielkopolskie
    "32": 33.0,   # zachodniopomorskie
}

VOIVODESHIP_NAMES: dict[str, str] = {
    "02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie",
    "08": "lubuskie", "10": "łódzkie", "12": "małopolskie",
    "14": "mazowieckie", "16": "opolskie", "18": "podkarpackie",
    "20": "podlaskie", "22": "pomorskie", "24": "śląskie",
    "26": "świętokrzyskie", "28": "warmińsko-mazurskie",
    "30": "wielkopolskie", "32": "zachodniopomorskie",
}

# --------------------------------------------------------------------------
# App setup
# --------------------------------------------------------------------------

app = FastAPI(title="Analiza Działki GIS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

geod = Geod(ellps="WGS84")
to_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


# --------------------------------------------------------------------------
# KROK 0 — GUGiK ULDK: resolve parcel identifier -> geometry + TERYT
# --------------------------------------------------------------------------

_EWKT_SRID_PREFIX = re.compile(r"^SRID=\d+;\s*", re.IGNORECASE)


def _parse_uldk_geometry(raw: str) -> BaseGeometry:
    """ULDK's geom_wkt field actually returns EWKT, e.g. 'SRID=4326;POLYGON(...)'.
    Shapely's WKT reader doesn't understand the SRID prefix, so we strip it.
    As a defensive fallback (in case of an unexpected WKB-hex reply instead)
    we also try decoding as (E)WKB hex."""
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

    # GetParcelByIdOrNr: first line is the number of parcels found — but on
    # error/not-found it returns a line starting with "-1" (sometimes with
    # trailing explanatory text, e.g. "-1 brak wyników") rather than a bare
    # "-1", so check with startswith rather than equality.
    first = lines[0].strip()
    if first.startswith("-1") or first == "0":
        raise HTTPException(
            404, f"Nie znaleziono działki dla identyfikatora '{parcel_id}'."
        )

    try:
        count = int(first)
        data_line = lines[1] if count >= 1 and len(lines) > 1 else None
    except ValueError:
        # Some ULDK request variants don't prefix a count line.
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


# --------------------------------------------------------------------------
# KROK 1 — PIG-PIB SOPO landslide risk (ArcGIS REST 'query', spatial intersect)
# --------------------------------------------------------------------------

async def check_landslide(client: httpx.AsyncClient, geometry: BaseGeometry) -> dict[str, Any]:
    """The 'query' capability is disabled on the individual SOPO feature
    layers (confirmed live: returns 'Requested operation is not supported by
    this service'), but 'identify' is enabled and works correctly for spatial
    intersection against an arbitrary polygon — verified against a known
    Carpathian landslide polygon and a known-clear control area."""
    rings = [list(coord) for coord in geometry.exterior.coords]
    minx, miny, maxx, maxy = geometry.bounds
    params = {
        "geometry": json.dumps(
            {"rings": [rings], "spatialReference": {"wkid": 4326}}
        ),
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
        return {
            "status": "ok",
            "has_landslide": len(results) > 0,
            "matched_categories": matched,
        }
    except Exception as exc:
        return {"status": "error", "message": f"Usługa SOPO PIG-PIB niedostępna: {exc}"}


# --------------------------------------------------------------------------
# KROK 2 — GUGiK WMS GetFeatureInfo (utilities / cadastre / zoning)
# --------------------------------------------------------------------------

def _clean_feature_info_text(raw_html: str) -> str:
    """MapServer/county WMS GetFeatureInfo responses are usually small HTML
    tables whose structure varies per source. We strip tags and collapse
    whitespace into a single readable summary string."""
    if not raw_html or not raw_html.strip():
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" | ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\|\s*){2,}", "| ", text).strip(" |")
    return text


async def wms_get_feature_info(
    client: httpx.AsyncClient,
    base_url: str,
    layers: str,
    x_2180: float,
    y_2180: float,
    half_extent_m: float = 3.0,
) -> dict[str, Any]:
    bbox = (
        f"{x_2180 - half_extent_m},{y_2180 - half_extent_m},"
        f"{x_2180 + half_extent_m},{y_2180 + half_extent_m}"
    )
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": layers,
        "QUERY_LAYERS": layers,
        "STYLES": "",
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "101",
        "HEIGHT": "101",
        "X": "50",
        "Y": "50",
        "INFO_FORMAT": "text/html",
        "FEATURE_COUNT": "10",
        "FORMAT": "image/png",
    }
    try:
        resp = await client.get(base_url, params=params)
        resp.raise_for_status()
        text = _clean_feature_info_text(resp.text)
        if not text:
            return {"status": "ok", "summary": "Brak danych w tej lokalizacji."}
        return {"status": "ok", "summary": text}
    except Exception as exc:
        return {"status": "error", "message": f"Usługa niedostępna: {exc}"}


# --------------------------------------------------------------------------
# KROK 4 — statistical GUS-style value estimator
# --------------------------------------------------------------------------

def gus_estimate(area_m2: float, voivodeship_code: Optional[str]) -> dict[str, Any]:
    price = GUS_PRICE_PER_M2.get(voivodeship_code) if voivodeship_code else None
    if price is None:
        return {
            "status": "error",
            "message": "Nie udało się ustalić województwa dla wyceny statystycznej.",
        }
    value = round(area_m2 * price, 2)
    return {
        "status": "ok",
        "area_m2": round(area_m2, 2),
        "price_per_m2": price,
        "voivodeship_name": VOIVODESHIP_NAMES.get(voivodeship_code),
        "estimated_value_pln": value,
    }


# --------------------------------------------------------------------------
# Orchestration endpoint
# --------------------------------------------------------------------------

@app.get("/api/analyze")
async def analyze(parcel_id: str = Query(default="")):
    parcel_id = parcel_id.strip()
    if len(parcel_id) < 3:
        raise HTTPException(400, "Podaj poprawny numer działki (identyfikator TERYT).")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialki/1.0"}) as client:
        parcel = await uldk_get_parcel(client, parcel_id)
        geometry = parcel["geometry"]

        centroid = geometry.centroid
        cx2180, cy2180 = to_2180.transform(centroid.x, centroid.y)

        # geodesic area in m^2 from the WGS84 polygon (accurate, no 2nd call)
        area_m2, _perimeter_m = geod.geometry_area_perimeter(geometry)
        area_m2 = abs(area_m2)

        landslide_task = check_landslide(client, geometry)
        utilities_task = wms_get_feature_info(client, KIUT_URL, KIUT_LAYERS, cx2180, cy2180)
        cadastre_task = wms_get_feature_info(client, KIEG_URL, KIEG_LAYERS, cx2180, cy2180)
        zoning_task = wms_get_feature_info(client, KIMPZP_URL, KIMPZP_LAYERS, cx2180, cy2180)

        landslide, utilities, cadastre, zoning = await asyncio.gather(
            landslide_task, utilities_task, cadastre_task, zoning_task
        )

    gus = gus_estimate(area_m2, parcel["voivodeship_code"])

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
        "zoning": zoning,
        "gus_estimate": gus,
    }


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/service-worker.js")
async def service_worker():
    # Served from root (not /static/) so its default scope is "/" and it can
    # control the whole app — Chrome requires this for install eligibility.
    return FileResponse("static/service-worker.js", media_type="application/javascript")
