"""Geometry parsing/measurement helpers and small text-cleanup utilities
shared by several services. No network calls in this module."""
import re

from bs4 import BeautifulSoup
from pyproj import Geod, Transformer
from shapely import wkb, wkt
from shapely.geometry.base import BaseGeometry

geod = Geod(ellps="WGS84")
to_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

_EWKT_SRID_PREFIX = re.compile(r"^SRID=\d+;\s*", re.IGNORECASE)


def _parse_uldk_geometry(raw: str) -> BaseGeometry:
    raw = raw.strip()
    stripped = _EWKT_SRID_PREFIX.sub("", raw)
    try:
        return wkt.loads(stripped)
    except Exception:
        pass
    try:
        return wkb.loads(bytes.fromhex(raw))
    except Exception as exc:
        raise ValueError(f"Nie udało się sparsować geometrii ULDK: {exc}")


def _clean_feature_info_text(raw_html: str) -> str:
    if not raw_html or not raw_html.strip():
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" | ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\|\s*){2,}", "| ", text).strip(" |")
    return text


def _parse_feature_info_table(raw_html: str) -> list[dict[str, str]]:
    """GUGiK's MapServer GetFeatureInfo templates (confirmed live for both
    KIEG and KIMPZP) render one <tr><td>label</td><td>value</td></tr> per
    field — i.e. each row IS a label:value pair, not a header row followed by
    data rows. Parse accordingly into [{"label": ..., "value": ...}, ...],
    with a blank "label" cell (feature separator rows some services emit)
    used to start a new group when multiple features are returned."""
    if not raw_html or not raw_html.strip():
        return []
    soup = BeautifulSoup(raw_html, "html.parser")
    rows_out: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0]:
                rows_out.append({"label": cells[0], "value": " ".join(cells[1:])})
    return rows_out


def _rectangle_side_lengths(geometry_2180) -> tuple[float, float]:
    """Minimum-rotated-rectangle side lengths (meters) for an irregular
    parcel polygon — the standard way to give a meaningful 'width x length'
    for a shape that usually isn't a true rectangle. Returns (short, long)."""
    mrr = geometry_2180.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)[:4]
    d1 = ((coords[0][0] - coords[1][0]) ** 2 + (coords[0][1] - coords[1][1]) ** 2) ** 0.5
    d2 = ((coords[1][0] - coords[2][0]) ** 2 + (coords[1][1] - coords[2][1]) ** 2) ** 0.5
    return (min(d1, d2), max(d1, d2))


def _polygon_outline_normalized(geometry_2180, target_size: float = 64.0) -> list[list[float]]:
    """Simplified outline points for a small shape-thumbnail (frontend draws
    these as an inline SVG polygon next to search results). Normalized so
    the longer side maps to target_size and the shorter side scales
    proportionally — real aspect ratio is preserved, absolute size isn't
    (a tiny icon can't show that anyway). Y is flipped (north-up) since
    projected northing grows upward but SVG y grows downward.

    Simplification (shapely .simplify, meters tolerance) keeps the response
    small for parcels with many vertices — a thumbnail doesn't need every
    cadastral survey point, and most residential/agricultural parcels are
    near-rectangular anyway."""
    poly = geometry_2180
    if poly.geom_type != "Polygon":
        poly = poly.convex_hull
    simplified = poly.simplify(0.5, preserve_topology=True)
    coords = list(simplified.exterior.coords)
    if len(coords) > 40:
        simplified = poly.simplify(2.0, preserve_topology=True)
        coords = list(simplified.exterior.coords)

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y) or 1.0
    scale = target_size / span
    return [[round((x - min_x) * scale, 1), round((max_y - y) * scale, 1)] for x, y in coords]


_POLAND_BOUNDS = (13.5, 48.8, 24.7, 55.0)  # lon_min, lat_min, lon_max, lat_max


def _within_poland(lon: float, lat: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = _POLAND_BOUNDS
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _feature_info_has_data(text: str) -> bool:
    if not text or len(text) < 25:
        return False
    return not re.search(
        r"no features|brak (danych|obiekt|wyniku)|nie udostępnia danych|search returned no results",
        text, re.IGNORECASE,
    )

