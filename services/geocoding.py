"""GUGiK free-text geocoding: address points, gmina TERYT resolution, and
the address-search pipeline (geocode -> parcel at that point)."""
import asyncio
from typing import Any

import httpx

from config import TIMEOUT_GEOCODER, logger
from services.uldk import find_parcel_by_xy

GEOCODER_URL = "https://capap.gugik.gov.pl/api/fts/gc/pkt"


async def geocode_address_points(client: httpx.AsyncClient, query: str, max_results: int = 15) -> list[dict[str, Any]]:
    """Free-text address search (street + number + city) using the same
    official GUGiK geocoder as the gmina lookup above — confirmed live with
    the generic 'q' field, e.g. 'Kraków Floriańska 5' resolves to an exact
    address point with coordinates. An ambiguous query (e.g. a common street
    name with no city) returns many candidates instead of one 'single' match;
    we cap how many we follow up on to keep the response fast."""
    try:
        resp = await client.post(GEOCODER_URL, json={"reqs": [{"q": query}]}, timeout=TIMEOUT_GEOCODER)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("Geokoder GUGiK niedostępny dla zapytania %r", query, exc_info=True)
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
            GEOCODER_URL, json={"reqs": [{"gm_nazwa": name}]}, timeout=TIMEOUT_GEOCODER
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

