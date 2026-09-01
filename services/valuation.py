"""Section 6/7 — permit/map deep links and the statistical land+buildings
valuation (GUS regional average price/m² x area; buildings priced at a
flat, clearly-caveated assumed build cost)."""
from typing import Any, Optional

from config import GUNB_SEARCH_URL, GUS_PRICE_PER_M2, ROUGH_BUILD_COST_PER_M2, VOIVODESHIP_NAMES

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

