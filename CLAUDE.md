# CLAUDE.md — analiza-dzialki

> **Zacznij od `HANDOFF.md`, sekcja 0.** Ten plik to krótki przewodnik
> operacyjny (jak pracować w tym repo); `HANDOFF.md` to pełna historia
> projektu — co działa, co świadomie odłożone, co czeka na decyzję
> Klaudii. Nie zgaduj tego, co jest tam już opisane.

## Co to jest

FastAPI backend + statyczny frontend (vanilla JS + Leaflet), hostowany na
Render (Docker). Dla podanego numeru działki (TERYT) odpytuje ~12 darmowych
usług rządowych/OSM równolegle i zwraca skonsolidowaną analizę (ewidencja,
zagrożenia, media, hydrologia, plan zagospodarowania, wycena). Osobny
endpoint wyszukuje działki po miejscowości + rozmiarze.

Siostrzana appka: `wyszukiwarka-dzialek` (statyczny HTML, GitHub Pages,
osobne repo) — linki do portali ogłoszeniowych, bez backendu.

## Struktura

```
main.py                — FastAPI app, 5 tras HTTP, orkiestracja asyncio.gather
config.py               — URL-e usług, warstwy WMS, timeouty, TTL cache'u, tabele cen
geo_utils.py            — geometria/parsowanie
http_utils.py           — retry, wyścig mirrorów Overpass, describe_exc
services/*.py           — jedna usługa/sekcja na plik
tests/test_pure_logic.py — pytest, logika BEZ sieci (patrz niżej, dlaczego)
static/                 — index.html + app.js (jeden plik) + service worker
wfs_powiat_registry.json — rejestr 380 serwerów WFS per powiat
```

## Zanim zaczniesz zmieniać kod

1. Przeczytaj `HANDOFF.md` sekcja 0 — może to, co masz zrobić, jest już
   opisane jako zrobione/odłożone/zbadane.
2. **Sieć rządowa (ULDK/WFS/Overpass/ISOK/PIG-PIB/GIOŚ/GDOŚ) jest
   CAŁKOWICIE zablokowana z tego środowiska** (proxy odrzuca połączenia).
   `WebFetch` też nie działa na żadną domenę. Nie trać na to czasu —
   testuj logiką (pytest + monkeypatch/fake HTTP client), nie curlem na
   żywo. Do researchu użyj `WebSearch`.
3. Uruchom testy: `pip install -r requirements-dev.txt && pytest -q`.
   112+ testów, wszystkie bez sieci.

## Zasady, których nie łam

- **`HANDOFF.md` aktualizuj w TYM SAMYM commicie co zmiana kodu** — nowy
  endpoint, integracja, naprawiony/odkryty ślepy zaułek, zmieniona
  metodologia. Jeśli zmiana dotyczy tylko jednej appki, i tak zaktualizuj
  plik w OBU repo (`wyszukiwarka-dzialek` ma identyczną kopię, poza
  `TEST_PARCELS.md` — ten istnieje tylko tutaj).
- **Cache-busting przy KAŻDEJ zmianie `static/`**: podbij `?v=N` w
  `<script src="/static/app.js?v=N">` (`static/index.html`) ORAZ
  `CACHE_NAME` w `static/service-worker.js`. Bez tego przeglądarka (i PWA
  na telefonie) pokazuje starą wersję mimo poprawnego pusha.
- **Dodajesz nowy plik/moduł na topie repo albo w `services/`?** Dopisz go
  do `COPY` w `Dockerfile` — brakujący `COPY` to cicha awaria startu na
  Render (już się zdarzyło z `wfs_powiat_registry.json`).
- **Nowa integracja z zewnętrznym API**: nie zgaduj URL-a/nazwy
  warstwy/schematu. Znajdź potwierdzenie (cytowane projekty open-source,
  `GetCapabilities`, zrzut ekranu od Klaudii) albo powiedz wprost, że to
  niepewne — HANDOFF.md dokumentuje kilka przypadków, gdzie zgadywanie
  kosztowało dużo czasu.
- **Nie dodawaj cache'u dla danych z realną wagą decyzyjną** bez
  widocznego w UI `dane z: [data]` (patrz `services/cache.py` +
  `dataAgeNote()` w `app.js`) — "nie wiemy, czy dane są świeże" nigdy nie
  powinno wyglądać jak "sprawdzone teraz".
- **Testy pytest przy każdej naprawionej usterce/nowej logice** — wzorzec:
  fake HTTP client (`httpx.MockTransport`-style) lub monkeypatch, zero
  prawdziwych requestów. Zobacz `tests/test_pure_logic.py`.
- Backend zwraca zawsze `{"status": "ok"|"partial"|"error", ...}` per
  sekcja — nigdy nie ukrywaj awarii usługi jako fałszywe "brak
  zagrożenia"/"brak danych".

## Deploy

Push na `main` → Render (Docker, automatyczny, ~60-90s) i GitHub Pages dla
`wyszukiwarka-dzialek` (~30-60s). Zanim uznasz coś za gotowe po większej
zmianie backendu — sprawdź logi startowe na Render (import mógł się nie
udać) i przetestuj ręcznie jedną prawdziwą analizę na żywo, jeśli to
możliwe — to jedyna rzecz, której nie da się zweryfikować z tego
środowiska.
