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
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import mapping

from config import (
    HTTP_TIMEOUT, KIAPP_URL, KIEG_URL, KIMPZP_URL, MAX_CONCURRENT_SECTIONS, TIMEOUT_RESOLVE_BUDGET,
    TTL_AIR_QUALITY, TTL_BUILDINGS, TTL_CADASTRE, TTL_FLOOD_ZONE, TTL_LANDSLIDE, TTL_MINING_AREAS,
    TTL_NEAREST_ROAD, TTL_PROTECTED_AREAS, TTL_UTILITIES, TTL_WATERLOGGING, TTL_WATERWAYS, TTL_ZONING, logger,
)
from geo_utils import geod, to_2180
from http_utils import describe_exc
from services import cache
from services.air_quality import get_air_quality
from services.cadastre import get_buildings_on_parcel, get_cadastre_basic
from services.geocoding import (
    geocode_gmina_candidates, geocode_powiat_gmina_prefixes, resolve_address_to_parcels,
)
from services.due_diligence import build_due_diligence_checklist
from services.geology import check_mining_areas
from services.hazards import check_landslide, get_flood_zone, get_waterlogging_risk
from services.nature import get_protected_areas
from services.nearby_features import get_nearest_municipal_road, get_waterways
from services.uldk import (
    find_parcel_by_id_direct, scan_gmina_obreby_for_parcel, try_numbered_precinct_variants,
    uldk_get_parcel, uldk_search_candidates,
)
from services.utilities import check_utilities
from services.valuation import estimate_value, get_ekw_link, get_emapa_link, get_geoportal_link, get_gunb_link
from services.verdict import build_verdict
from services.wfs_search import scan_wfs_for_parcel_number, search_parcels_universal
from services.zoning import get_zoning


