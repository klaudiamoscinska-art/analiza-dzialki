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

Map layers (frontend, toggleable, static/app.js):
  - EGiB (parcels + parcel numbers + buildings) — WMS tile overlay, KIEG;
                                                   flat checkbox in
                                                   L.control.layers
  - Media / uzbrojenie terenu (GESUT)           — WMS tile overlay, KIUT;
                                                   combined toggle + 6
                                                   per-type sub-toggles
                                                   (water, sewage, gas,
                                                   power, heating, telecom)
  - Plany zagospodarowania                      — combined L.layerGroup of
                                                   MPZP (legacy) + Rejestr
                                                   Urbanistyczny (new), + 2
                                                   per-source sub-toggles
  GESUT and Plany zagospodarowania are NOT flat L.control.layers entries —
  each is its own checkbox+<details> row appended directly to the control's
  container (see addLayerGroupRow() in app.js), so their subcategories
  expand right under their own checkbox instead of appearing as unrelated
  flat items (first attempt did that and looked broken — fixed same day).
  SOPO/hydrogeologia are NOT offered as map tile overlays: cbdgmapa.pgi.gov.pl
  is behind an Incapsula bot-mitigation WAF that intermittently blocks plain
  HTTP requests (confirmed live). Panel-only "identify" calls (unaffected by
  the WAF) are used instead of visual overlays for those two sources.

