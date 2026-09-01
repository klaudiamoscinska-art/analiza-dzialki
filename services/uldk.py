"""GUGiK ULDK (Uniwersalny Lokalizator Działek Katastralnych): parcel
lookups by point, by TERYT id/number, and the ULDK-only fallback searches
used to disambiguate free-text place+parcel-number queries."""
import asyncio
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from shapely.ops import transform as shapely_transform

from config import ULDK_URL, TIMEOUT_OBREB_SCAN, logger
from geo_utils import _parse_uldk_geometry, _rectangle_side_lengths, geod, to_2180

MAX_OBREB_SCAN = 40


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
            logger.warning(
                "ULDK GetParcelByXY (%s, %s): próba %d/%d nieudana",
                lon, lat, attempt + 1, max_retries + 1, exc_info=True,
            )
        if attempt < max_retries:
            await asyncio.sleep(1.5)
    if last_text:
        logger.warning("ULDK GetParcelByXY (%s, %s): wyczerpano próby, ostatnia odpowiedź: %r", lon, lat, last_text[:200])
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
            resp = await client.get(ULDK_URL, params=params, timeout=TIMEOUT_OBREB_SCAN)
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

