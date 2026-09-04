"""Follow-up to the Działkopedia competitor analysis (see HANDOFF.md) —
nearest air-quality monitoring station and its latest PM2.5/PM10 reading,
via GIOŚ's public JSON API (api.gios.gov.pl/pjp-api/v1/rest). Added
2026-09-04.

Meaningfully better-corroborated than most of this app's other newer
integrations: the base URL and JSON shape below match real, captured API
responses independently reproduced by several open-source projects (see
HANDOFF.md for citations), not just a plausible-looking URL. Still NOT
verified live from this app itself — government domains are blocked in
the sandbox this was written in.

No 'find nearest station' endpoint exists — GIOŚ's API only lists ALL
~288 stations, so this fetches (and caches, see services/cache.py) that
whole list once and ranks it by distance itself. About 42% of Poland's
measurement stands are MANUAL (lab-filter based) and never return
anything from the live-data endpoint — data/getData simply comes back
empty for those — so this tries several nearest candidates in order, not
just the closest one, and skips silently past any that have no working
automatic sensor for PM2.5 or PM10."""
from typing import Any, Optional

import httpx

from config import GIOS_API_URL, TIMEOUT_GIOS, TTL_AIR_QUALITY_STATIONS, logger
from geo_utils import geod
from services import cache

# GIOŚ's terms of use require visible attribution of the data source —
# see https://powietrze.gios.gov.pl/pjp/content/terms_of_service.
ATTRIBUTION = "Źródło danych: GIOŚ — EKOINFONET"

# Prefer PM2.5 (finer, more health-relevant) but fall back to PM10, since
# PM10 coverage is denser — many stations only have one of the two.
_PREFERRED_POLLUTANTS = ("PM2.5", "PM10")

_MAX_STATION_CANDIDATES = 5


async def _fetch_all_stations(client: httpx.AsyncClient) -> dict[str, Any]:
    """Paginates through station/findAll (page size capped at 500 by the
    API) — confirmed live by third parties to return ~288 stations
    nationally, comfortably one page, but this still paginates on
    totalPages rather than assuming that."""
    stations: list[dict[str, Any]] = []
    page = 0
    while True:
        resp = await client.get(
            f"{GIOS_API_URL}/station/findAll", params={"page": page, "size": 500}, timeout=TIMEOUT_GIOS,
        )
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("Lista stacji pomiarowych", []):
            try:
                lat = float(s["WGS84 φ N"])
                lon = float(s["WGS84 λ E"])
            except (KeyError, TypeError, ValueError):
                continue
            station_id = s.get("Identyfikator stacji")
            name = s.get("Nazwa stacji")
            if station_id is None or not name:
                continue
            stations.append({"id": station_id, "name": name, "lat": lat, "lon": lon})
        total_pages = data.get("totalPages", 1) or 1
        page += 1
        if page >= total_pages:
            break
    return {"status": "ok", "stations": stations}


async def _nearest_stations(client: httpx.AsyncClient, lon: float, lat: float) -> list[dict[str, Any]]:
    result = await cache.get_or_fetch(
        "air_quality_stations", "all", TTL_AIR_QUALITY_STATIONS,
        lambda: _fetch_all_stations(client),
    )
    stations = result.get("stations", []) if result.get("status") == "ok" else []
    if not stations:
        return []
    ranked = sorted(stations, key=lambda s: geod.inv(lon, lat, s["lon"], s["lat"])[2])
    return ranked[:_MAX_STATION_CANDIDATES]


async def _find_pollutant_sensor(client: httpx.AsyncClient, station_id: int) -> Optional[dict[str, Any]]:
    resp = await client.get(f"{GIOS_API_URL}/station/sensors/{station_id}", timeout=TIMEOUT_GIOS)
    resp.raise_for_status()
    data = resp.json()
    by_code = {}
    for s in data.get("Lista stanowisk pomiarowych dla podanej stacji", []):
        code = (s.get("Wskaźnik - kod") or "").strip()
        sensor_id = s.get("Identyfikator stanowiska")
        if code and sensor_id is not None:
            by_code[code] = sensor_id
    for pollutant in _PREFERRED_POLLUTANTS:
        if pollutant in by_code:
            return {"sensor_id": by_code[pollutant], "pollutant": pollutant}
    return None


async def _latest_value(client: httpx.AsyncClient, sensor_id: int) -> Optional[dict[str, Any]]:
    """The newest row or two frequently has a null 'Wartość' (measurement
    not finalized yet) — confirmed by third-party integrations that all
    scan forward for the first non-null row rather than trusting index 0."""
    resp = await client.get(f"{GIOS_API_URL}/data/getData/{sensor_id}", params={"size": 24}, timeout=TIMEOUT_GIOS)
    resp.raise_for_status()
    data = resp.json()
    for row in data.get("Lista danych pomiarowych", []):
        value = row.get("Wartość")
        if value is not None:
            return {"value": value, "measured_at": row.get("Data")}
    return None


async def get_air_quality(client: httpx.AsyncClient, lon: float, lat: float) -> dict[str, Any]:
    try:
        candidates = await _nearest_stations(client, lon, lat)
    except Exception as exc:
        logger.warning("get_air_quality: lista stacji GIOŚ niedostępna", exc_info=True)
        return {"status": "error", "message": f"Usługa GIOŚ (lista stacji) niedostępna: {exc}"}

    if not candidates:
        return {"status": "error", "message": "Brak stacji pomiarowych GIOŚ w bazie."}

    for station in candidates:
        try:
            sensor = await _find_pollutant_sensor(client, station["id"])
            if sensor is None:
                continue
            reading = await _latest_value(client, sensor["sensor_id"])
            if reading is None:
                continue
            distance_m = geod.inv(lon, lat, station["lon"], station["lat"])[2]
            return {
                "status": "ok",
                "station_name": station["name"],
                "distance_m": round(distance_m),
                "pollutant": sensor["pollutant"],
                "value": reading["value"],
                "unit": "µg/m³",
                "measured_at": reading["measured_at"],
                "attribution": ATTRIBUTION,
            }
        except Exception:
            logger.warning("get_air_quality: stacja %s pominięta po błędzie", station.get("id"), exc_info=True)
            continue

    return {
        "status": "error",
        "message": (
            "Żadna z pobliskich stacji GIOŚ nie zwróciła aktualnego pomiaru PM2.5/PM10 — część stacji jest "
            "manualna (wyniki laboratoryjne z opóźnieniem tygodni) i nie raportuje danych na bieżąco."
        ),
    }
