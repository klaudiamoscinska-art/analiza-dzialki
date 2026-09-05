"""Unit tests for app logic that does NOT depend on live network calls.

These deliberately stay away from anything that hits ULDK/WFS/Overpass/etc.
directly (this sandbox has no route to those government services — see
HANDOFF.md), and instead cover the parts of the app that are pure
computation or that can be exercised with a monkeypatched network layer:
geometry helpers, the "Szukaj działki" matching/ranking logic, link
builders, and the WFS registry lookup rules.

Run with: pytest (after `pip install -r requirements-dev.txt`).
"""
import asyncio
import io
import json
import pathlib
import re
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from shapely.geometry import Polygon

import geo_utils
import http_utils
import main
from config import KIAPP_URL, KIMPZP_URL, TIMEOUT_OVERPASS
from services import (
    air_quality, cache, due_diligence, geocoding, geology, nature, uldk, utilities, valuation, verdict, wfs_search,
    zoning,
)


async def _no_sleep(*_args, **_kwargs) -> None:
    """Drop-in replacement for asyncio.sleep in tests that exercise a retry
    loop's delay — keeps tests fast without changing the retry logic itself."""
    return None


# ---------------------------------------------------------------------------
# geo_utils._parse_uldk_geometry
# ---------------------------------------------------------------------------

def test_parse_uldk_geometry_plain_wkt():
    geom = geo_utils._parse_uldk_geometry("POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))")
    assert geom.area == pytest.approx(1.0)


def test_parse_uldk_geometry_ewkt_srid_prefix_is_stripped():
    geom = geo_utils._parse_uldk_geometry("SRID=4326;POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))")
    assert geom.area == pytest.approx(1.0)


def test_parse_uldk_geometry_wkb_hex_fallback():
    original = Polygon([(0, 0), (0, 2), (2, 2), (2, 0)])
    geom = geo_utils._parse_uldk_geometry(original.wkb_hex)
    assert geom.equals(original)


def test_parse_uldk_geometry_unparseable_raises():
    with pytest.raises(ValueError):
        geo_utils._parse_uldk_geometry("not a geometry at all")


# ---------------------------------------------------------------------------
# geo_utils._rectangle_side_lengths
# ---------------------------------------------------------------------------

def test_rectangle_side_lengths_axis_aligned_rectangle():
    rect = Polygon([(0, 0), (0, 30), (10, 30), (10, 0)])
    short, long = geo_utils._rectangle_side_lengths(rect)
    assert short == pytest.approx(10.0)
    assert long == pytest.approx(30.0)


def test_rectangle_side_lengths_rotated_rectangle():
    # A 10x30 rectangle rotated 30 degrees should still report the same
    # true side lengths via the minimum rotated bounding rectangle.
    import math
    angle = math.radians(30)
    w, h = 10.0, 30.0
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    rotated = [
        (x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle))
        for x, y in corners
    ]
    rect = Polygon(rotated)
    short, long = geo_utils._rectangle_side_lengths(rect)
    assert short == pytest.approx(10.0, abs=0.01)
    assert long == pytest.approx(30.0, abs=0.01)


# ---------------------------------------------------------------------------
# geo_utils._polygon_outline_normalized
# ---------------------------------------------------------------------------

def test_polygon_outline_normalized_preserves_aspect_ratio():
    # 10x30 rectangle -> longer side maps to target_size, shorter side
    # scales proportionally (10/30 of target_size).
    rect = Polygon([(1000, 2000), (1000, 2030), (1010, 2030), (1010, 2000)])
    pts = geo_utils._polygon_outline_normalized(rect, target_size=60.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert max(xs) - min(xs) == pytest.approx(20.0, abs=0.1)  # 10/30 * 60
    assert max(ys) - min(ys) == pytest.approx(60.0, abs=0.1)


def test_polygon_outline_normalized_is_north_up():
    # The northernmost (higher y in EPSG:2180) point must map to the
    # smallest SVG y (SVG y grows downward, projected northing grows up).
    rect = Polygon([(0, 0), (0, 30), (10, 30), (10, 0)])
    pts = geo_utils._polygon_outline_normalized(rect)
    north_point_svg_y = next(svg_y for (x, y), (svg_x, svg_y) in zip(rect.exterior.coords, pts) if y == 30)
    south_point_svg_y = next(svg_y for (x, y), (svg_x, svg_y) in zip(rect.exterior.coords, pts) if y == 0)
    assert north_point_svg_y < south_point_svg_y


def test_polygon_outline_normalized_simplifies_many_vertices():
    # A near-circular polygon with 200 vertices should come back much
    # smaller — a thumbnail doesn't need every survey point.
    import math
    circle = Polygon(
        [(50 * math.cos(t), 50 * math.sin(t)) for t in [i * 2 * math.pi / 200 for i in range(200)]]
    )
    pts = geo_utils._polygon_outline_normalized(circle)
    assert len(pts) < 100


# ---------------------------------------------------------------------------
# geo_utils._feature_info_has_data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("", False),
    ("short", False),
    ("brak danych w tej lokalizacji dla wybranej warstwy", False),
    ("Usługa nie udostępnia danych opisowych dla wybranego obiektu", False),
    ("search returned no results for this location query", False),
    ("Nazwa planu: MPZP Śródmieście | Uchwała: XV/123/2020 | Status: obowiązujący", True),
])
def test_feature_info_has_data(text, expected):
    assert geo_utils._feature_info_has_data(text) is expected


# ---------------------------------------------------------------------------
# geo_utils._within_poland
# ---------------------------------------------------------------------------

def test_within_poland_center_point():
    assert geo_utils._within_poland(19.0, 52.0) is True


def test_within_poland_outside_bounds():
    assert geo_utils._within_poland(2.0, 48.85) is False  # Paris


# ---------------------------------------------------------------------------
# services.valuation.estimate_value
# ---------------------------------------------------------------------------

def test_estimate_value_known_voivodeship_no_buildings():
    result = valuation.estimate_value(area_m2=1000.0, voivodeship_code="12", buildings=[])
    assert result["status"] == "ok"
    assert result["land"]["voivodeship_name"] == "małopolskie"
    assert result["land"]["value_pln"] == pytest.approx(1000.0 * 121.0)
    assert result["buildings"] is None


def test_estimate_value_with_buildings():
    buildings = [{"area_m2": 80.0}, {"area_m2": 20.0}]
    result = valuation.estimate_value(area_m2=500.0, voivodeship_code="14", buildings=buildings)
    assert result["buildings"]["building_count"] == 2
    assert result["buildings"]["footprint_area_m2"] == pytest.approx(100.0)
    assert result["buildings"]["value_pln"] == pytest.approx(100.0 * valuation.ROUGH_BUILD_COST_PER_M2)


def test_estimate_value_unknown_voivodeship_is_error():
    result = valuation.estimate_value(area_m2=500.0, voivodeship_code=None, buildings=[])
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# services.valuation deep-link builders
# ---------------------------------------------------------------------------

def test_get_gunb_link_uses_last_dot_segment():
    assert valuation.get_gunb_link("121507_2.0004.3692/5") == f"{valuation.GUNB_SEARCH_URL}?ew_parcel=3692/5"


def test_get_gunb_link_no_dot_uses_whole_string():
    assert valuation.get_gunb_link("3692/5") == f"{valuation.GUNB_SEARCH_URL}?ew_parcel=3692/5"


def test_get_geoportal_link_uses_old_imap_viewer():
    link = valuation.get_geoportal_link("121507_2.0004.3692/5")
    assert link == "https://mapy.geoportal.gov.pl/imap/?identifyParcel=121507_2.0004.3692/5"
    assert "/imapnext/" not in link


def test_get_emapa_link():
    assert valuation.get_emapa_link("121507_2.0004.3692/5") == "https://polska.e-mapa.net?identifyParcel=121507_2.0004.3692/5"


# ---------------------------------------------------------------------------
# services.wfs_search._lookup_wfs_config
# ---------------------------------------------------------------------------

def test_lookup_wfs_config_gmina_level_exact_match_wins(monkeypatch):
    fake_registry = {
        "121507_2": {"url": "https://gmina.example/wfs", "version": "2.0.0", "layer": "dzialki"},
        "1215": {"url": "https://powiat.example/wfs", "version": "1.1.0", "layer": "dzialki"},
    }
    monkeypatch.setattr(wfs_search, "WFS_POWIAT_REGISTRY", fake_registry)
    config = wfs_search._lookup_wfs_config("121507_2.0004.3692/5")
    assert config["url"] == "https://gmina.example/wfs"


def test_lookup_wfs_config_falls_back_to_powiat_prefix(monkeypatch):
    fake_registry = {
        "1215": {"url": "https://powiat.example/wfs", "version": "1.1.0", "layer": "dzialki"},
    }
    monkeypatch.setattr(wfs_search, "WFS_POWIAT_REGISTRY", fake_registry)
    config = wfs_search._lookup_wfs_config("121507_2.0004.3692/5")
    assert config["url"] == "https://powiat.example/wfs"


def test_lookup_wfs_config_missing_powiat_returns_none(monkeypatch):
    monkeypatch.setattr(wfs_search, "WFS_POWIAT_REGISTRY", {})
    assert wfs_search._lookup_wfs_config("121507_2.0004.3692/5") is None


# ---------------------------------------------------------------------------
# services.wfs_search.search_parcels_universal — the core "Szukaj działki"
# matching/ranking logic. _gather_nearby_parcels is monkeypatched to avoid
# any network dependency; everything below this point (filtering,
# rectangularity check, RMS scoring, sorting, rounding) is real production
# logic under test.
# ---------------------------------------------------------------------------

def _make_parcel(teryt_id, area_m2, short_side_m, long_side_m):
    return {
        "teryt_id": teryt_id,
        "area_m2": area_m2,
        "short_side_m": short_side_m,
        "long_side_m": long_side_m,
    }


def _patch_gather(monkeypatch, parcels: dict):
    async def fake_gather(client, place_query):
        return {"status": "ok", "parcels": parcels, "search_center": place_query}
    monkeypatch.setattr(wfs_search, "_gather_nearby_parcels", fake_gather)