Module layout: this file only wires up the FastAPI app and the four HTTP
routes. Each section above lives in its own module under services/ (named
after what it wraps, not the section number), with shared constants in
config.py, geometry/text helpers in geo_utils.py, and generic HTTP
retry/fallback helpers in http_utils.py. See HANDOFF.md for the full
per-service notes (what's confirmed live, known dead ends, etc).
"""
import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import mapping

from config import HTTP_TIMEOUT, KIAPP_URL, KIEG_URL, KIMPZP_URL
from geo_utils import geod, to_2180
from services.cadastre import get_buildings_on_parcel, get_cadastre_basic
from services.geocoding import (
    geocode_gmina_candidates, geocode_powiat_gmina_prefixes, resolve_address_to_parcels,
)
from services.hazards import check_landslide, get_flood_zone, get_waterlogging_risk
from services.nearby_features import get_nearest_municipal_road, get_waterways
from services.uldk import (
    find_parcel_by_id_direct, scan_gmina_obreby_for_parcel, try_numbered_precinct_variants,
    uldk_get_parcel, uldk_search_candidates,
)
from services.utilities import check_utilities
from services.valuation import estimate_value, get_emapa_link, get_geoportal_link, get_gunb_link
from services.wfs_search import scan_wfs_for_parcel_number, search_parcels_universal
from services.zoning import get_zoning

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



@app.get("/api/search-by-parcel-size")
async def search_by_parcel_size(
    place: str = Query(default=""),
    area_m2: Optional[float] = Query(default=None),
    width_m: Optional[float] = Query(default=None),
    length_m: Optional[float] = Query(default=None),
    dims_as_maximum: bool = Query(default=False),
):
    """'Szukaj działki' tab: one universal search. Given a locality name and
    ANY combination of a target area (m²) and/or width/length (m), finds
    ALL real nearby parcels within ±10% matching ALL supplied criteria (no
    result-count cap — see search_parcels_universal), ranked by combined
    closeness across whichever criteria were given. A single side length
    (just width, or just length) is accepted as long as area is also
    given. If dims_as_maximum=true, width AND length are both required and
    treated as a hard ceiling (parcel's sides must each be ≤ the given
    value) rather than an approximate ±10% target — see
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

    Multi-stage lookup:
      1. Direct ULDK free-text 'ObrębName Numer' search (exact obręb name),
         plus (2026-09-03) a GetParcelById retry if the query is itself a
         full TERYT id — see find_parcel_by_id_direct.
      2. If that finds nothing AND the query looks like 'Name Number': treat
         "Name" as a gmina/village name, resolve it to gmina TERYT code(s)
         via GUGiK's official geocoder (capap.gugik.gov.pl), then brute-force
         scan every obręb in that gmina for the given parcel number. This is
         what resolves cases like 'Milówka 2994/4', where the parcel is
         actually registered under a neighbouring village's obręb ('Laliki')
         within the same gmina — confirmed live.
      3. (2026-09-03) If THAT still finds nothing: for the same gmina
         candidates, try scan_wfs_for_parcel_number() instead of the
         ID-based scan above — enumerates real parcel geometries from the
         powiat's own WFS server and resolves each by spatial GetParcelByXY,
         rather than an ID-indexed lookup. Added after Klaudia confirmed a
         real, independently-verified parcel (121505_2.0001.636/3) was not
         found by ANY ID-based ULDK query, AND reported seeing the identical
         symptom on a different provider (polska.e-mapa.net) for an
         unrelated parcel — findable only after browsing to a neighbouring
         parcel first. This automates exactly that "browse to a neighbour"
         path. See scan_wfs_for_parcel_number's docstring for detail.
      4. If that STILL finds nothing: try the '{Name}-{n}' numbered-precinct
         naming pattern directly via ULDK (see try_numbered_precinct_variants)
         — this is what resolves cases like 'Bochnia 6312/1', a city whose
         own gmina isn't surfaced by the geocoder used in stage 2 at all.
      5. (2026-09-03, Klaudia's request) If that STILL finds nothing: treat
         "Name" as a POWIAT name instead of a gmina — reuses
         geocode_powiat_gmina_prefixes() (built for 'Szukaj działki'), then
         repeats stages 2+3 (ID scan, then WFS scan) for every gmina in that
         powiat. Covers the case where the obręb name someone types (e.g.
         'Łętownia', a real village but not itself a gmina) doesn't share a
         name with its gmina, but the powiat name IS known — see 'Fallback
         GetParcelById' in HANDOFF.md. Bounded but not small: a powiat can
         have several gminas, each scanned concurrently — acceptable for a
         manual, one-off search, not something to call in a hot loop."""
    query = query.strip()
    if len(query) < 3:
        raise HTTPException(400, "Podaj nazwę miejscowości i numer działki (lub pełny identyfikator TERYT).")

    async def scan_gminas_both_ways(gminas: list[dict], parcel_part: str) -> list[dict]:
        """Stages 2+3 above, applied to any list of gmina candidates (used
        for both the gmina-name and powiat-name pathways): ID-based obręb
        scan first (cheap, usually sufficient), then — only if that finds
        nothing — the WFS geometry-based scan (only for candidates with
        known coordinates; see geocode_gmina_candidates/
        geocode_powiat_gmina_prefixes)."""
        id_scan_results = await asyncio.gather(
            *[scan_gmina_obreby_for_parcel(client, g["gmina_prefix"], parcel_part) for g in gminas]
        )
        hits = [h for r in id_scan_results for h in r]
        if hits:
            return hits

        geocoded = [g for g in gminas if "lon" in g]
        wfs_scan_results = await asyncio.gather(
            *[
                scan_wfs_for_parcel_number(client, g["lon"], g["lat"], g["gmina_prefix"], parcel_part)
                for g in geocoded
            ]
        )
        return [h for r in wfs_scan_results for h in r]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialkiGIS/2.0"}) as client:
        candidates = await uldk_search_candidates(client, query)

        if not candidates and query.count(".") >= 2 and " " not in query:
            # Wygląda jak pełny identyfikator TERYT (gmina.obręb.numer) —
            # spróbuj bardziej bezpośredniego zapytania GetParcelById
            # zamiast tylko GetParcelByIdOrNr powyżej. Potwierdzone na żywo
            # 2026-09-03: prawdziwa, poprawna działka (zweryfikowana przez
            # polska.e-mapa.net) nie została znaleziona przez ByIdOrNr —
            # patrz find_parcel_by_id_direct.
            direct = await find_parcel_by_id_direct(client, query)
            if direct:
                candidates = [direct]

        if not candidates and " " in query:
            name_part, parcel_part = query.rsplit(" ", 1)
            name_part = name_part.strip()
            if name_part and parcel_part:
                gminas = await geocode_gmina_candidates(client, name_part)
                candidates.extend(await scan_gminas_both_ways(gminas, parcel_part))

                if not candidates:
                    candidates = await try_numbered_precinct_variants(client, name_part, parcel_part)

                if not candidates:
                    # Etap 5: "Name" mogła być powiatem, nie gminą (np.
                    # "suski 636/3") — przeszukaj obręby WSZYSTKICH gmin w
                    # tym powiecie, tym samym dwuetapowym (ID + WFS)
                    # skanem. Patrz docstring wyżej i HANDOFF.md.
                    powiat_gminas = await geocode_powiat_gmina_prefixes(client, name_part)
                    candidates.extend(await scan_gminas_both_ways(powiat_gminas, parcel_part))

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
