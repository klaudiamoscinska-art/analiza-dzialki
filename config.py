"""Configuration constants shared across the app: external service URLs,
layer names, HTTP timeouts, and static lookup tables. No logic here beyond
the module logger — see geo_utils.py, http_utils.py and services/ for that."""
import logging

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
TIMEOUT_OVERPASS = 14.0  # OpenStreetMap Overpass API
TIMEOUT_WFS_POWIAT = 45.0  # individual powiat WFS servers — confirmed slow, keep generous
TIMEOUT_ISOK_FLOOD = 30.0  # Wody Polskie ISOK flood-depth WMS
TIMEOUT_PIG_WATERLOGGING = 30.0  # PIG-PIB waterlogging-risk ArcGIS 'identify'
TIMEOUT_MPZP_PROBE = 15.0  # MPZP/APP GetMap visual pre-check
TIMEOUT_MPZP_DETAIL = 12.0  # MPZP/APP GetFeatureInfo detail fetch (wrapped in asyncio.wait_for)
TIMEOUT_OBREB_SCAN = 10.0  # per-obręb brute-force scan (many parallel short requests)

HTTP_TIMEOUT = TIMEOUT_DEFAULT

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
