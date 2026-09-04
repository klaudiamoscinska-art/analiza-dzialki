"""Section 8 (Działkopedia competitor analysis) — the 25-point pre-purchase
checklist, grouped into 7 categories. Added 2026-09-04, after Klaudia
shared Działkopedia's actual free-tier PDF, whose last page is exactly
this: a generic real-estate due-diligence checklist (categories any
Polish land-purchase guide would use — stan prawny, planowanie
przestrzenne, bezpieczeństwo, infrastruktura, koszty, wycena, teren —
this is standard domain knowledge, not their proprietary content) with
items this app already has data for pre-checked.

Purely presentational: no new data source, just turns 'here is data we
collected' into 'here is your literal to-do list before signing',
marking which of the 25 steps this app's own sections already answer."""
from typing import Any

# Each item's 'covers' key, when not None, must match a key that main.py
# puts into the 'covered' set it passes to build_checklist() below — see
# main.py for exactly which service results earn which key.
_CHECKLIST: list[dict[str, Any]] = [
    {
        "category": "Stan prawny",
        "items": [
            {"text": "Sprawdź księgę wieczystą", "covers": None},
            {"text": "Zamów wypis i wyrys z rejestru gruntów (EGiB)", "covers": None},
            {"text": "Sprawdź służebności i roszczenia", "covers": None},
            {"text": "Sprawdź prawo pierwokupu (KOWR / gmina)", "covers": None},
        ],
    },
    {
        "category": "Planowanie przestrzenne",
        "items": [
            {"text": "Sprawdź MPZP (miejscowy plan zagospodarowania)", "covers": "zoning_mpzp"},
            {"text": "Sprawdź warunki zabudowy (WZ)", "covers": None},
            {"text": "Sprawdź plan ogólny gminy", "covers": None},
            {"text": "Sprawdź studium (SUiKZP) lub strefę planistyczną", "covers": None},
        ],
    },
    {
        "category": "Bezpieczeństwo i zagrożenia",
        "items": [
            {"text": "Sprawdź strefę zalewową", "covers": "flood_zone"},
            {"text": "Sprawdź tereny chronione", "covers": "protected_areas"},
            {"text": "Sprawdź zagrożenie osuwiskowe i warunki gruntowe", "covers": "landslide"},
            {"text": "Sprawdź jakość powietrza", "covers": None},
        ],
    },
    {
        "category": "Infrastruktura i media",
        "items": [
            {"text": "Sprawdź dostęp do drogi publicznej", "covers": "road"},
            {"text": "Sprawdź dostęp do prądu", "covers": "power"},
            {"text": "Sprawdź dostęp do wody i kanalizacji", "covers": "water_sewage"},
            {"text": "Sprawdź zasięg internetu", "covers": None},
        ],
    },
    {
        "category": "Koszty zakupu",
        "items": [
            {"text": "Oblicz koszty notarialne i PCC", "covers": None},
            {"text": "Oszacuj koszty uzbrojenia działki", "covers": None},
            {"text": "Sprawdź koszt odrolnienia (jeśli grunt rolny)", "covers": None},
        ],
    },
    {
        "category": "Wycena i ceny",
        "items": [
            {"text": "Sprawdź wartość rynkową działki", "covers": "valuation"},
            {"text": "Porównaj z cenami w okolicy", "covers": None},
            {"text": "Sprawdź trend cenowy w gminie", "covers": None},
        ],
    },
    {
        "category": "Teren i otoczenie",
        "items": [
            {"text": "Odwiedź działkę osobiście", "covers": None},
            {"text": "Oceń nachylenie terenu i nasłonecznienie", "covers": None},
            {"text": "Sprawdź sąsiedztwo i planowane inwestycje", "covers": None},
        ],
    },
]


def build_due_diligence_checklist(covered: set[str]) -> dict[str, Any]:
    """covered: set of keys for sections this analysis actually returned
    usable ('ok') data for — see main.py for how it's built. An item is
    marked auto_checked only when its 'covers' key is in that set; items
    with covers=None (needs a notary, a personal visit, a KOWR check —
    nothing this app can ever answer on its own) are never auto-checked."""
    total = 0
    checked = 0
    categories = []
    for cat in _CHECKLIST:
        cat_items = []
        cat_checked = 0
        for item in cat["items"]:
            total += 1
            is_checked = item["covers"] is not None and item["covers"] in covered
            if is_checked:
                checked += 1
                cat_checked += 1
            cat_items.append({"text": item["text"], "auto_checked": is_checked})
        categories.append({
            "category": cat["category"], "checked": cat_checked, "total": len(cat_items), "items": cat_items,
        })
    return {"total": total, "checked": checked, "categories": categories}
