"""Configuration constants shared across the app: external service URLs,
layer names, HTTP timeouts, and static lookup tables. No logic here beyond
the module logger — see geo_utils.py, http_utils.py and services/ for that."""
import logging
import os

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

# PIG-PIB MIDAS — obszary/tereny górnicze (mining areas/terrains), a real
# legal encumbrance on a parcel. Same host and same ArcGIS REST 'identify'
# pattern as SOPO/podtopienia above — added 2026-09-04 (item 9, competitor
# analysis — see HANDOFF.md). NOT verified live: unlike SOPO's layer IDs
# (confirmed live via ?f=json), this URL and the fact that a 'midas'
# service exists on this host is corroborated only from third-party
# sources (see HANDOFF.md citations), not from PIG-PIB's own live
# capabilities response.
MIDAS_BASE_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/MapServer"

# GDOŚ — obszary chronione przyrody (parki narodowe, rezerwaty, parki
# krajobrazowe, obszary chronionego krajobrazu, Natura 2000), served via
# GDOŚ's own WFS (GeoServer). Added 2026-09-04 (item 8, competitor
# analysis). NOT verified live — URL and layer (typeName) names are
# corroborated from multiple independent open-source projects that
# already integrate this exact service (see HANDOFF.md citations), since
# GDOŚ's own GetCapabilities isn't reachable from this sandbox.
GDOS_WFS_URL = "https://sdi.gdos.gov.pl/wfs"
GDOS_LAYERS = [
    ("GDOS:ParkiNarodowe", "park narodowy"),
    ("GDOS:Rezerwaty", "rezerwat przyrody"),
    ("GDOS:ParkiKrajobrazowe", "park krajobrazowy"),
    ("GDOS:ObszaryChronionegoKrajobrazu", "obszar chronionego krajobrazu"),
    ("GDOS:ObszarySpecjalnejOchrony", "obszar Natura 2000 (ptasi)"),
    ("GDOS:SpecjalneObszaryOchrony", "obszar Natura 2000 (siedliskowy)"),
]

# Hałas (mapy akustyczne) — deliberately NOT integrated as a live service.
# Researched 2026-09-04: there is no single national WMS/API for
# strategic noise maps in Poland. GIOŚ aggregates for EU reporting but
# doesn't publish a unified map; actual noise contours are published
# separately by GDDKiA (national roads), PKP PLK (rail), airports, and
# every city over 100k population, each on its own portal with its own
# schema — dozens of separate integrations for a layer that would return
# "no data" for most (rural/small-town) parcels anyway, which reads as a
# false "no noise risk" rather than "not covered". See HANDOFF.md — the
# app shows a static disclaimer instead of a live check.

# GIOŚ (Główny Inspektorat Ochrony Środowiska) — public air-quality API,
# added 2026-09-04. Unlike most of this app's newer integrations, this one
# is well corroborated: real captured API responses from several
# independent open-source projects agree on the exact JSON shape (see
# HANDOFF.md for citations) — NOT verified live from this app itself
# (government domains blocked in this sandbox), but meaningfully more
# solid ground than a guessed URL. The unversioned '/pjp-api/rest/...'
# base was retired 2025-06-30 (now returns 410 Gone) — this MUST be the
# '/v1/' base or every request 404s/410s.
GIOS_API_URL = "https://api.gios.gov.pl/pjp-api/v1/rest"

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
# "plany" (the generic top-level/group layer) does NOT reliably render —
# confirmed live 2026-09-03 by fetching this service's own GetCapabilities:
# it's one name among several, and gminas publish either raster or vector
# plans (never both), so a single generic name misses whichever format a
# given gmina actually uses. Query all the real content leaf layers at
# once instead (boundary-only "granice"/"plany_granice" deliberately
# excluded — those just draw the plan's outline box, not its content):
KIMPZP_LAYERS = "raster,wektor-str,wektor-lzb,wektor-pow,wektor-lin,wektor-pkt"

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

# GUNB's official building-permits register (RWDZ) has no public API and its
# search UI is CAPTCHA-protected. We only offer a deep link, never scraped data.
GUNB_SEARCH_URL = "https://wyszukiwarka.gunb.gov.pl/"

logger = logging.getLogger("analiza_dzialki")

