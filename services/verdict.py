"""Section 8 (per the Działkopedia competitor analysis — see HANDOFF.md) —
one synthesized verdict at the top of the analysis, combining the risk
signals this app already collects separately into a single score AND a
full checklist of status rows (one per check, including the clean ones —
not just the flagged ones). Added 2026-09-04, extended 2026-09-04 after
Klaudia shared Działkopedia's actual free-tier report: their whole result
is a compact list of rows (label · RYZYKO/UWAGA/OK pill · one-line text)
with a 3-way count at the top (do sprawdzenia / bez zastrzeżeń / ryzyko)
— this reproduces that structure from the same signals this app already
had, no new data required.

Deliberately a small, transparent point-based rule set, NOT a model —
every point deducted is traceable to one named row, never a black box.
Each signal is interpreted exactly ONCE (in _evaluate calls below) and
that single interpretation drives both the row shown and the score
deducted — there is no separate, second place that decides what counts
as risky, so the two can't drift apart."""
from typing import Any, Optional

DISCLAIMER = (
    "To automatyczne podsumowanie sygnałów zebranych powyżej, nie opinia prawna ani "
    "rzeczoznawcza — każda pozycja odsyła do właściwej sekcji, gdzie widać szczegóły."
)

# Score weight per non-'ok' tier — 'risk' rows are the two hazards serious
# enough to plausibly rule a parcel out outright; everything else is 'warning'.
_TIER_POINTS = {"risk": 35, "warning": 10}


