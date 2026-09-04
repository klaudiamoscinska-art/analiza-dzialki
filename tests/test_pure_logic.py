"""Unit tests for app logic that does NOT depend on live network calls.

These deliberately stay away from anything that hits ULDK/WFS/Overpass/etc.
directly (this sandbox has no route to those government services — see
HANDOFF.md), and instead cover the parts of the app that are pure
computation or that can be exercised with a monkeypatched network layer:
geometry helpers, the "Szukaj działki" matching/ranking logic, link
builders, and the WFS registry lookup rules.

Run with: pytest (after `pip install -r requirements-dev.txt`).
"""
import pytest
from shapely.geometry import Polygon

import geo_utils
from services import cache, due_diligence, geocoding, geology, nature, uldk, valuation, verdict, wfs_search, zoning


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
    )


def _row(result, key):
    return next(r for r in result["rows"] if r["key"] == key)


def test_build_verdict_all_clean_scores_100_dobra():
    result = verdict.build_verdict(**_clean_signals())
    assert result["score"] == 100
    assert result["level"] == "dobra"
    assert result["incomplete_sections"] == []
    assert result["counts"] == {"risk": 0, "warning": 0, "ok": 8}
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


def test_build_verdict_failed_section_is_incomplete_no_row_no_deduction():
    signals = _clean_signals()
    signals["landslide"] = {"status": "error", "message": "usługa niedostępna"}
    result = verdict.build_verdict(**signals)
    assert result["score"] == 100  # brak danych nie obniża wyniku
    assert "zagrożenie osuwiskowe" in result["incomplete_sections"]
    assert not any(r["key"] == "landslide" for r in result["rows"])


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

    async def get(self, url, params=None, timeout=None):
        return _FakeArcgisResponse(self._data)


class _FailingGetClient:
    async def get(self, url, params=None, timeout=None):
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
async def test_get_protected_areas_request_failure_returns_error():
    result = await nature.get_protected_areas(_FailingGetClient(), 500000.0, 300000.0)
    assert result["status"] == "error"


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