@pytest.mark.asyncio
async def test_search_by_area_only_filters_by_tolerance(monkeypatch):
    parcels = {
        "a": _make_parcel("a", area_m2=1000, short_side_m=20, long_side_m=50),   # within 10%
        "b": _make_parcel("b", area_m2=1200, short_side_m=20, long_side_m=60),   # +20%, excluded
        "c": _make_parcel("c", area_m2=950, short_side_m=19, long_side_m=50),    # within 10%
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(None, "Testowo", target_area_m2=1000)
    ids = {m["teryt_id"] for m in result["matches"]}
    assert ids == {"a", "c"}


@pytest.mark.asyncio
async def test_search_rejects_irregular_parcel_via_rectangularity_filter(monkeypatch):
    # short*long = 20*50 = 1000 m^2 bounding rectangle, but the real parcel
    # is only 500 m^2 (rectangularity 0.5) -> below the 0.65 default cutoff,
    # so a width+length search must reject it even though both sides match.
    parcels = {
        "irregular": _make_parcel("irregular", area_m2=500, short_side_m=20, long_side_m=50),
        "regular": _make_parcel("regular", area_m2=980, short_side_m=20, long_side_m=50),
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(
        None, "Testowo", target_width_m=20, target_length_m=50,
    )
    ids = {m["teryt_id"] for m in result["matches"]}
    assert ids == {"regular"}


@pytest.mark.asyncio
async def test_search_dims_as_maximum_excludes_oversized_and_ranks_by_fill(monkeypatch):
    parcels = {
        "too_big": _make_parcel("too_big", area_m2=1300, short_side_m=25, long_side_m=55),  # exceeds ceiling
        "snug": _make_parcel("snug", area_m2=950, short_side_m=19, long_side_m=49),          # close to ceiling
        "loose": _make_parcel("loose", area_m2=600, short_side_m=12, long_side_m=30),        # well under ceiling
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(
        None, "Testowo", target_width_m=20, target_length_m=50, dims_as_maximum=True,
    )
    ids = [m["teryt_id"] for m in result["matches"]]
    assert "too_big" not in ids
    # "snug" fills the envelope more fully than "loose", so it must rank first.
    assert ids.index("snug") < ids.index("loose")
    assert result["criteria"]["dims_as_maximum"] is True


@pytest.mark.asyncio
async def test_search_single_dimension_requires_area(monkeypatch):
    # Business rule enforced in the /api/search-by-parcel-size endpoint, not
    # in search_parcels_universal itself — but we can still confirm that
    # given only a single dimension (no area), the function matches against
    # whichever side (short or long) is closer, without raising.
    parcels = {
        "match_short": _make_parcel("match_short", area_m2=1000, short_side_m=20.5, long_side_m=60),
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(None, "Testowo", target_width_m=20)
    assert len(result["matches"]) == 1
    assert result["matches"][0]["matched_side_label"] == "krótszy bok"


@pytest.mark.asyncio
async def test_search_no_criteria_returns_all_candidates_unfiltered(monkeypatch):
    parcels = {
        "a": _make_parcel("a", area_m2=1000, short_side_m=20, long_side_m=50),
        "b": _make_parcel("b", area_m2=200, short_side_m=5, long_side_m=40),
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(None, "Testowo")
    assert len(result["matches"]) == 2
    assert result["criteria"] == {
        "area": False, "dimensions": False, "single_dimension": False, "dims_as_maximum": False,
    }


@pytest.mark.asyncio
async def test_search_returns_all_matches_uncapped(monkeypatch):
    # Klaudia zażyczyła sobie WSZYSTKICH pasujących działek, nie top-10 —
    # 15 działek w tolerancji muszą wszystkie wrócić, żaden sztuczny limit.
    parcels = {
        str(i): _make_parcel(str(i), area_m2=1000 + i, short_side_m=20, long_side_m=50)
        for i in range(15)
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(None, "Testowo", target_area_m2=1000)
    assert len(result["matches"]) == 15


@pytest.mark.asyncio
async def test_search_dimensions_use_10_percent_tolerance_not_20(monkeypatch):
    # Wymiary teraz też ±10% (dawniej ±20%) — działka 15% poza celem musi
    # zostać odrzucona, mimo że mieściłaby się w starej tolerancji.
    parcels = {
        "within_10pct": _make_parcel("within_10pct", area_m2=1000, short_side_m=21, long_side_m=50),
        "within_15pct_only": _make_parcel("within_15pct_only", area_m2=1000, short_side_m=23, long_side_m=50),
    }
    _patch_gather(monkeypatch, parcels)
    result = await wfs_search.search_parcels_universal(
        None, "Testowo", target_width_m=20, target_length_m=50,
    )
    ids = {m["teryt_id"] for m in result["matches"]}
    assert ids == {"within_10pct"}


@pytest.mark.asyncio
async def test_gather_nearby_parcels_falls_back_to_powiat_geocoding(monkeypatch):
    # "Powiat suski" nie jest adresem/miejscowością (darmowy geokoder 'q'
    # nic nie znajduje) - _gather_nearby_parcels musi wtedy zdjąć przedrostek
    # "Powiat " i spróbować strukturalnego pola pow_nazwa zamiast od razu
    # zwracać błąd "nie znaleziono".
    calls = {}

    async def fake_address_geocode(client, query, max_results=15):
        calls["address_query"] = query
        return []

    async def fake_powiat_geocode(client, name):
        calls["powiat_query"] = name
        return [{"lon": 19.6, "lat": 49.7, "description": "Sucha Beskidzka"}]

    async def fake_find_parcel_by_xy(client, lon, lat):
        return {"teryt_id": "121507_2.0001.1"}

    async def fake_enumerate(*args, **kwargs):
        calls["enumerate_called"] = True
        return []

    monkeypatch.setattr(wfs_search, "geocode_address_points", fake_address_geocode)
    monkeypatch.setattr(wfs_search, "geocode_powiat_gmina_points", fake_powiat_geocode)
    monkeypatch.setattr(wfs_search, "find_parcel_by_xy", fake_find_parcel_by_xy)
    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)

    result = await wfs_search._gather_nearby_parcels(None, "Powiat suski")

    assert calls["address_query"] == "Powiat suski"
    assert calls["powiat_query"] == "suski"
    assert calls["enumerate_called"] is True
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_gather_nearby_parcels_powiat_searches_every_gmina_separately(monkeypatch):
    # Klaudia zgłosiła na żywo (2026-09-03): pierwsza wersja zbierała
    # WSZYSTKIE centroidy gmin do jednej mediany i szukała tylko w promieniu
    # wokół niej - pokrywało to małą część powiatu. Naprawa: osobne
    # zapytanie enumerate_parcel_points_in_area per gmina, z wynikami
    # połączonymi w jedną listę kandydatów - działki z KAŻDEJ gminy muszą
    # się pojawić w wyniku, nie tylko z gminy najbliższej medianie.
    gminas = [
        {"lon": 19.6, "lat": 49.7, "description": "Sucha Beskidzka"},
        {"lon": 19.8, "lat": 49.6, "description": "Maków Podhalański"},
        {"lon": 19.5, "lat": 49.8, "description": "Zawoja"},
    ]
    enumerate_calls = []

    async def fake_address_geocode(client, query, max_results=15):
        return []

    async def fake_powiat_geocode(client, name):
        return gminas

    async def fake_find_parcel_by_xy(client, lon, lat):
        return {"teryt_id": "121507_2.0001.1"}

    async def fake_enumerate(client, teryt_id, x_2180, y_2180, anchor_lon, anchor_lat, radius_m=None, max_features=None):
        enumerate_calls.append({"anchor_lon": anchor_lon, "max_features": max_features})
        # Jeden unikalny punkt-kandydat per gmina, żeby dało się policzyć,
        # z ilu różnych gmin faktycznie przyszły wyniki.
        return [(anchor_lon, anchor_lat)]

    async def fake_find_parcel_with_area_by_xy(client, lon, lat):
        return {
            "teryt_id": f"parcel-{lon}-{lat}", "voivodeship": "x", "county": "x", "commune": "x",
            "parcel_no": "1", "area_m2": 1000.0, "short_side_m": 20.0, "long_side_m": 50.0,
        }

    monkeypatch.setattr(wfs_search, "geocode_address_points", fake_address_geocode)
    monkeypatch.setattr(wfs_search, "geocode_powiat_gmina_points", fake_powiat_geocode)
    monkeypatch.setattr(wfs_search, "find_parcel_by_xy", fake_find_parcel_by_xy)
    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)
    monkeypatch.setattr(wfs_search, "find_parcel_with_area_by_xy", fake_find_parcel_with_area_by_xy)

    result = await wfs_search._gather_nearby_parcels(None, "Powiat suski")

    assert len(enumerate_calls) == 3
    assert {c["anchor_lon"] for c in enumerate_calls} == {19.6, 19.8, 19.5}
    assert all(c["max_features"] == 166 for c in enumerate_calls)  # max(50, 500 // 3)
    assert result["status"] == "ok"
    assert len(result["parcels"]) == 3  # jedna działka z każdej z trzech gmin


@pytest.mark.asyncio
async def test_gather_nearby_parcels_no_powiat_fallback_for_normal_place(monkeypatch):
    # Zwykła nazwa miejscowości, dla której geokoder nic nie zwrócił, NIE
    # powinna wywoływać fallbacku powiatowego (nie zaczyna się od "Powiat ").
    calls = {"powiat_called": False}

    async def fake_address_geocode(client, query, max_results=15):
        return []

    async def fake_powiat_geocode(client, name):
        calls["powiat_called"] = True
        return []

    monkeypatch.setattr(wfs_search, "geocode_address_points", fake_address_geocode)
    monkeypatch.setattr(wfs_search, "geocode_powiat_gmina_points", fake_powiat_geocode)

    result = await wfs_search._gather_nearby_parcels(None, "Nieistniejąca Wieś Zupełnie")

    assert calls["powiat_called"] is False
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_gather_nearby_parcels_plain_place_uses_small_radius(monkeypatch):
    # Regresja zgłoszona na żywo 2026-09-05: wyszukiwanie "Raciechowice"
    # (mała gmina w powiecie myślenickim) zwracało tylko działki z sąsiednich
    # Dobczyc. Przyczyna: domyślny radius_m w enumerate_parcel_points_in_area
    # był tymczasowo podbity do 15000 dla wyszukiwania całego powiatu, ale ta
    # gałąź (zwykła nazwa miejscowości, NIE "Powiat X") nigdy nie przekazywała
    # własnego radius_m, więc po cichu dziedziczyła podbity domyślny promień —
    # 15km wokół małej gminy sięga głęboko w większych sąsiadów obsługiwanych
    # przez ten sam serwer WFS powiatu, a limit max_features=500 (bez
    # sortowania po odległości) mógł się wyczerpać, zanim padły działki z
    # docelowej miejscowości. Ten test pilnuje, żeby ta gałąź NIE wracała do
    # dużego promienia.
    import inspect

    # 1) sam default w sygnaturze funkcji musi zostać mały (nie 15000) —
    # to jest właściwe źródło regresji, niezależnie od tego, co przekazują
    # poszczególni wywołujący.
    default_radius = inspect.signature(wfs_search.enumerate_parcel_points_in_area).parameters["radius_m"].default
    assert default_radius <= 3000.0

    calls = {}

    async def fake_address_geocode(client, query, max_results=15):
        return [{"lon": 20.0, "lat": 49.8, "description": "Raciechowice"}]

    async def fake_find_parcel_by_xy(client, lon, lat):
        return {"teryt_id": "121505_2.0001.1"}

    async def fake_enumerate(client, teryt_id, x_2180, y_2180, anchor_lon, anchor_lat, radius_m=None, max_features=None):
        calls["radius_m"] = radius_m
        return []

    monkeypatch.setattr(wfs_search, "geocode_address_points", fake_address_geocode)
    monkeypatch.setattr(wfs_search, "find_parcel_by_xy", fake_find_parcel_by_xy)
    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)

    result = await wfs_search._gather_nearby_parcels(None, "Raciechowice")

    # 2) zwykła gałąź (nie "Powiat X") NIE przekazuje własnego radius_m —
    # musi więc polegać na (małym) defaultcie sprawdzonym w kroku 1, tak
    # jak gałąź is_powiat_query i scan_wfs_for_parcel_number przekazują
    # SWOJE, jawne wartości.
    assert calls["radius_m"] is None
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_search_propagates_gather_error(monkeypatch):
    async def fake_gather_error(client, place_query):
        return {"status": "error", "message": "Nie znaleziono miejscowości."}
    monkeypatch.setattr(wfs_search, "_gather_nearby_parcels", fake_gather_error)
    result = await wfs_search.search_parcels_universal(None, "Nieistniejąca Wieś", target_area_m2=1000)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# services.uldk.find_parcel_by_id_direct — GetParcelById fallback (2026-09-03,
# added after Klaudia confirmed a real, valid TERYT id — verified
# independently via polska.e-mapa.net — came back empty from the primary
# GetParcelByIdOrNr lookup).
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_params = None

    async def get(self, url, params=None, timeout=None):
        self.last_params = params
        return _FakeResponse(self._response_text)


@pytest.mark.asyncio
async def test_find_parcel_by_id_direct_parses_successful_response():
    client = _FakeClient("0\n121505_2.0001.636/3|małopolskie|suski|Jordanów|636/3")
    result = await uldk.find_parcel_by_id_direct(client, "121505_2.0001.636/3")
    assert result == {
        "teryt_id": "121505_2.0001.636/3", "voivodeship": "małopolskie", "county": "suski",
        "commune": "Jordanów", "parcel_no": "636/3",
    }
    assert client.last_params["request"] == "GetParcelById"


@pytest.mark.asyncio
async def test_find_parcel_by_id_direct_returns_none_when_not_found():
    client = _FakeClient("-1 nie znaleziono")
    result = await uldk.find_parcel_by_id_direct(client, "000000_0.0000.0/0")
    assert result is None


# ---------------------------------------------------------------------------
# services.geocoding.geocode_powiat_gmina_prefixes — /api/resolve stage-4
# fallback (2026-09-03): "Name" in "Name Number" might be a powiat, not a
# gmina (e.g. "suski 636/3") - added after gmina- and obręb-name attempts
# both failed to resolve a real, independently-verified parcel.
# ---------------------------------------------------------------------------

class _FakeJsonResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakePostClient:
    def __init__(self, data):
        self._data = data
        self.last_json = None

    async def post(self, url, json=None, timeout=None):
        self.last_json = json
        return _FakeJsonResponse(self._data)


@pytest.mark.asyncio
async def test_geocode_powiat_gmina_prefixes_extracts_and_dedupes_gminas():
    data = [
        {
            "others": [
                {"teryt": "1215052", "gm_nazwa": "Jordanów"},
                {"teryt": "1215043", "gm_nazwa": "Bystra-Sidzina"},
                {"teryt": "1215052", "gm_nazwa": "Jordanów"},  # duplikat
            ]
        }
    ]
    client = _FakePostClient(data)
    result = await geocoding.geocode_powiat_gmina_prefixes(client, "suski")
    assert client.last_json == {"reqs": [{"pow_nazwa": "suski"}]}
    prefixes = {g["gmina_prefix"] for g in result}
    assert prefixes == {"121505_2", "121504_3"}
    assert len(result) == 2  # duplikat odfiltrowany


@pytest.mark.asyncio
async def test_geocode_powiat_gmina_prefixes_returns_empty_on_request_failure():
    class _FailingClient:
        async def post(self, url, json=None, timeout=None):
            raise RuntimeError("network down")

    result = await geocoding.geocode_powiat_gmina_prefixes(_FailingClient(), "suski")
    assert result == []


@pytest.mark.asyncio
async def test_geocode_powiat_gmina_prefixes_includes_coordinates_when_present():
    # scan_wfs_for_parcel_number (wfs_search.py) needs an anchor lon/lat per
    # gmina candidate — added 2026-09-03 alongside it.
    data = [{"others": [
        {"teryt": "1215052", "gm_nazwa": "Jordanów", "geometry": {"coordinates": [19.68, 49.63]}},
    ]}]
    result = await geocoding.geocode_powiat_gmina_prefixes(_FakePostClient(data), "suski")
    assert result[0]["lon"] == 19.68
    assert result[0]["lat"] == 49.63


@pytest.mark.asyncio
async def test_geocode_gmina_candidates_includes_coordinates_when_present():
    data = [{"others": [
        {
            "teryt": "1215052", "gm_nazwa": "Jordanów", "pow_nazwa": "suski", "woj_nazwa": "małopolskie",
            "geometry": {"coordinates": [19.68, 49.63]},
        },
    ]}]
    result = await geocoding.geocode_gmina_candidates(_FakePostClient(data), "Jordanów")
    assert result[0]["lon"] == 19.68
    assert result[0]["lat"] == 49.63


@pytest.mark.asyncio
async def test_geocode_gmina_candidates_missing_geometry_omits_coordinates():
    data = [{"others": [
        {"teryt": "1215052", "gm_nazwa": "Jordanów", "pow_nazwa": "suski", "woj_nazwa": "małopolskie"},
    ]}]
    result = await geocoding.geocode_gmina_candidates(_FakePostClient(data), "Jordanów")
    assert "lon" not in result[0]
    assert "lat" not in result[0]


# ---------------------------------------------------------------------------
# services.wfs_search.scan_wfs_for_parcel_number — geometry-based fallback
# for /api/resolve's 'Name Number' path (2026-09-03), added after Klaudia
# confirmed a real parcel was unfindable by ANY ID-based ULDK query, and
# reported the identical symptom on a different provider (polska.e-mapa.net)
# for an unrelated parcel — findable only by browsing to a neighbour first.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_wfs_for_parcel_number_matches_by_suffix(monkeypatch):
    async def fake_enumerate(client, teryt_id, x_2180, y_2180, anchor_lon, anchor_lat, radius_m=None, max_features=None):
        assert teryt_id == "121505_2.0000.0"
        return [(19.68, 49.63), (19.69, 49.64), (19.70, 49.65)]

    async def fake_find_parcel_by_xy(client, lon, lat):
        by_point = {
            (19.68, 49.63): {"teryt_id": "121505_2.0001.636/3", "commune": "Jordanów"},
            (19.69, 49.64): {"teryt_id": "121505_2.0001.100", "commune": "Jordanów"},
            (19.70, 49.65): None,
        }
        return by_point[(lon, lat)]

    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)
    monkeypatch.setattr(wfs_search, "find_parcel_by_xy", fake_find_parcel_by_xy)

    result = await wfs_search.scan_wfs_for_parcel_number(None, 19.68, 49.63, "121505_2", "636/3")

    assert len(result) == 1
    assert result[0]["teryt_id"] == "121505_2.0001.636/3"


@pytest.mark.asyncio
async def test_scan_wfs_for_parcel_number_no_match_returns_empty(monkeypatch):
    async def fake_enumerate(*args, **kwargs):
        return [(19.68, 49.63)]

    async def fake_find_parcel_by_xy(client, lon, lat):
        return {"teryt_id": "121505_2.0001.999", "commune": "Jordanów"}

    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)
    monkeypatch.setattr(wfs_search, "find_parcel_by_xy", fake_find_parcel_by_xy)

    result = await wfs_search.scan_wfs_for_parcel_number(None, 19.68, 49.63, "121505_2", "636/3")
    assert result == []


@pytest.mark.asyncio
async def test_scan_wfs_for_parcel_number_enumerate_failure_returns_empty(monkeypatch):
    async def fake_enumerate(*args, **kwargs):
        raise RuntimeError("WFS server down")

    monkeypatch.setattr(wfs_search, "enumerate_parcel_points_in_area", fake_enumerate)

    result = await wfs_search.scan_wfs_for_parcel_number(None, 19.68, 49.63, "121505_2", "636/3")
    assert result == []


# ---------------------------------------------------------------------------
# services.zoning — Plan Ogólny / OUZ keyword flags + "no plan" note, added
# 2026-09-03 (competitor research turned up that the OUZ rule that took
# effect 2026-09-01 makes "brak MPZP" no longer mean "warunki zabudowy
# możliwe" — see HANDOFF.md). Best-effort keyword matching only, since
# KIAPP's actual attribute schema can't be verified live in this sandbox.
# ---------------------------------------------------------------------------

def test_mentions_any_case_insensitive():
    assert zoning._mentions_any("Ustalenia PLANU OGÓLNEGO gminy", zoning._PLAN_OGOLNY_KEYWORDS)
    assert not zoning._mentions_any("Miejscowy plan zagospodarowania", zoning._PLAN_OGOLNY_KEYWORDS)


def test_mentions_any_ouz_keyword():
    assert zoning._mentions_any("działka leży w obszarze uzupełnienia zabudowy", zoning._OUZ_KEYWORDS)
    assert not zoning._mentions_any("brak informacji o strefach", zoning._OUZ_KEYWORDS)


class _FakeFeatureInfoResponse:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_try_zoning_source_sets_plan_ogolny_and_ouz_flags(monkeypatch):
    html = (
        "<table><tr><td>Rodzaj aktu</td><td>Plan ogólny gminy — strefa OUZ, "
        "obszar uzupełnienia zabudowy nr 3</td></tr></table>"
    )

    async def fake_has_plan(client, url, layer, x, y, half_extent_m=15.0):
        return True

    async def fake_get_feature_info(client, url, layers, x, y, half_extent_m=15.0):
        return _FakeFeatureInfoResponse(html)

    monkeypatch.setattr(zoning, "_mpzp_has_plan_drawn", fake_has_plan)
    monkeypatch.setattr(zoning, "wms_get_feature_info", fake_get_feature_info)

    result = await zoning._try_zoning_source(None, "http://x", "layer", 0.0, 0.0, "Test")

    assert result["found"] == "yes"
    assert result["mentions_plan_ogolny"] is True
    assert result["mentions_ouz"] is True


@pytest.mark.asyncio
async def test_try_zoning_source_no_flags_for_plain_mpzp(monkeypatch):
    html = "<table><tr><td>Symbol</td><td>1MN — zabudowa jednorodzinna</td></tr></table>"

    async def fake_has_plan(client, url, layer, x, y, half_extent_m=15.0):
        return True

    async def fake_get_feature_info(client, url, layers, x, y, half_extent_m=15.0):
        return _FakeFeatureInfoResponse(html)

    monkeypatch.setattr(zoning, "_mpzp_has_plan_drawn", fake_has_plan)
    monkeypatch.setattr(zoning, "wms_get_feature_info", fake_get_feature_info)

    result = await zoning._try_zoning_source(None, "http://x", "layer", 0.0, 0.0, "Test")

    assert result["found"] == "yes"
    assert result["mentions_plan_ogolny"] is False
    assert result["mentions_ouz"] is False


@pytest.mark.asyncio
async def test_get_zoning_attaches_ouz_note_when_no_plan_found_anywhere(monkeypatch):
    async def fake_has_plan(client, url, layer, x, y, half_extent_m=15.0):
        return False

    monkeypatch.setattr(zoning, "_mpzp_has_plan_drawn", fake_has_plan)

    result = await zoning.get_zoning(None, 0.0, 0.0)

    assert result["found"] == "no"
    assert "obszar" in result["note"].lower()
    assert "2026" in result["note"]


@pytest.mark.asyncio
async def test_get_zoning_kiapp_error_does_not_mask_kimpzp_success(monkeypatch):
    """Fixed 2026-09-04 — a transient KIAPP failure used to short-circuit
    get_zoning() and throw away a concurrently-successful KIMPZP result."""
    html = "<table><tr><td>Symbol</td><td>1MN — zabudowa jednorodzinna</td></tr></table>"

    async def fake_has_plan(client, url, layer, x, y, half_extent_m=15.0):
        if url == KIAPP_URL:
            raise RuntimeError("KIAPP niedostępny")
        return True

    async def fake_get_feature_info(client, url, layers, x, y, half_extent_m=15.0):
        return _FakeFeatureInfoResponse(html)

    monkeypatch.setattr(zoning, "_mpzp_has_plan_drawn", fake_has_plan)
    monkeypatch.setattr(zoning, "wms_get_feature_info", fake_get_feature_info)

    result = await zoning.get_zoning(None, 0.0, 0.0)

    assert result["status"] == "ok"
    assert result["found"] == "yes"
    assert result["source"] == "MPZP (KIMPZP)"


@pytest.mark.asyncio
async def test_get_zoning_both_sources_erroring_surfaces_error_not_no_plan(monkeypatch):
    async def fake_has_plan(client, url, layer, x, y, half_extent_m=15.0):
        raise RuntimeError("usługa niedostępna")

    monkeypatch.setattr(zoning, "_mpzp_has_plan_drawn", fake_has_plan)

    result = await zoning.get_zoning(None, 0.0, 0.0)

    assert result["status"] == "error"


class _FlakyGetMapClient:
    """Raises httpx.ConnectTimeout on the first call, then returns a valid
    (non-blank) PNG — reproduces the live bug Klaudia reported 2026-09-04:
    a KIMPZP GetMap probe hit a brief ConnectTimeout even though the plan
    layer visibly rendered on the map moments later, and _mpzp_has_plan_drawn
    used a raw single-shot client.get() with no retry, so that transient
    hiccup surfaced as a full section failure instead of succeeding on retry."""

    def __init__(self):
        self.calls = 0

    async def get(self, url, params=None, timeout=None, follow_redirects=None):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ConnectTimeout("")
        return _FakePngResponse(_png_response(50))


@pytest.mark.asyncio
async def test_mpzp_has_plan_drawn_retries_on_connect_timeout():
    client = _FlakyGetMapClient()

    result = await zoning._mpzp_has_plan_drawn(client, "http://x", "layer", 0.0, 0.0)

    assert result is True
    assert client.calls == 2


# ---------------------------------------------------------------------------
# services.cache — generic SQLite cache-aside, added 2026-09-04 after the
# performance investigation ("Plan Pamięci Podręcznej"). Lazy cache-aside,
# NOT a background poller — see the module docstring for why.
# ---------------------------------------------------------------------------

@pytest.fixture
def cache_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DB_PATH", str(tmp_path / "test_cache.db"))
    cache._reset_for_tests()
    yield
    cache._reset_for_tests()


@pytest.mark.asyncio
async def test_get_or_fetch_miss_calls_fetch_and_stores(cache_db):
    calls = []

    async def fetch():
        calls.append(1)
        return {"status": "ok", "value": 42}

    result = await cache.get_or_fetch("svc", "key1", 1000.0, fetch)

    assert len(calls) == 1
    assert result["value"] == 42
    assert result["cached"] is False
    assert "fetched_at" in result


@pytest.mark.asyncio
async def test_get_or_fetch_hit_skips_fetch(cache_db):
    calls = []

    async def fetch():
        calls.append(1)
        return {"status": "ok", "value": 42}

    await cache.get_or_fetch("svc", "key1", 1000.0, fetch)
    result = await cache.get_or_fetch("svc", "key1", 1000.0, fetch)

    assert len(calls) == 1  # druga próba nie wywołała fetch ponownie
    assert result["value"] == 42
    assert result["cached"] is True


@pytest.mark.asyncio
async def test_get_or_fetch_expired_entry_refetches(cache_db):
    calls = []

    async def fetch():
        calls.append(1)
        return {"status": "ok", "value": len(calls)}

    await cache.get_or_fetch("svc", "key1", 0.0, fetch)  # TTL=0 -> natychmiast "wygasłe"
    result = await cache.get_or_fetch("svc", "key1", 0.0, fetch)

    assert len(calls) == 2
    assert result["value"] == 2
    assert result["cached"] is False


@pytest.mark.asyncio
async def test_get_or_fetch_error_result_not_cached(cache_db):
    calls = []

    async def fetch():
        calls.append(1)
        return {"status": "error", "message": "usługa niedostępna"}

    r1 = await cache.get_or_fetch("svc", "key1", 1000.0, fetch)
    r2 = await cache.get_or_fetch("svc", "key1", 1000.0, fetch)

    assert len(calls) == 2  # błąd nigdy nie trafia do cache'u, druga próba znów odpytuje
    assert "cached" not in r1
    assert r2["status"] == "error"


@pytest.mark.asyncio
async def test_get_or_fetch_keys_are_independent(cache_db):
    async def fetch_a():
        return {"status": "ok", "value": "a"}

    async def fetch_b():
        return {"status": "ok", "value": "b"}

    result_a = await cache.get_or_fetch("svc", "parcel-a", 1000.0, fetch_a)
    result_b = await cache.get_or_fetch("svc", "parcel-b", 1000.0, fetch_b)

    assert result_a["value"] == "a"
    assert result_b["value"] == "b"


@pytest.mark.asyncio
async def test_get_or_fetch_services_are_independent(cache_db):
    async def fetch_x():
        return {"status": "ok", "value": "x"}

    async def fetch_y():
        return {"status": "ok", "value": "y"}

    result_x = await cache.get_or_fetch("service-x", "same-key", 1000.0, fetch_x)
    result_y = await cache.get_or_fetch("service-y", "same-key", 1000.0, fetch_y)

    assert result_x["value"] == "x"
    assert result_y["value"] == "y"


@pytest.mark.asyncio
async def test_get_or_fetch_concurrent_first_touch_does_not_race(cache_db):
    """Regression, found live 2026-09-04 while testing the persistent-httpx-
    client performance optimization: main.py fans out up to 12 concurrent
    get_or_fetch calls per /api/analyze via asyncio.gather, hitting a
    completely fresh cache.db — the NORMAL state after every deploy, since
    the cache resets (see the module docstring). Before _conn_lock existed,
    several worker threads could race _get_conn() on that very first touch:
    either seeing the table not yet created ("no such table: cache_entries")
    or racing each other's implicit transactions on the one shared
    connection ("cannot commit - no transaction is active")."""
    async def fetch(i):
        return {"status": "ok", "value": i}

    results = await asyncio.gather(*[
        cache.get_or_fetch(f"svc{i % 3}", f"key{i}", 1000.0, lambda i=i: fetch(i))
        for i in range(24)
    ])

    assert [r["value"] for r in results] == list(range(24))
    assert all(r["cached"] is False for r in results)


# ---------------------------------------------------------------------------
# services.verdict.build_verdict — synthesized score/verdict + full status
# checklist (2026-09-04, item 6 from "Rozpoznanie Działkopedii", extended
# same day after Klaudia shared Działkopedia's real free-tier report: its
# whole result is a compact row list — label/pill/one-liner — with a 3-way
# risk/warning/ok count, not just a list of problems). Pure, deterministic
# point-based rules — every deduction is named in a 'row', never a black
# box, and every row (including the clean ones) is returned, not just the
# flagged ones.
# ---------------------------------------------------------------------------

def _clean_signals():
    return dict(
        landslide={"status": "ok", "has_landslide": False},
        zoning={"status": "ok", "found": "yes", "table": []},
        flood_zone={"status": "ok", "in_flood_zone": False},
        waterlogging={"status": "ok", "at_risk": False},
        utilities={"status": "ok", "utilities": [{"present": True}] * 4 + [{"present": False}] * 2},
        nearest_road={"status": "ok", "found": "yes", "is_fallback_powiatowa": False},
        protected_areas={"status": "ok", "areas": []},
        mining_areas={"status": "ok", "has_mining_area": False},
        air_quality={
            "status": "ok", "station_name": "Testowa", "distance_m": 1000,
            "pollutant": "PM2.5", "value": 10.0, "unit": "µg/m³",
            "measured_at": "2026-09-04 10:00:00", "attribution": "Źródło danych: GIOŚ — EKOINFONET",
        },
    )


def _row(result, key):
    return next(r for r in result["rows"] if r["key"] == key)


def test_build_verdict_all_clean_scores_100_dobra():
    result = verdict.build_verdict(**_clean_signals())
    assert result["score"] == 100
    assert result["level"] == "dobra"
    assert result["incomplete_sections"] == []
    assert result["counts"] == {"risk": 0, "warning": 0, "ok": 9, "unknown": 0}
    assert all(r["tier"] == "ok" for r in result["rows"])


def test_build_verdict_landslide_and_flood_are_risk_and_stack():
    signals = _clean_signals()
    signals["landslide"] = {"status": "ok", "has_landslide": True}
    signals["flood_zone"] = {"status": "ok", "in_flood_zone": True}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100 - 40 - 35
    assert result["level"] == "wysokie_ryzyko"
    assert result["counts"]["risk"] == 2
    assert _row(result, "landslide")["tier"] == "risk"
    assert _row(result, "flood_zone")["tier"] == "risk"


def test_build_verdict_score_never_goes_below_zero():
    signals = _clean_signals()
    signals["landslide"] = {"status": "ok", "has_landslide": True}
    signals["flood_zone"] = {"status": "ok", "in_flood_zone": True}
    signals["waterlogging"] = {"status": "ok", "at_risk": True}
    signals["zoning"] = {"status": "ok", "found": "no"}
    signals["nearest_road"] = {"status": "ok", "found": "no"}
    signals["utilities"] = {"status": "ok", "utilities": [{"present": False}] * 6}
    signals["protected_areas"] = {"status": "ok", "areas": [{"name": "Park X"}]}
    signals["mining_areas"] = {"status": "ok", "has_mining_area": True}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 0
    assert result["level"] == "wysokie_ryzyko"


def test_build_verdict_failed_section_is_incomplete_and_gets_unknown_row():
    """Fixed 2026-09-04 — Klaudia reported the checklist had NO row at all
    for a section whose fetch errored (zoning, in her live report), only
    the one summary sentence above it. A failed section now gets its own
    'unknown' row (never scored) in addition to the summary line."""
    signals = _clean_signals()
    signals["landslide"] = {"status": "error", "message": "usługa niedostępna"}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100  # brak danych nie obniża wyniku
    assert "zagrożenie osuwiskowe" in result["incomplete_sections"]
    row = _row(result, "landslide")
    assert row["tier"] == "unknown"
    assert result["counts"]["unknown"] == 1


def test_build_verdict_no_utilities_detected_is_warning():
    signals = _clean_signals()
    signals["utilities"] = {"status": "ok", "utilities": [{"present": False}] * 6}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100 - 15
    row = _row(result, "utilities")
    assert row["tier"] == "warning"
    assert "mediów" in row["text"]


def test_build_verdict_protected_area_names_included_in_row_text():
    signals = _clean_signals()
    signals["protected_areas"] = {"status": "ok", "areas": [{"name": "Rezerwat Wiślany"}]}
    result = verdict.build_verdict(**signals)
    assert "Rezerwat Wiślany" in _row(result, "protected_areas")["text"]


def test_build_verdict_mining_area_is_warning():
    signals = _clean_signals()
    signals["mining_areas"] = {"status": "ok", "has_mining_area": True}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100 - 10
    assert _row(result, "mining_areas")["tier"] == "warning"


def test_build_verdict_no_missing_plan_flag_when_partial():
    # status "partial" (plan wykryty, ale szczegóły nie doszły) nie powinien
    # trafić do incomplete_sections — to nie jest "brak planu", tylko wiersz
    # ostrzegawczy bez odjęcia punktów.
    signals = _clean_signals()
    signals["zoning"] = {"status": "partial", "found": "yes", "message": "timeout"}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100
    assert result["incomplete_sections"] == []
    assert _row(result, "zoning")["tier"] == "warning"


# ---------------------------------------------------------------------------
# services.due_diligence.build_due_diligence_checklist — the 25-point
# pre-purchase checklist (2026-09-04), marking which steps this app's own
# data already covers. Purely presentational: no new data source.
# ---------------------------------------------------------------------------

def test_due_diligence_checklist_has_25_items_in_7_categories():
    result = due_diligence.build_due_diligence_checklist(set())
    assert result["total"] == 25
    assert len(result["categories"]) == 7
    assert result["checked"] == 0


def test_due_diligence_checklist_marks_covered_items():
    covered = {"flood_zone", "landslide", "road"}
    result = due_diligence.build_due_diligence_checklist(covered)
    assert result["checked"] == 3
    flat_items = [item for cat in result["categories"] for item in cat["items"]]
    checked_texts = {i["text"] for i in flat_items if i["auto_checked"]}
    assert "Sprawdź strefę zalewową" in checked_texts
    assert "Sprawdź zagrożenie osuwiskowe i warunki gruntowe" in checked_texts
    assert "Sprawdź dostęp do drogi publicznej" in checked_texts


def test_due_diligence_checklist_items_with_no_coverage_never_auto_checked():
    # np. wizyta osobista na działce — appka nigdy nie może tego sama sprawdzić.
    result = due_diligence.build_due_diligence_checklist({"flood_zone", "landslide", "road", "power",
                                                            "water_sewage", "protected_areas", "zoning_mpzp",
                                                            "valuation"})
    flat_items = [item for cat in result["categories"] for item in cat["items"]]
    visit_item = next(i for i in flat_items if i["text"] == "Odwiedź działkę osobiście")
    assert visit_item["auto_checked"] is False


def test_due_diligence_checklist_category_counts_sum_to_total():
    result = due_diligence.build_due_diligence_checklist({"power", "water_sewage"})
    assert sum(cat["total"] for cat in result["categories"]) == 25
    assert sum(cat["checked"] for cat in result["categories"]) == result["checked"] == 2


# ---------------------------------------------------------------------------
# services.nature.get_protected_areas / services.geology.check_mining_areas
# — items 8/9 from the competitor analysis (2026-09-04). Neither endpoint
# is verified live (see HANDOFF.md) — these tests cover the parsing/
# containment/CRS-detection logic against fake responses, not the real
# services.
# ---------------------------------------------------------------------------

class _FakeArcgisResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeGetJsonClient:
    def __init__(self, data):
        self._data = data

    async def get(self, url, params=None, timeout=None, follow_redirects=None):
        return _FakeArcgisResponse(self._data)


class _FailingGetClient:
    async def get(self, url, params=None, timeout=None, follow_redirects=None):
        raise RuntimeError("network down")


def _square_around(cx, cy, half_extent=100.0):
    return [[
        [cx - half_extent, cy - half_extent], [cx + half_extent, cy - half_extent],
        [cx + half_extent, cy + half_extent], [cx - half_extent, cy + half_extent],
        [cx - half_extent, cy - half_extent],
    ]]


@pytest.mark.asyncio
async def test_get_protected_areas_point_inside_polygon_epsg2180():
    data = {"features": [{
        "id": "ParkiNarodowe.42",
        "geometry": {"type": "Polygon", "coordinates": _square_around(500000.0, 300000.0)},
        "properties": {"nazwa": "Testowy Park Narodowy"},
    }]}
    result = await nature.get_protected_areas(_FakeGetJsonClient(data), 500000.0, 300000.0)
    assert result["status"] == "ok"
    assert result["areas"] == [{"name": "Testowy Park Narodowy", "kind": "park narodowy"}]


@pytest.mark.asyncio
async def test_get_protected_areas_bbox_hit_but_not_containing_is_excluded():
    # Poligon daleko od punktu zapytania — nie powinien trafić do wyniku,
    # nawet jeśli serwer zwrócił go w odpowiedzi na (celowo szeroki) bbox.
    data = {"features": [{
        "id": "Rezerwaty.7",
        "geometry": {"type": "Polygon", "coordinates": _square_around(600000.0, 400000.0)},
        "properties": {"nazwa": "Daleki Rezerwat"},
    }]}
    result = await nature.get_protected_areas(_FakeGetJsonClient(data), 500000.0, 300000.0)
    assert result["status"] == "ok"
    assert result["areas"] == []


@pytest.mark.asyncio
async def test_get_protected_areas_detects_wgs84_response_and_transforms_query_point():
    from services.nature import _to_4326

    qlon, qlat = _to_4326.transform(500000.0, 300000.0)
    d = 0.001
    coords = [[[qlon - d, qlat - d], [qlon + d, qlat - d], [qlon + d, qlat + d], [qlon - d, qlat + d], [qlon - d, qlat - d]]]
    data = {"features": [{
        "id": "ParkiKrajobrazowe.3",
        "geometry": {"type": "Polygon", "coordinates": coords},
        "properties": {"nazwa": "Testowy Park Krajobrazowy"},
    }]}
    result = await nature.get_protected_areas(_FakeGetJsonClient(data), 500000.0, 300000.0)
    assert result["status"] == "ok"
    assert result["areas"] == [{"name": "Testowy Park Krajobrazowy", "kind": "park krajobrazowy"}]


@pytest.mark.asyncio
async def test_get_protected_areas_no_features_returns_empty_ok():
    result = await nature.get_protected_areas(_FakeGetJsonClient({"features": []}), 500000.0, 300000.0)
    assert result == {"status": "ok", "areas": []}


@pytest.mark.asyncio
async def test_get_protected_areas_dedupes_same_name():
    poly = _square_around(500000.0, 300000.0)
    data = {"features": [
        {"id": "ParkiNarodowe.1", "geometry": {"type": "Polygon", "coordinates": poly}, "properties": {"nazwa": "X"}},
        {"id": "ParkiNarodowe.2", "geometry": {"type": "Polygon", "coordinates": poly}, "properties": {"nazwa": "X"}},
    ]}
    result = await nature.get_protected_areas(_FakeGetJsonClient(data), 500000.0, 300000.0)
    assert len(result["areas"]) == 1


@pytest.mark.asyncio
async def test_get_protected_areas_request_failure_returns_error(monkeypatch):
    monkeypatch.setattr(nature.asyncio, "sleep", _no_sleep)
    result = await nature.get_protected_areas(_FailingGetClient(), 500000.0, 300000.0)
    assert result["status"] == "error"


class _EmptyBodyResponse:
    """Models GDOŚ's confirmed-live quirk: HTTP 200 with a body that isn't
    valid JSON (raise_for_status() doesn't object to a 200 — the failure
    only surfaces when something calls .json())."""

    def raise_for_status(self):
        pass

    def json(self):
        import json
        return json.loads("")  # raises json.JSONDecodeError, same as a real empty body


class _EmptyThenOkClient:
    def __init__(self, ok_data):
        self._ok_data = ok_data
        self.calls = 0

    async def get(self, url, params=None, timeout=None, follow_redirects=None):
        self.calls += 1
        if self.calls == 1:
            return _EmptyBodyResponse()
        return _FakeArcgisResponse(self._ok_data)


@pytest.mark.asyncio
async def test_get_protected_areas_retries_on_empty_body_then_succeeds(monkeypatch):
    monkeypatch.setattr(nature.asyncio, "sleep", _no_sleep)
    client = _EmptyThenOkClient({"features": []})

    result = await nature.get_protected_areas(client, 500000.0, 300000.0)

    assert result == {"status": "ok", "areas": []}
    assert client.calls == 2


@pytest.mark.asyncio
async def test_check_mining_areas_present_extracts_names_from_value_field():
    data = {"results": [{"value": "Obszar górniczy Bogdanka I"}, {"value": "Teren górniczy Bogdanka I"}]}
    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    result = await geology.check_mining_areas(_FakeGetJsonClient(data), geometry)
    assert result["status"] == "ok"
    assert result["has_mining_area"] is True
    assert result["names"] == ["Obszar górniczy Bogdanka I", "Teren górniczy Bogdanka I"]


@pytest.mark.asyncio
async def test_check_mining_areas_absent_when_no_results():
    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    result = await geology.check_mining_areas(_FakeGetJsonClient({"results": []}), geometry)
    assert result["status"] == "ok"
    assert result["has_mining_area"] is False
    assert result["names"] == []


@pytest.mark.asyncio
async def test_check_mining_areas_service_error_response():
    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    result = await geology.check_mining_areas(_FakeGetJsonClient({"error": {"message": "boom"}}), geometry)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_check_mining_areas_request_failure_returns_error():
    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    result = await geology.check_mining_areas(_FailingGetClient(), geometry)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# services.utilities — KIUT GetMap pixel-counting presence check. Fixed
# 2026-09-04, reported live by Klaudia ("media przestały działać" on the
# test parcel): each of the 6 layers catches its OWN request failure and
# silently reports present=False, so a total KIUT outage used to look
# identical to "checked, genuinely no utilities nearby" — both to the
# verdict's scoring AND to the chip grid. Now: all 6 layers failing makes
# the whole check report status="error" (goes to incomplete_sections,
# never scored) instead of a falsely confident "ok, nothing found".
# ---------------------------------------------------------------------------

def _png_response(non_transparent_pixels):
    img = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    for i in range(non_transparent_pixels):
        img.putpixel((i % 240, i // 240), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakePngResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeUtilitiesClient:
    """Routes by layer name (params["LAYERS"]) — some layers return an
    image with real content, some return blank/transparent, some raise."""

    def __init__(self, layer_pixels, layer_errors=()):
        self._layer_pixels = layer_pixels
        self._layer_errors = set(layer_errors)

    async def get(self, url, params=None, follow_redirects=None):
        layer = params["LAYERS"]
        if layer in self._layer_errors:
            raise RuntimeError("KIUT niedostępny dla tej warstwy")
        return _FakePngResponse(_png_response(self._layer_pixels.get(layer, 0)))


@pytest.mark.asyncio
async def test_check_utilities_detects_present_above_threshold():
    client = _FakeUtilitiesClient({"przewod_wodociagowy": 500, "przewod_elektroenergetyczny": 700})
    result = await utilities.check_utilities(client, 500000.0, 300000.0)
    assert result["status"] == "ok"
    present_keys = {u["key"] for u in result["utilities"] if u["present"]}
    assert present_keys == {"woda", "prad"}


@pytest.mark.asyncio
async def test_check_utilities_below_threshold_is_absent_not_error():
    client = _FakeUtilitiesClient({})  # all layers return a blank (fully transparent) image
    result = await utilities.check_utilities(client, 500000.0, 300000.0)
    assert result["status"] == "ok"
    assert all(not u["present"] and not u.get("error") for u in result["utilities"])


@pytest.mark.asyncio
async def test_check_utilities_all_layers_failing_is_error_not_false_ok():
    all_layers = ["przewod_wodociagowy", "przewod_kanalizacyjny", "przewod_gazowy",
                  "przewod_elektroenergetyczny", "przewod_cieplowniczy", "przewod_telekomunikacyjny"]
    client = _FakeUtilitiesClient({}, layer_errors=all_layers)
    result = await utilities.check_utilities(client, 500000.0, 300000.0)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_check_utilities_partial_failure_stays_ok_with_error_flag_per_layer():
    client = _FakeUtilitiesClient({"przewod_wodociagowy": 500}, layer_errors=["przewod_gazowy"])
    result = await utilities.check_utilities(client, 500000.0, 300000.0)
    assert result["status"] == "ok"
    by_key = {u["key"]: u for u in result["utilities"]}
    assert by_key["woda"]["present"] is True
    assert by_key["gaz"]["error"] is True
    assert by_key["gaz"]["present"] is False


@pytest.mark.asyncio
async def test_check_utilities_present_includes_distance_m():
    """Added 2026-09-04, requested by Klaudia after comparing against
    Działkopedia's "71m dobry dojazd"-style output — a present utility
    chip should also say how far, not just yes/no. The tile is 240x240 px
    over a 120m bbox (0.5 m/px); a cluster of pixels starting 40px right
    of the tile center should report ~20m."""
    size_px = 240
    center = (size_px - 1) / 2
    offset_px = 40

    def cluster_image():
        img = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
        y = round(center)
        for i in range(100):  # well above threshold_px=60
            x = min(round(center) + offset_px + i, size_px - 1)
            img.putpixel((x, y), (0, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    class _ClusterClient:
        async def get(self, url, params=None, follow_redirects=None):
            if params["LAYERS"] == "przewod_wodociagowy":
                return _FakePngResponse(cluster_image())
            return _FakePngResponse(_png_response(0))

    result = await utilities.check_utilities(_ClusterClient(), 500000.0, 300000.0)
    woda = {u["key"]: u for u in result["utilities"]}["woda"]
    assert woda["present"] is True
    assert woda["distance_m"] == pytest.approx(20, abs=1)


# ---------------------------------------------------------------------------
# services.air_quality — GIOŚ nearest-station PM2.5/PM10 lookup, added
# 2026-09-04. get_air_quality makes three distinct kinds of calls (station
# list, sensors-per-station, data-per-sensor), so it needs a fake client
# that routes by URL path rather than the single-fixed-payload fakes above.
# ---------------------------------------------------------------------------

class _FakeAirQualityResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeAirQualityClient:
    def __init__(self, stations, sensors_by_station, data_by_sensor):
        self._stations = stations
        self._sensors_by_station = sensors_by_station
        self._data_by_sensor = data_by_sensor

    async def get(self, url, params=None, timeout=None):
        if url.endswith("/station/findAll"):
            page = (params or {}).get("page", 0)
            if page == 0:
                return _FakeAirQualityResponse({"Lista stacji pomiarowych": self._stations, "totalPages": 1})
            return _FakeAirQualityResponse({"Lista stacji pomiarowych": [], "totalPages": 1})
        if "/station/sensors/" in url:
            station_id = int(url.rsplit("/", 1)[-1])
            sensors = self._sensors_by_station.get(station_id, [])
            return _FakeAirQualityResponse({"Lista stanowisk pomiarowych dla podanej stacji": sensors})
        if "/data/getData/" in url:
            sensor_id = int(url.rsplit("/", 1)[-1])
            rows = self._data_by_sensor.get(sensor_id, [])
            return _FakeAirQualityResponse({"Lista danych pomiarowych": rows})
        raise AssertionError(f"unexpected URL: {url}")


def _aq_station(station_id, name, lon, lat):
    return {"Identyfikator stacji": station_id, "Nazwa stacji": name, "WGS84 φ N": str(lat), "WGS84 λ E": str(lon)}


def _aq_sensor(sensor_id, code):
    return {"Identyfikator stanowiska": sensor_id, "Wskaźnik - kod": code}


@pytest.mark.asyncio
async def test_get_air_quality_nearest_station_pm25(cache_db):
    stations = [_aq_station(1, "Bliska", 19.0, 50.0), _aq_station(2, "Daleka", 25.0, 54.0)]
    sensors = {1: [_aq_sensor(101, "PM2.5"), _aq_sensor(102, "PM10")]}
    data = {101: [{"Wartość": 12.5, "Data": "2026-09-04 09:00:00"}]}
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["station_name"] == "Bliska"
    assert result["pollutant"] == "PM2.5"
    assert result["value"] == 12.5
    assert result["unit"] == "µg/m³"
    assert "GIOŚ" in result["attribution"]


@pytest.mark.asyncio
async def test_get_air_quality_falls_back_to_pm10_when_no_pm25(cache_db):
    stations = [_aq_station(1, "Stacja", 19.0, 50.0)]
    sensors = {1: [_aq_sensor(201, "PM10")]}
    data = {201: [{"Wartość": 30.0, "Data": "2026-09-04 09:00:00"}]}
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["pollutant"] == "PM10"
    assert result["value"] == 30.0


@pytest.mark.asyncio
async def test_get_air_quality_falls_back_to_pm10_on_same_station_when_pm25_has_no_reading(cache_db):
    """Fixed 2026-09-04 — used to abandon the nearest station entirely on a
    PM2.5 miss, without trying that same station's own PM10 sensor first,
    needlessly reporting a farther station instead."""
    stations = [_aq_station(1, "Bliska", 19.0, 50.0), _aq_station(2, "Daleka", 25.0, 54.0)]
    sensors = {
        1: [_aq_sensor(101, "PM2.5"), _aq_sensor(102, "PM10")],
        2: [_aq_sensor(201, "PM2.5")],
    }
    data = {
        101: [{"Wartość": None, "Data": "2026-09-04 09:00:00"}],
        102: [{"Wartość": 18.0, "Data": "2026-09-04 09:00:00"}],
        201: [{"Wartość": 25.0, "Data": "2026-09-04 09:00:00"}],
    }
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["station_name"] == "Bliska"
    assert result["pollutant"] == "PM10"
    assert result["value"] == 18.0


@pytest.mark.asyncio
async def test_get_air_quality_scans_past_null_values(cache_db):
    stations = [_aq_station(1, "Stacja", 19.0, 50.0)]
    sensors = {1: [_aq_sensor(301, "PM2.5")]}
    data = {301: [
        {"Wartość": None, "Data": "2026-09-04 10:00:00"},
        {"Wartość": None, "Data": "2026-09-04 09:00:00"},
        {"Wartość": 8.1, "Data": "2026-09-04 08:00:00"},
    ]}
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["value"] == 8.1
    assert result["measured_at"] == "2026-09-04 08:00:00"


@pytest.mark.asyncio
async def test_get_air_quality_skips_manual_station_with_no_sensors(cache_db):
    stations = [_aq_station(1, "Manualna", 19.0, 50.0), _aq_station(2, "Automatyczna", 19.1, 50.1)]
    sensors = {1: [], 2: [_aq_sensor(401, "PM2.5")]}
    data = {401: [{"Wartość": 15.0, "Data": "2026-09-04 09:00:00"}]}
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["station_name"] == "Automatyczna"


@pytest.mark.asyncio
async def test_get_air_quality_skips_station_with_only_null_readings(cache_db):
    stations = [_aq_station(1, "BezDanych", 19.0, 50.0), _aq_station(2, "ZDanymi", 19.1, 50.1)]
    sensors = {1: [_aq_sensor(501, "PM2.5")], 2: [_aq_sensor(502, "PM2.5")]}
    data = {
        501: [{"Wartość": None, "Data": "2026-09-04 09:00:00"}],
        502: [{"Wartość": 20.0, "Data": "2026-09-04 09:00:00"}],
    }
    client = _FakeAirQualityClient(stations, sensors, data)

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "ok"
    assert result["station_name"] == "ZDanymi"


@pytest.mark.asyncio
async def test_get_air_quality_all_candidates_exhausted_is_error(cache_db):
    stations = [_aq_station(i, f"Stacja{i}", 19.0 + i * 0.01, 50.0) for i in range(1, 4)]
    client = _FakeAirQualityClient(stations, {}, {})

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_air_quality_no_stations_in_database_is_error(cache_db):
    client = _FakeAirQualityClient([], {}, {})

    result = await air_quality.get_air_quality(client, 19.0, 50.0)

    assert result["status"] == "error"
    assert "stacji" in result["message"]


@pytest.mark.asyncio
async def test_get_air_quality_station_list_fetch_failure_is_error(cache_db):
    result = await air_quality.get_air_quality(_FailingGetClient(), 19.0, 50.0)

    assert result["status"] == "error"
    assert "GIOŚ" in result["message"]


# ---------------------------------------------------------------------------
# http_utils.describe_exc — several httpx exceptions (ConnectTimeout,
# ReadTimeout, ...) have an empty str() when raised without an explicit
# message, which silently produced "usługa niedostępna: " with nothing
# after the colon in every service's error message — reported live by
# Klaudia 2026-09-04 (a genuine Overpass timeout for the nearest-road
# check). Fixed by falling back to the exception's class name.
# ---------------------------------------------------------------------------

def test_describe_exc_uses_str_when_present():
    assert http_utils.describe_exc(RuntimeError("połączenie zerwane")) == "połączenie zerwane"


def test_describe_exc_falls_back_to_class_name_when_str_is_empty():
    assert http_utils.describe_exc(httpx.ConnectTimeout("")) == "ConnectTimeout"


def test_describe_exc_falls_back_for_bare_exception_with_no_args():
    assert http_utils.describe_exc(httpx.ReadTimeout("")) == "ReadTimeout"


# ---------------------------------------------------------------------------
# http_utils._get_with_retry — retries transient failures for the ~380
# independently-operated powiat WFS servers. Fixed 2026-09-04: used to
# retry only httpx.TimeoutException/TransportError, never a 5xx HTTP
# status, so an overloaded server returning 503 failed on the first try
# instead of getting the one retry this helper exists to provide.
# ---------------------------------------------------------------------------

class _QueueClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, url, params=None, timeout=None, follow_redirects=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_response(status_code):
    request = httpx.Request("GET", "http://example.com")
    return httpx.Response(status_code, request=request)


@pytest.mark.asyncio
async def test_get_with_retry_retries_on_5xx_then_succeeds():
    client = _QueueClient([_http_response(503), _http_response(200)])

    resp = await http_utils._get_with_retry(client, "http://x", params={}, timeout=1.0, retry_delay_s=0)

    assert resp.status_code == 200
    assert client.calls == 2


@pytest.mark.asyncio
async def test_get_with_retry_does_not_retry_on_4xx():
    client = _QueueClient([_http_response(404)])

    with pytest.raises(httpx.HTTPStatusError):
        await http_utils._get_with_retry(client, "http://x", params={}, timeout=1.0, retry_delay_s=0)

    assert client.calls == 1


@pytest.mark.asyncio
async def test_get_with_retry_retries_on_timeout_then_succeeds():
    client = _QueueClient([httpx.TimeoutException("boom"), _http_response(200)])

    resp = await http_utils._get_with_retry(client, "http://x", params={}, timeout=1.0, retry_delay_s=0)

    assert resp.status_code == 200
    assert client.calls == 2


@pytest.mark.asyncio
async def test_get_with_retry_exhausts_retries_and_raises():
    client = _QueueClient([_http_response(503), _http_response(503)])

    with pytest.raises(httpx.HTTPStatusError):
        await http_utils._get_with_retry(
            client, "http://x", params={}, timeout=1.0, max_retries=1, retry_delay_s=0,
        )

    assert client.calls == 2


@pytest.mark.asyncio
async def test_get_with_retry_logs_elapsed_time_of_failed_attempt(caplog):
    # Diagnostic step 1 from HANDOFF.md's MPZP ConnectTimeout investigation
    # (added 2026-09-04) — a failed attempt's log line must include how long
    # it actually took, so a future live report can distinguish "genuinely
    # burned the whole timeout" from "failed almost instantly" (two
    # different root causes, needing two different fixes).
    client = _QueueClient([httpx.ConnectTimeout("boom"), _http_response(200)])

    with caplog.at_level("WARNING"):
        resp = await http_utils._get_with_retry(client, "http://x", params={}, timeout=1.0, retry_delay_s=0)

    assert resp.status_code == 200
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "ConnectTimeout" in warnings[0]
    assert re.search(r"po \d+\.\d+s", warnings[0]), warnings[0]


# ---------------------------------------------------------------------------
# config.TIMEOUT_OVERPASS vs. the in-query "[timeout:N]" directives in
# services/nearby_features.py. Fixed 2026-09-04, reported live by Klaudia
# as an intermittent "usługa OpenStreetMap/Overpass niedostępna" for the
# nearest-road check: TIMEOUT_OVERPASS was 14s while every query told the
# server it had up to 25s — our own client gave up before the server's own
# granted budget closed, turning "a bit busy" into a false "down", roughly
# whenever Overpass took 14-25s to answer. Regression guard: parses the
# actual query strings (not a hardcoded number) so it stays correct if the
# in-query timeout is ever deliberately changed alongside TIMEOUT_OVERPASS.
# ---------------------------------------------------------------------------

def test_timeout_overpass_exceeds_every_in_query_overpass_timeout():
    source = pathlib.Path(__file__).resolve().parent.parent / "services" / "nearby_features.py"
    text = source.read_text()
    in_query_timeouts = [int(m) for m in re.findall(r"\[timeout:(\d+)\]", text)]
    assert in_query_timeouts, "brak dyrektyw [timeout:N] — czy format zapytania się zmienił?"
    assert TIMEOUT_OVERPASS > max(in_query_timeouts), (
        f"TIMEOUT_OVERPASS ({TIMEOUT_OVERPASS}s) musi przewyższać każdą dyrektywę "
        f"[timeout:N] w zapytaniach Overpass (max znaleziony: {max(in_query_timeouts)}s), "
        "inaczej klient zrezygnuje, zanim serwer sam by skończył."
    )


# ---------------------------------------------------------------------------
# http_utils._overpass_query — races every configured mirror concurrently
# instead of trying them one after another. Fixed 2026-09-04: the sequential
# version meant a slow, rate-limited, or silently-blocked mirror (a real
# risk for shared hosting IPs like Render's against free public Overpass
# instances) had to fully exhaust its own timeout before the next mirror was
# even attempted — reported live by Klaudia as the nearest-road check still
# failing even after the [timeout:25] vs. client-timeout mismatch was fixed.
# ---------------------------------------------------------------------------

class _FakeJsonPostResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeOverpassClient:
    """responses: dict[url] -> {"json": ...} or {"exc": Exception(...)},
    each optionally with "delay" (seconds) to control which mirror
    "answers" first."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(url)
        spec = self._responses[url]
        await asyncio.sleep(spec.get("delay", 0))
        if "exc" in spec:
            raise spec["exc"]
        return _FakeJsonPostResponse(spec["json"])


@pytest.mark.asyncio
async def test_overpass_query_returns_first_successful_mirror(monkeypatch):
    monkeypatch.setattr(http_utils, "OVERPASS_URLS", ["http://a", "http://b"])
    client = _FakeOverpassClient({
        "http://a": {"exc": RuntimeError("zablokowany")},
        "http://b": {"json": {"elements": ["real result"]}},
    })

    result = await http_utils._overpass_query_once(client, "fake query")

    assert result == {"elements": ["real result"]}


@pytest.mark.asyncio
async def test_overpass_query_all_mirrors_failing_raises():
    client = _FakeOverpassClient({
        u: {"exc": RuntimeError(f"{u} niedostępny")} for u in http_utils.OVERPASS_URLS
    })

    with pytest.raises(RuntimeError):
        await http_utils._overpass_query_once(client, "fake query")


@pytest.mark.asyncio
async def test_overpass_query_does_not_wait_for_a_slower_blocked_mirror(monkeypatch):
    """The whole point of racing instead of retrying sequentially: a mirror
    that never answers must not delay a healthy one that answers quickly."""
    monkeypatch.setattr(http_utils, "OVERPASS_URLS", ["http://slow", "http://fast"])
    client = _FakeOverpassClient({
        "http://slow": {"json": {"elements": ["late"]}, "delay": 0.3},
        "http://fast": {"json": {"elements": ["quick"]}, "delay": 0.02},
    })

    start = time.monotonic()
    result = await http_utils._overpass_query(client, "fake query")
    elapsed = time.monotonic() - start

    assert result == {"elements": ["quick"]}
    assert elapsed < 0.2  # well under the slow mirror's 0.3s delay


# ---------------------------------------------------------------------------
# http_utils._overpass_query — retry wrapper around _overpass_query_once,
# added 2026-09-05 after Klaudia reported live (on the "Zawoja" test parcel)
# that the nearest-road check failed once, then succeeded on a manual
# re-run moments later — racing mirrors only helps when at least one is
# healthy on a given attempt, not when BOTH happen to fail at once.
# ---------------------------------------------------------------------------

class _CountingFailThenSucceedClient:
    """Fails every mirror on the first N passes, then succeeds — models
    "both mirrors briefly down/rate-limited, then recover"."""

    def __init__(self, fail_passes: int):
        self.fail_passes = fail_passes
        self.pass_count = 0

    async def post(self, url, data=None, headers=None, timeout=None):
        # Both mirrors are hit within the same pass; count passes by URL
        # cycling back to the first one.
        if url == http_utils.OVERPASS_URLS[0]:
            self.pass_count += 1
        if self.pass_count <= self.fail_passes:
            raise RuntimeError(f"{url} niedostępny")
        return _FakeJsonPostResponse({"elements": ["recovered"]})


@pytest.mark.asyncio
async def test_overpass_query_retries_when_both_mirrors_fail_then_succeeds():
    client = _CountingFailThenSucceedClient(fail_passes=1)

    result = await http_utils._overpass_query(client, "fake query", retry_delay_s=0)

    assert result == {"elements": ["recovered"]}
    assert client.pass_count == 2  # first pass failed, second (retry) succeeded


@pytest.mark.asyncio
async def test_overpass_query_exhausts_retries_and_raises():
    client = _CountingFailThenSucceedClient(fail_passes=99)

    with pytest.raises(RuntimeError):
        await http_utils._overpass_query(client, "fake query", max_retries=1, retry_delay_s=0)

    assert client.pass_count == 2  # initial attempt + 1 retry, then give up


# ---------------------------------------------------------------------------
# main.resolve_parcel — the multi-stage /api/resolve cascade had no overall
# time budget. Fixed 2026-09-04, reported live by Klaudia as a confusing,
# untranslated browser error ("Wystąpił nieoczekiwany błąd sieci lub
# przeglądarki") for query "Korbielów 3917/5" — a query slow enough to
# plausibly exceed Render's own platform proxy timeout, which returns HTML
# instead of JSON and breaks the frontend's resp.json() parse. See
# TIMEOUT_RESOLVE_BUDGET in config.py.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_parcel_times_out_cleanly_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(main, "TIMEOUT_RESOLVE_BUDGET", 0.05)

    async def _hangs(client, query):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(main, "uldk_search_candidates", _hangs)

    with pytest.raises(HTTPException) as exc_info:
        await main.resolve_parcel(query="Korbielów 3917/5")

    assert exc_info.value.status_code == 504
    assert "Korbielów 3917/5" in exc_info.value.detail


# ---------------------------------------------------------------------------
# main.analyze / main.analyze_stream — performance optimizations added
# 2026-09-04 (see HANDOFF.md, "Propozycje optymalizacji wydajności"):
# (a) one persistent httpx.AsyncClient instead of one per request, (b) zoning
# plugged into the cache, (c) a new SSE /api/analyze-stream endpoint sharing
# _section_specs()/_compute_derived() with the original /api/analyze rather
# than duplicating that wiring. This end-to-end test (via Starlette's
# TestClient, so FastAPI's own routing/lifespan run for real) guards the
# refactor: both endpoints must expose the exact same 12 section results and
# the same verdict/due_diligence/valuation, assembled from a genuinely
# streamed SSE response for the second one.
# ---------------------------------------------------------------------------

def _fake_parcel_for_stream_test():
    return {
        "teryt_id": "146501_1.0001.1/2",
        "voivodeship_code": "14",
        "voivodeship_name": "mazowieckie",
        "county": "warszawski",
        "commune": "Warszawa",
        "parcel_no": "1/2",
        "geometry": Polygon([(21.0, 52.0), (21.001, 52.0), (21.001, 52.001), (21.0, 52.001), (21.0, 52.0)]),
        "multiple_found": False,
        "found_count": 1,
    }


def _patch_all_sections_ok(monkeypatch):
    async def ok(*_a, **_k):
        return {"status": "ok"}

    async def ok_buildings(*_a, **_k):
        return {"status": "ok", "buildings": [], "source": "OSM"}

    async def ok_air_quality(*_a, **_k):
        return {
            "status": "ok", "station_name": "Test", "distance_m": 500, "pollutant": "PM2.5",
            "value": 10, "unit": "µg/m3", "measured_at": "2026-09-04 10:00", "attribution": "GIOŚ",
        }

    async def fake_uldk_get_parcel(_client, _parcel_id):
        return _fake_parcel_for_stream_test()

    monkeypatch.setattr(main, "uldk_get_parcel", fake_uldk_get_parcel)
    monkeypatch.setattr(main, "check_landslide", ok)
    monkeypatch.setattr(main, "check_utilities", ok)
    monkeypatch.setattr(main, "get_cadastre_basic", ok)
    monkeypatch.setattr(main, "get_zoning", ok)
    monkeypatch.setattr(main, "get_buildings_on_parcel", ok_buildings)
    monkeypatch.setattr(main, "get_waterways", ok)
    monkeypatch.setattr(main, "get_flood_zone", ok)
    monkeypatch.setattr(main, "get_waterlogging_risk", ok)
    monkeypatch.setattr(main, "get_nearest_municipal_road", ok)
    monkeypatch.setattr(main, "get_protected_areas", ok)
    monkeypatch.setattr(main, "check_mining_areas", ok)
    monkeypatch.setattr(main, "get_air_quality", ok_air_quality)


_SECTION_KEYS = {
    "landslide", "utilities", "cadastre", "zoning", "buildings", "waterways",
    "flood_zone", "waterlogging", "nearest_road", "protected_areas", "mining_areas", "air_quality",
}


def _parse_sse(raw_text):
    """Minimal SSE frame parser for tests — splits on the blank-line frame
    separator and pulls out the 'event:'/'data:' lines, mirroring what
    static/app.js's own parser does for the real stream."""
    events = []
    for frame in raw_text.split("\n\n"):
        if not frame.strip():
            continue
        event_name, data_line = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_line = line[len("data: "):]
        events.append((event_name, json.loads(data_line) if data_line is not None else None))
    return events


def test_analyze_and_analyze_stream_agree_on_sections_and_derived_fields(monkeypatch, cache_db):
    _patch_all_sections_ok(monkeypatch)

    with TestClient(main.app) as client:
        plain = client.get("/api/analyze?parcel_id=146501_1.0001.1/2")
        assert plain.status_code == 200
        plain_data = plain.json()

        with client.stream("GET", "/api/analyze-stream?parcel_id=146501_1.0001.1/2") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = _parse_sse("".join(resp.iter_text()))

    assert events[0][0] == "meta"
    assert events[-1][0] == "done"
    section_events = [payload for name, payload in events if name == "section"]
    assert {e["key"] for e in section_events} == _SECTION_KEYS
    assert len(section_events) == len(_SECTION_KEYS)  # każda sekcja dokładnie raz

    def _without_cache_metadata(section):
        # The second request (the streamed one) hits the cache the first
        # request just populated, so 'cached'/'fetched_at' legitimately
        # differ between the two calls — strip them before comparing.
        return {k: v for k, v in section.items() if k not in ("cached", "fetched_at")}

    streamed = {e["key"]: e["value"] for e in section_events}
    for key in _SECTION_KEYS:
        expected = (
            plain_data["hydrology"][key] if key in ("waterways", "flood_zone", "waterlogging")
            else plain_data[key]
        )
        assert _without_cache_metadata(streamed[key]) == _without_cache_metadata(expected)

    done_payload = events[-1][1]
    assert done_payload["verdict"] == plain_data["verdict"]
    assert done_payload["due_diligence"] == plain_data["due_diligence"]
    assert done_payload["valuation"] == plain_data["valuation"]

    meta_payload = events[0][1]
    assert meta_payload["parcel"] == plain_data["parcel"]
    assert meta_payload["area_m2"] == plain_data["area_m2"]
    assert meta_payload["permits"] == plain_data["permits"]
    assert meta_payload["land_registry"] == plain_data["land_registry"]


# ---------------------------------------------------------------------------
# main._section_specs — MAX_CONCURRENT_SECTIONS semaphore, added 2026-09-04.
# Confirmed live (see HANDOFF.md, "Prawdziwa przyczyna: przeciążenie
# zasobów na Render"): firing all 12 branches at once for a data-heavy
# parcel overwhelmed Render's free-tier single thread badly enough that
# UNRELATED external services (GUGiK's MPZP host AND OpenStreetMap
# Overpass, in the same analysis run) each timed out near their own
# configured limit. A first fix attempt (forcing IPv4 for the MPZP host)
# was reverted after live verification showed it didn't help — the real
# fix is capping how many branches actually run their fetch at once.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_section_specs_caps_concurrent_fetches(monkeypatch, cache_db):
    current = 0
    max_seen = 0
    guard = asyncio.Lock()

    async def fake_service(*_a, **_k):
        nonlocal current, max_seen
        async with guard:
            current += 1
            max_seen = max(max_seen, current)
        await asyncio.sleep(0.02)
        async with guard:
            current -= 1
        return {"status": "ok"}

    for name in (
        "check_landslide", "check_utilities", "get_cadastre_basic", "get_zoning",
        "get_buildings_on_parcel", "get_waterways", "get_flood_zone", "get_waterlogging_risk",
        "get_nearest_municipal_road", "get_protected_areas", "check_mining_areas", "get_air_quality",
    ):
        monkeypatch.setattr(main, name, fake_service)
    monkeypatch.setattr(main, "MAX_CONCURRENT_SECTIONS", 3)

    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    specs = main._section_specs(
        client=None, teryt_id="TEST_CONCURRENCY/1", geometry=geometry,
        cx2180=0.0, cy2180=0.0, centroid=geometry.centroid,
    )
    assert len(specs) == len(_SECTION_KEYS)

    await asyncio.gather(*[coro for _name, coro in specs])

    assert max_seen == 3, f"powinno dojść dokładnie do limitu (3), a nie {max_seen}"


@pytest.mark.asyncio
async def test_section_specs_without_cap_would_run_all_12_at_once(monkeypatch, cache_db):
    # Sanity check for the test above: WITHOUT the semaphore (limit >= 12),
    # all 12 branches genuinely do run concurrently — confirms
    # test_section_specs_caps_concurrent_fetches is actually exercising the
    # cap, not just measuring something that was already low.
    current = 0
    max_seen = 0
    guard = asyncio.Lock()

    async def fake_service(*_a, **_k):
        nonlocal current, max_seen
        async with guard:
            current += 1
            max_seen = max(max_seen, current)
        await asyncio.sleep(0.02)
        async with guard:
            current -= 1
        return {"status": "ok"}

    for name in (
        "check_landslide", "check_utilities", "get_cadastre_basic", "get_zoning",
        "get_buildings_on_parcel", "get_waterways", "get_flood_zone", "get_waterlogging_risk",
        "get_nearest_municipal_road", "get_protected_areas", "check_mining_areas", "get_air_quality",
    ):
        monkeypatch.setattr(main, name, fake_service)
    monkeypatch.setattr(main, "MAX_CONCURRENT_SECTIONS", 12)

    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    specs = main._section_specs(
        client=None, teryt_id="TEST_CONCURRENCY/2", geometry=geometry,
        cx2180=0.0, cy2180=0.0, centroid=geometry.centroid,
    )
    await asyncio.gather(*[coro for _name, coro in specs])

    assert max_seen == len(_SECTION_KEYS)
