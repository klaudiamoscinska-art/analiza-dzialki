"""Section 8 (per the Działkopedia competitor analysis — see HANDOFF.md) —
one synthesized verdict at the top of the analysis, combining the risk
signals this app already collects separately into a single score and
plain-language summary. Added 2026-09-04.

Deliberately a small, transparent point-based rule set, NOT a model —
every point deducted is named in 'flags', so the number is always
traceable to a specific reason rather than a black box. This mirrors how
the rest of the app already treats its statistical valuation: clearly a
heuristic, not professional advice, and says so."""
from typing import Any

DISCLAIMER = (
    "To automatyczne podsumowanie sygnałów zebranych powyżej, nie opinia prawna ani "
    "rzeczoznawcza — każda pozycja odsyła do właściwej sekcji, gdzie widać szczegóły."
)


def build_verdict(
    landslide: dict[str, Any], zoning: dict[str, Any], flood_zone: dict[str, Any],
    waterlogging: dict[str, Any], utilities: dict[str, Any], nearest_road: dict[str, Any],
    protected_areas: dict[str, Any],
) -> dict[str, Any]:
    """Combines existing risk signals into one score (0-100, clamped) and a
    three-level verdict. Only WEIGHTS are risk-based; a section that
    simply failed to fetch (status != 'ok') is never scored as a red flag
    — it's surfaced separately as 'incomplete data' so the verdict never
    silently treats 'we don't know' as 'it's fine'."""
    score = 100
    flags: list[dict[str, str]] = []
    incomplete: list[str] = []

    def flag(points: int, severity: str, text: str) -> None:
        nonlocal score
        score -= points
        flags.append({"severity": severity, "text": text})

    if landslide.get("status") == "ok":
        if landslide.get("has_landslide"):
            flag(40, "critical", "Teren osuwiskowy lub zagrożony osuwiskiem (SOPO PIG-PIB)")
    else:
        incomplete.append("zagrożenie osuwiskowe")

    if flood_zone.get("status") == "ok":
        if flood_zone.get("in_flood_zone"):
            flag(35, "critical", "Działka w strefie zalewowej (ISOK)")
    else:
        incomplete.append("strefa zalewowa")

    if waterlogging.get("status") == "ok":
        if waterlogging.get("at_risk"):
            flag(15, "warning", "Teren podatny na podtopienia (PIG-PIB)")
    else:
        incomplete.append("ryzyko podtopień")

    if protected_areas.get("status") == "ok":
        if protected_areas.get("areas"):
            names = ", ".join(a["name"] for a in protected_areas["areas"][:3])
            flag(10, "warning", f"Działka w obszarze chronionym: {names} — możliwe dodatkowe ograniczenia")
    else:
        incomplete.append("obszary chronione przyrody")

    if zoning.get("status") == "ok":
        if zoning.get("found") == "no":
            flag(10, "warning", "Brak planu miejscowego — sprawdź plan ogólny/OUZ (patrz sekcja Plany zagospodarowania)")
    elif zoning.get("status") != "partial":
        incomplete.append("plan zagospodarowania")

    if nearest_road.get("status") == "ok":
        if nearest_road.get("found") == "no":
            flag(20, "warning", "Nie wykryto drogi publicznej w pobliżu (dane OpenStreetMap)")
        elif nearest_road.get("is_fallback_powiatowa"):
            flag(5, "info", "Brak drogi gminnej w pobliżu — najbliższa jest wyższej kategorii")
    else:
        incomplete.append("odległość do drogi")

    if utilities.get("status") == "ok":
        present_count = sum(1 for u in utilities.get("utilities", []) if u.get("present"))
        if present_count == 0:
            flag(15, "warning", "Nie wykryto żadnych mediów w pobliżu działki (GESUT)")
        elif present_count <= 2:
            flag(5, "info", "Wykryto niewiele typów mediów w pobliżu działki")
    else:
        incomplete.append("media / uzbrojenie terenu")

    score = max(0, min(100, score))
    if score >= 80:
        level = "dobra"
    elif score >= 50:
        level = "do_sprawdzenia"
    else:
        level = "wysokie_ryzyko"

    return {
        "score": score,
        "level": level,
        "flags": flags,
        "incomplete_sections": incomplete,
        "disclaimer": DISCLAIMER,
    }
