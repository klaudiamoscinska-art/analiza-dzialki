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
from services import valuation, wfs_search


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
async def test_search_propagates_gather_error(monkeypatch):
    async def fake_gather_error(client, place_query):
        return {"status": "error", "message": "Nie znaleziono miejscowości."}
    monkeypatch.setattr(wfs_search, "_gather_nearby_parcels", fake_gather_error)
    result = await wfs_search.search_parcels_universal(None, "Nieistniejąca Wieś", target_area_m2=1000)
    assert result["status"] == "error"
