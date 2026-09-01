# Analiza Działki

Aplikacja do pełnej analizy pojedynczej działki geodezyjnej w Polsce
(ewidencja gruntów, zagrożenia — osuwiska/powódź/podtopienia, media,
plany zagospodarowania, wycena statystyczna) oraz wyszukiwania działek po
miejscowości i rozmiarze. Dane pochodzą wyłącznie z darmowych, oficjalnych
usług rządowych (GUGiK, PIG-PIB, Wody Polskie) i OpenStreetMap — bez kluczy
API.

Backend: Python/FastAPI. Frontend: statyczny HTML/CSS/JS w `static/`.

Pełny, głęboki kontekst budowy tej appki (co działa, co jest strukturalnie
zepsute po stronie usług rządowych, sprawdzone ślepe zaułki, metodologia
pracy) jest w [`HANDOFF.md`](./HANDOFF.md) — warto go przeczytać przed
większymi zmianami.

## Struktura repo

```
main.py                — FastAPI app: tworzenie aplikacji, middleware, trasy HTTP
config.py               — stałe: URL-e usług, warstwy, timeouty, tabele cen
geo_utils.py             — parsowanie/pomiary geometrii, czyszczenie tekstu
http_utils.py            — generyczne helpery HTTP (retry, Overpass, WMS GetFeatureInfo)
services/                — po jednym module na sekcję analizy (ULDK, WFS/"Szukaj
                            działki", ewidencja+budynki, zagrożenia, media,
                            drogi/cieki, plany, wycena)
tests/                   — testy jednostkowe (pytest) dla logiki bez zależności sieciowych
wfs_powiat_registry.json — rejestr bezpośrednich serwerów WFS per powiat (patrz HANDOFF.md §4)
static/                  — frontend: index.html, app.js, manifest.json, service-worker.js, icons/
```

## Uruchomienie lokalne

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # albo requirements.txt, jeśli nie potrzebujesz testów
uvicorn main:app --reload
```

Appka wystartuje na `http://127.0.0.1:8000`.

## Testy

```bash
pip install -r requirements-dev.txt
pytest
```

Testy pokrywają logikę bez zależności sieciowych (parsowanie geometrii,
dopasowanie/ranking w "Szukaj działki", rejestr WFS, buildery linków).
Nie testują samych wywołań do usług rządowych — te wymagają weryfikacji na
żywo (patrz HANDOFF.md, sekcja "Metodologia pracy").

## Deploy

Automatyczny na Render.com (Docker) po pushu do `main`. Przy zmianie
`static/app.js` pamiętaj o podbiciu numeru `?v=N` w `static/index.html`
(cache-busting — patrz HANDOFF.md §3).

## CI

`.github/workflows/ci.yml` sprawdza przy każdym pushu/PR: kompilację
wszystkich plików `.py`, testy pytest, import aplikacji (łapie brakujące
pliki/moduły — to realnie się kiedyś zdarzyło, patrz HANDOFF.md §6),
poprawność JSON-a rejestrów, i składnię plików JS frontendu.