def build_verdict(
    landslide: dict[str, Any], zoning: dict[str, Any], flood_zone: dict[str, Any],
    waterlogging: dict[str, Any], utilities: dict[str, Any], nearest_road: dict[str, Any],
    protected_areas: dict[str, Any], mining_areas: dict[str, Any], air_quality: dict[str, Any],
) -> dict[str, Any]:
    """Returns score (0-100), level, the full row list (ok included), a
    3-way tier count, which sections had no usable data ('incomplete'),
    and the disclaimer. A section that simply failed to fetch never
    produces a row or a score deduction — it's surfaced separately in
    'incomplete_sections' so 'we don't know' is never silently shown or
    scored as 'it's fine'."""
    score = 100
    rows: list[dict[str, str]] = []
    incomplete: list[str] = []

    def add_row(key: str, label: str, tier: str, text: str, points: Optional[int] = None) -> None:
        nonlocal score
        rows.append({"key": key, "label": label, "tier": tier, "text": text})
        if tier != "ok":
            score -= points if points is not None else _TIER_POINTS[tier]

    if landslide.get("status") == "ok":
        has = landslide.get("has_landslide")
        add_row("landslide", "Osuwiska", "risk" if has else "ok",
                "Wykryto osuwisko lub teren zagrożony osuwiskiem (SOPO PIG-PIB)." if has
                else "Teren stabilny geologicznie — brak osuwisk w rejestrze SOPO.", points=40 if has else None)
    else:
        incomplete.append("zagrożenie osuwiskowe")

    if flood_zone.get("status") == "ok":
        in_zone = flood_zone.get("in_flood_zone")
        depth = flood_zone.get("depth_range")
        add_row("flood_zone", "Powódź", "risk" if in_zone else "ok",
                f"Działka w strefie zalewowej (ISOK){f', głębokość: {depth}' if depth else ''}." if in_zone
                else "Brak strefy zalewowej ISOK w tym miejscu.")
    else:
        incomplete.append("strefa zalewowa")

    if waterlogging.get("status") == "ok":
        at_risk = waterlogging.get("at_risk")
        add_row("waterlogging", "Podtopienia", "warning" if at_risk else "ok",
                "Teren podatny na podtopienia (wody gruntowe, PIG-PIB)." if at_risk
                else "Brak wykrytego ryzyka podtopień.", points=15 if at_risk else None)
    else:
        incomplete.append("ryzyko podtopień")

    if protected_areas.get("status") == "ok":
        areas = protected_areas.get("areas") or []
        names = ", ".join(a["name"] for a in areas[:3])
        add_row("protected_areas", "Przyroda", "warning" if areas else "ok",
                f"W pobliżu działki obszary chronione: {names}." if areas
                else "Brak obszarów chronionych GDOŚ w tym miejscu.")
    else:
        incomplete.append("obszary chronione przyrody")

    if mining_areas.get("status") == "ok":
        has_mining = mining_areas.get("has_mining_area")
        add_row("mining_areas", "Tereny górnicze", "warning" if has_mining else "ok",
                "Działka w terenie/obszarze górniczym (MIDAS PIG-PIB)." if has_mining
                else "Brak wykrytych terenów/obszarów górniczych.")
    else:
        incomplete.append("tereny górnicze")

    if zoning.get("status") == "ok":
        no_plan = zoning.get("found") == "no"
        add_row("zoning", "Plan zagospodarowania", "warning" if no_plan else "ok",
                "Brak planu miejscowego — sprawdź plan ogólny/OUZ (patrz sekcja Plany zagospodarowania)." if no_plan
                else "Działka objęta planem miejscowym.")
    elif zoning.get("status") == "partial":
        add_row("zoning", "Plan zagospodarowania", "warning",
                "Działka jest objęta planem, ale szczegóły nie zostały pobrane — sprawdź sekcję niżej.", points=0)
    else:
        incomplete.append("plan zagospodarowania")

    if nearest_road.get("status") == "ok":
        if nearest_road.get("found") == "no":
            add_row("nearest_road", "Dojazd", "warning",
                    "Nie wykryto drogi publicznej w pobliżu (dane OpenStreetMap).", points=20)
        else:
            dist = nearest_road.get("distance_m") or 0
            dist_txt = f"{dist / 1000:.2f} km" if dist >= 1000 else f"{dist} m"
            fallback = nearest_road.get("is_fallback_powiatowa")
            add_row("nearest_road", "Dojazd", "warning" if fallback else "ok",
                    f"Droga publiczna w odległości {dist_txt}"
                    + (" (najbliższa jest wyższej kategorii, brak drogi gminnej w pobliżu)." if fallback else "."),
                    points=5 if fallback else None)
    else:
        incomplete.append("odległość do drogi")

    if utilities.get("status") == "ok":
        util_list = utilities.get("utilities", [])
        present = [u for u in util_list if u.get("present")]
        add_row("utilities", "Media", "warning" if not present else "ok",
                f"Wykryto {len(present)} z {len(util_list)} typów mediów w pobliżu działki." if present
                else "Nie wykryto żadnych mediów w pobliżu działki (GESUT).", points=15 if not present else None)
    else:
        incomplete.append("media / uzbrojenie terenu")

    if air_quality.get("status") == "ok":
        dist_km = air_quality["distance_m"] / 1000
        add_row("air_quality", "Powietrze", "ok",
                f"Stacja GIOŚ {air_quality['station_name']} ({dist_km:.1f} km): "
                f"{air_quality['pollutant']} {air_quality['value']} {air_quality['unit']} — ostatni dostępny pomiar.")
        # Informacyjne, nie punktowane — nie mamy (jeszcze) logiki progów
        # zdrowotnych WHO/UE, więc appka nie udaje oceny ryzyka, tylko
        # pokazuje surowy odczyt, tak jak konkurencja.
    else:
        incomplete.append("jakość powietrza")

    score = max(0, min(100, score))
    if score >= 80:
        level = "dobra"
    elif score >= 50:
        level = "do_sprawdzenia"
    else:
        level = "wysokie_ryzyko"

    counts = {"risk": 0, "warning": 0, "ok": 0}
    for r in rows:
        counts[r["tier"]] += 1

    return {
        "score": score,
        "level": level,
        "rows": rows,
        "counts": counts,
        "incomplete_sections": incomplete,
        "disclaimer": DISCLAIMER,
    }