# --------------------------------------------------------------------------
# HTTP timeouts (seconds), centralized here instead of scattered literals.
# Values reflect real, confirmed-live behaviour of each service: the ~380
# independent powiat WFS servers are the slowest and least predictable (see
# section 4 of HANDOFF.md), so they get by far the most generous timeout;
# ArcGIS 'identify' and Overpass calls are usually fast but occasionally
# slow under load. Do not collapse these into one shared value — that was
# tried informally before and either starved the slow services or made every
# other request wait too long on a hung one.
# --------------------------------------------------------------------------
TIMEOUT_DEFAULT = 20.0  # default httpx.AsyncClient timeout (most WMS/ArcGIS 'identify' calls)
TIMEOUT_GEOCODER = 15.0  # GUGiK geocoder (capap.gugik.gov.pl)
TIMEOUT_OVERPASS = 30.0  # OpenStreetMap Overpass API — MUST exceed the "[timeout:25]" directive
# inside every Overpass query in services/nearby_features.py (fixed 2026-09-04, reported live by
# Klaudia as an intermittent "usługa niedostępna" for the nearest-road check): a client timeout
# shorter than the server-side budget we ourselves grant the query means we give up before Overpass
# would have, turning "the server is a bit busy" into a false "the server is down" every time.
TIMEOUT_WFS_POWIAT = 45.0  # individual powiat WFS servers — confirmed slow, keep generous
TIMEOUT_ISOK_FLOOD = 30.0  # Wody Polskie ISOK flood-depth WMS
TIMEOUT_PIG_WATERLOGGING = 30.0  # PIG-PIB waterlogging-risk ArcGIS 'identify'
TIMEOUT_MIDAS = 30.0  # PIG-PIB MIDAS mining-areas ArcGIS 'identify' — same host/cadence as SOPO
TIMEOUT_GDOS = 20.0  # GDOŚ protected-areas WFS GetFeature
TIMEOUT_GIOS = 30.0  # GIOŚ air-quality API — station list, sensors, readings
TIMEOUT_MPZP_PROBE = 15.0  # MPZP/APP GetMap visual pre-check
TIMEOUT_MPZP_DETAIL = 12.0  # MPZP/APP GetFeatureInfo detail fetch (wrapped in asyncio.wait_for)
TIMEOUT_OBREB_SCAN = 10.0  # per-obręb brute-force scan (many parallel short requests)

HTTP_TIMEOUT = TIMEOUT_DEFAULT

# --------------------------------------------------------------------------
# services/cache.py — per-parcel cache-aside TTLs (seconds), added 2026-09-04
# after a performance investigation (see the "Plan Pamięci Podręcznej"
# artifact referenced in HANDOFF.md). Value chosen per how often the real
# underlying registry actually changes, NOT a single shared number —
# geological/hydrological hazard maps are revised on legally-mandated
# multi-year cycles (near-zero risk of a long TTL being wrong), while
# anything with real decision-relevant risk gets left uncached for now
# (zoning — a gmina's plan can change mid-negotiation — and the parcel's
# own ULDK identification) rather than assigned a falsely-reassuring TTL.
# See the artifact for the full per-service reasoning.
# --------------------------------------------------------------------------
CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.db")

_DAY = 86400.0
TTL_LANDSLIDE = 180 * _DAY  # SOPO — geological survey, multi-year revision cycle
TTL_FLOOD_ZONE = 180 * _DAY  # ISOK — statutory ~6-year revision cycle
TTL_WATERLOGGING = 180 * _DAY  # PIG-PIB — same geological-survey cadence as SOPO
TTL_WATERWAYS = 90 * _DAY  # OSM hydrography — essentially never changes
TTL_UTILITIES = 30 * _DAY  # GESUT — new connections happen, but rarely
TTL_CADASTRE = 30 * _DAY  # KIEG basic — geodetic updates, rarely
TTL_NEAREST_ROAD = 30 * _DAY  # OSM road network — rarely changes
TTL_BUILDINGS = 14 * _DAY  # OSM buildings — new construction is the one thing here that plausibly moves faster
TTL_MINING_AREAS = 180 * _DAY  # PIG-PIB MIDAS — same geological-survey cadence as SOPO
TTL_PROTECTED_AREAS = 180 * _DAY  # GDOŚ — protected-area boundaries are a legal act, changes are rare and public
TTL_AIR_QUALITY_STATIONS = 30 * _DAY  # GIOŚ station list (~288 stations, coordinates) — changes rarely
TTL_AIR_QUALITY = 3600.0  # GIOŚ readings are HOURLY data — a long TTL here would show stale air quality as current, unlike the geological/legal data above
# Deliberately NOT cached yet: zoning (plan zagospodarowania — the one
# service where a change inside the TTL window has real decision
# relevance) and the parcel's own ULDK identification (its "identity" —
# see HANDOFF.md for why these are being held back until the "dane z:"
# timestamp + manual refresh UI ships).

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