async def _timed(label: str, awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Logs the real wall-clock time each branch of /api/analyze's
    asyncio.gather actually takes — added 2026-09-04 (performance
    investigation, see HANDOFF.md and the 'Plan Pamięci Podręcznej'
    artifact). The timeouts in config.py are configured ceilings, not
    measurements; this is what turns them into real numbers, and is step
    one of that plan — everything else (which TTLs actually matter) reads
    from these logs, not from guesses."""
    start = time.monotonic()
    try:
        return await awaitable
    finally:
        logger.info("analyze: %s zajęło %.2fs", label, time.monotonic() - start)


# --------------------------------------------------------------------------
# Shared httpx.AsyncClient for the whole server lifetime — added 2026-09-04,
# performance optimization (a) from HANDOFF.md's "Propozycje optymalizacji
# wydajności". Every route used to open `async with httpx.AsyncClient(...)`
# per request, paying for a fresh TCP+TLS handshake to every one of the
# dozen external government/OSM hosts on EVERY /api/analyze call instead of
# reusing already-open keep-alive connections via httpx's own connection
# pool. httpx.AsyncClient is documented as safe for concurrent use by many
# requests at once, so one long-lived instance is the correct fix, not a
# workaround.
#
# Lazily created (not only inside the lifespan hook below) so it also works
# when route functions are called directly without going through FastAPI's
# startup event — e.g. tests/test_pure_logic.py calls main.resolve_parcel()
# directly, which never triggers `lifespan`.
# --------------------------------------------------------------------------
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": "AnalizaDzialkiGIS/2.0"})
    return _http_client


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _get_http_client()
    try:
        yield
    finally:
        global _http_client
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None


app = FastAPI(title="Analiza Działki GIS", lifespan=_lifespan)
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

    client = _get_http_client()
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

    client = _get_http_client()
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

    async def _do_resolve() -> list[dict]:
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

        client = _get_http_client()
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

        return candidates

    # Ta kaskada (do 5 etapów, każdy z własnymi wywołaniami sieciowymi,
    # niektóre do 45s) nie miała wcześniej ŻADNEGO łącznego budżetu czasu —
    # dodane 2026-09-04, patrz TIMEOUT_RESOLVE_BUDGET w config.py za pełne
    # wyjaśnienie (zgłoszone przez Klaudię jako niezrozumiały błąd sieci dla
    # zapytania "Korbielów 3917/5"). Bez tego appka czekała, aż zrobi to za
    # nią serwer proxy — który zwraca HTML zamiast JSON, mylący frontend.
    try:
        candidates = await asyncio.wait_for(_do_resolve(), timeout=TIMEOUT_RESOLVE_BUDGET)
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"Wyszukiwanie \"{query}\" trwało zbyt długo (ponad {int(TIMEOUT_RESOLVE_BUDGET)}s) — "
            "serwery gminne bywają wolne, zwłaszcza gdy appka musi sprawdzić wiele gmin naraz. "
            "Spróbuj ponownie za chwilę, albo — jeśli go znasz — wpisz pełny identyfikator "
            "TERYT działki zamiast nazwy miejscowości.",
        )

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


def _section_specs(
    client: httpx.AsyncClient, teryt_id: str, geometry, cx2180: float, cy2180: float, centroid,
) -> list[tuple[str, Awaitable[dict[str, Any]]]]:
    """The 12 concurrent /api/analyze branches, as (name, awaitable) pairs —
    factored out (2026-09-04, performance optimization (c) — see HANDOFF.md)
    so /api/analyze (asyncio.gather, all-at-once) and /api/analyze-stream
    (asyncio.as_completed, progressive SSE) share the exact same list
    instead of two copies that could silently drift apart (different TTL, a
    service added to one but not the other, etc).

    Each branch is wrapped in a shared `asyncio.Semaphore(MAX_CONCURRENT_SECTIONS)`
    (added 2026-09-04 — see config.py's comment on MAX_CONCURRENT_SECTIONS for
    the live evidence this addresses): confirmed live that firing all 12 at
    once overwhelms Render's free-tier single thread for data-heavy parcels,
    causing UNRELATED external services to each time out near their own
    configured limit. Only MAX_CONCURRENT_SECTIONS branches actually run
    their fetch at a time; the rest wait their turn on the semaphore instead
    of piling on top of an already-saturated event loop. One semaphore per
    call (per request), not a module-level global — this app effectively
    serves one analysis at a time in practice, and a per-request semaphore
    avoids any cross-request interaction that a shared global would risk."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_SECTIONS)

    async def _limited(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        async with sem:
            return await coro

    specs = [
        ("landslide", cache.get_or_fetch(
            "landslide", teryt_id, TTL_LANDSLIDE, lambda: check_landslide(client, geometry))),
        ("utilities", cache.get_or_fetch(
            "utilities", teryt_id, TTL_UTILITIES, lambda: check_utilities(client, cx2180, cy2180))),
        ("cadastre", cache.get_or_fetch(
            "cadastre", teryt_id, TTL_CADASTRE, lambda: get_cadastre_basic(client, cx2180, cy2180))),
        # Plan zagospodarowania — cache'owany od 2026-09-04 (optymalizacja
        # (b), krótki 7-dniowy TTL, patrz TTL_ZONING w config.py). Identyfikacja
        # działki (ULDK) świadomie wciąż NIE jest cache'owana.
        ("zoning", cache.get_or_fetch(
            "zoning", teryt_id, TTL_ZONING, lambda: get_zoning(client, cx2180, cy2180))),
        ("buildings", cache.get_or_fetch(
            "buildings", teryt_id, TTL_BUILDINGS, lambda: get_buildings_on_parcel(client, geometry))),
        ("waterways", cache.get_or_fetch(
            "waterways", teryt_id, TTL_WATERWAYS, lambda: get_waterways(client, geometry))),
        ("flood_zone", cache.get_or_fetch(
            "flood_zone", teryt_id, TTL_FLOOD_ZONE, lambda: get_flood_zone(client, cx2180, cy2180))),
        ("waterlogging", cache.get_or_fetch(
            "waterlogging", teryt_id, TTL_WATERLOGGING, lambda: get_waterlogging_risk(client, geometry))),
        ("nearest_road", cache.get_or_fetch(
            "nearest_road", teryt_id, TTL_NEAREST_ROAD, lambda: get_nearest_municipal_road(client, geometry))),
        ("protected_areas", cache.get_or_fetch(
            "protected_areas", teryt_id, TTL_PROTECTED_AREAS, lambda: get_protected_areas(client, cx2180, cy2180))),
        ("mining_areas", cache.get_or_fetch(
            "mining_areas", teryt_id, TTL_MINING_AREAS, lambda: check_mining_areas(client, geometry))),
        # TTL krótki (1h) — w przeciwieństwie do reszty tych usług,
        # odczyty GIOŚ faktycznie zmieniają się co godzinę.
        ("air_quality", cache.get_or_fetch(
            "air_quality", teryt_id, TTL_AIR_QUALITY, lambda: get_air_quality(client, centroid.x, centroid.y))),
    ]
    return [(name, _limited(coro)) for name, coro in specs]


def _compute_derived(parcel: dict[str, Any], area_m2: float, results: dict[str, Any]) -> dict[str, Any]:
    """Given parcel identity + area + all 12 section results, computes the
    fields that need MULTIPLE sections at once (valuation, verdict,
    due-diligence checklist) — factored out (2026-09-04) so /api/analyze and
    /api/analyze-stream's final step share one implementation instead of two
    copies of this logic that could drift apart."""
    buildings = results["buildings"]
    building_list = buildings.get("buildings", []) if buildings.get("status") == "ok" else []
    valuation = estimate_value(area_m2, parcel["voivodeship_code"], building_list)
    verdict = build_verdict(
        results["landslide"], results["zoning"], results["flood_zone"], results["waterlogging"],
        results["utilities"], results["nearest_road"], results["protected_areas"], results["mining_areas"],
        results["air_quality"],
    )

    # Które z 25 punktów listy "przed zakupem" ta analiza już realnie
    # sprawdziła — patrz services/due_diligence.py. "power"/"water_sewage"
    # dzielą jeden status usługi (utilities), bo to jedno zapytanie GESUT
    # sprawdza oba naraz.
    covered = set()
    if results["flood_zone"].get("status") == "ok":
        covered.add("flood_zone")
    if results["protected_areas"].get("status") == "ok":
        covered.add("protected_areas")
    if results["landslide"].get("status") == "ok":
        covered.add("landslide")
    if results["zoning"].get("status") in ("ok", "partial"):
        covered.add("zoning_mpzp")
    if results["nearest_road"].get("status") == "ok":
        covered.add("road")
    if results["utilities"].get("status") == "ok":
        covered.add("power")
        covered.add("water_sewage")
    if valuation.get("status") == "ok":
        covered.add("valuation")
    if results["air_quality"].get("status") == "ok":
        covered.add("air_quality")
    due_diligence = build_due_diligence_checklist(covered)

    return {"valuation": valuation, "verdict": verdict, "due_diligence": due_diligence}


def _analyze_meta(parcel: dict[str, Any], geometry, centroid, area_m2: float) -> dict[str, Any]:
    """Everything about a parcel that's known right after the ULDK lookup —
    no enrichment section needed. Shared by /api/analyze (folded into its one
    big response) and /api/analyze-stream (sent as the first SSE event, so
    the map and identity line render immediately instead of waiting for any
    of the 12 slower sections)."""
    return {
        "parcel": {
            "teryt_id": parcel["teryt_id"],
            "voivodeship": parcel["voivodeship_name"],
            "county": parcel["county"],
            "commune": parcel["commune"],
            "parcel_no": parcel["parcel_no"],
            "multiple_found": parcel["multiple_found"],
            "geoportal_link": get_geoportal_link(parcel["teryt_id"]),
            "emapa_link": get_emapa_link(parcel["teryt_id"]),
        },
        "geometry_geojson": mapping(geometry),
        "centroid": {"lat": centroid.y, "lon": centroid.x},
        "area_m2": round(area_m2, 2),
        "permits": {"gunb_link": get_gunb_link(parcel["parcel_no"])},
        "land_registry": {"ekw_link": get_ekw_link()},
        "map_layers": {
            "egib": {"url": KIEG_URL, "layers": "dzialki,numery_dzialek,budynki"},
            "mpzp": {"url": KIMPZP_URL, "layers": "plany"},
            "app": {"url": KIAPP_URL, "layers": "app"},
        },
    }


async def _resolve_parcel_geometry(parcel_id: str) -> tuple[dict[str, Any], Any, Any, float, float, float]:
    """Shared prelude for both /api/analyze and /api/analyze-stream: the
    single ULDK lookup + derived geometry fields every section needs.
    Raises HTTPException (404/502, from uldk_get_parcel) on a genuine
    lookup failure — for the streaming endpoint this MUST happen before the
    StreamingResponse is constructed, since once that response starts,
    Starlette has already committed HTTP 200 and the event-stream headers,
    so a later error can no longer become a normal JSON error response."""
    client = _get_http_client()
    parcel = await uldk_get_parcel(client, parcel_id)
    geometry = parcel["geometry"]
    centroid = geometry.centroid
    cx2180, cy2180 = to_2180.transform(centroid.x, centroid.y)
    area_m2, _perimeter_m = geod.geometry_area_perimeter(geometry)
    area_m2 = abs(area_m2)
    return parcel, geometry, centroid, cx2180, cy2180, area_m2


@app.get("/api/analyze")
async def analyze(parcel_id: str = Query(default="")):
    parcel_id = parcel_id.strip()
    if len(parcel_id) < 3:
        raise HTTPException(400, "Podaj poprawny numer działki (identyfikator TERYT).")

    client = _get_http_client()
    parcel, geometry, centroid, cx2180, cy2180, area_m2 = await _resolve_parcel_geometry(parcel_id)
    teryt_id = parcel["teryt_id"]

    specs = _section_specs(client, teryt_id, geometry, cx2180, cy2180, centroid)
    values = await asyncio.gather(*[_timed(name, coro) for name, coro in specs])
    results = dict(zip((name for name, _coro in specs), values))

    derived = _compute_derived(parcel, area_m2, results)

    response = _analyze_meta(parcel, geometry, centroid, area_m2)
    response.update(derived)
    response.update({
        "landslide": results["landslide"],
        "utilities": results["utilities"],
        "cadastre": results["cadastre"],
        "buildings": results["buildings"],
        "zoning": results["zoning"],
        "protected_areas": results["protected_areas"],
        "mining_areas": results["mining_areas"],
        "air_quality": results["air_quality"],
        "hydrology": {
            "waterways": results["waterways"],
            "flood_zone": results["flood_zone"],
            "waterlogging": results["waterlogging"],
        },
        "nearest_road": results["nearest_road"],
    })
    return response


def _sse_event(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


@app.get("/api/analyze-stream")
async def analyze_stream(parcel_id: str = Query(default="")):
    """Streaming counterpart of /api/analyze (Server-Sent Events) — added
    2026-09-04, performance optimization (c) from HANDOFF.md's "Propozycje
    optymalizacji wydajności": fast sections (landslide, cadastre, hazards…)
    reach the browser as soon as each one's own network round-trip finishes,
    instead of the whole result waiting for ALL 12 branches — including the
    slowest, least reliable one (plan zagospodarowania) — to land together.
    /api/analyze itself is UNCHANGED and still returns the same one-shot
    JSON response as before; this is an additive alternative that
    static/app.js now uses for the main "Analiza działki" flow.

    Event stream shape (see static/app.js for the consumer):
      event: meta    — parcel identity, geometry, centroid, area, static
                        links (permits/land_registry/map_layers) — nothing
                        here needed a network call beyond the initial ULDK
                        lookup, so it's always the very first chunk.
      event: section — {"key": <one of the 12 _section_specs names>,
                        "value": <that section's result dict>}, one per
                        branch, in whatever order they actually finish.
      event: done    — {"verdict", "due_diligence", "valuation"}, only
                        computable once every section above has resolved.

    A disconnect mid-stream (or any other early exit from the generator)
    cancels whichever of the 12 background tasks are still pending instead
    of leaving them to run untracked to completion."""
    parcel_id = parcel_id.strip()
    if len(parcel_id) < 3:
        raise HTTPException(400, "Podaj poprawny numer działki (identyfikator TERYT).")

    client = _get_http_client()
    parcel, geometry, centroid, cx2180, cy2180, area_m2 = await _resolve_parcel_geometry(parcel_id)
    teryt_id = parcel["teryt_id"]
    specs = _section_specs(client, teryt_id, geometry, cx2180, cy2180, centroid)

    async def _named(name: str, awaitable: Awaitable[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        try:
            return name, await _timed(name, awaitable)
        except Exception as exc:
            # Sekcje w services/*.py z zasady same łapią swoje wyjątki i
            # zwracają {"status": "error", ...} (patrz HANDOFF.md) — to jest
            # tylko ostatnia linia obrony, gdyby któraś tego nie zrobiła.
            # asyncio.gather w /api/analyze miałby ten sam problem (całe
            # zapytanie padłoby z 500), ale tutaj — po tym jak nagłówki
            # odpowiedzi 200 zostały już wysłane — nie ma innej opcji niż
            # zamienić to w wiersz "error" i kontynuować strumień.
            logger.warning("analyze-stream: sekcja %s rzuciła nieoczekiwany wyjątek", name, exc_info=True)
            return name, {"status": "error", "message": f"Wewnętrzny błąd sekcji: {describe_exc(exc)}"}

    async def _events() -> AsyncIterator[bytes]:
        yield _sse_event("meta", _analyze_meta(parcel, geometry, centroid, area_m2))

        tasks = [asyncio.ensure_future(_named(name, coro)) for name, coro in specs]
        results: dict[str, Any] = {}
        try:
            for coro in asyncio.as_completed(tasks):
                name, value = await coro
                results[name] = value
                yield _sse_event("section", {"key": name, "value": value})
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        yield _sse_event("done", _compute_derived(parcel, area_m2, results))

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/service-worker.js")
async def service_worker():
    return FileResponse("static/service-worker.js", media_type="application/javascript")
