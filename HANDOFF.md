# Handoff — Analiza Działki / Wyszukiwarka Działek

> **Ten plik jest teraz commitowany w obu repozytoriach (`analiza-dzialki` i
> `wyszukiwarka-dzialek`) i ma być aktualizowany na bieżąco.** Zasada: przy
> każdej zmianie, która wpływa na coś opisanego tutaj (nowy endpoint, nowa
> integracja, naprawiony/odkryty ślepy zaułek, zmieniona metodologia, nowa
> luka), zaktualizuj odpowiednią sekcję **w tym samym commit-cie/PR co
> zmiana kodu** — nie osobno, nie "później". Jeśli zmiana dotyczy tylko
> jednej appki, zaktualizuj plik w obu repo (treść ma być identyczna w obu
> miejscach — to jeden wspólny dokument o dwóch aplikacjach).

Dokument dla kolejnej instancji Claude przejmującej pracę nad tymi dwiema
aplikacjami. Cel: żebyś mógł/mogła działać dalej z Klaudią bez zadawania jej
pytań, na które odpowiedź jest już tutaj.

---

## 0. Zacznij tutaj — stan projektu na 2026-09-04

Ten dokument jest długi (opisuje ~4 dni intensywnej pracy) — ta sekcja to
skrót: co już działa, co jest świadomie odłożone, i co konkretnie czeka na
decyzję Klaudii. Szczegóły każdego punktu są w dalszych sekcjach (odnośniki
niżej), ale **nie musisz czytać całego pliku od razu** — zacznij tutaj.

### Co jest już zaimplementowane i działa (nie trzeba tego robić od nowa)

**Analiza Działki (`analiza-dzialki`) — backend FastAPI, 11 sekcji analizy
działki liczonych RÓWNOLEGLE (`asyncio.gather` w `main.py`), plus osobny
endpoint wyszukiwania po miejscowości/rozmiarze:**
- Moduły: `config.py` (stałe/timeouty), `geo_utils.py`, `http_utils.py`
  (generyczny retry `_get_with_retry`, wyścig mirrorów Overpass, naprawa
  pustych komunikatów wyjątków `describe_exc`), `services/*.py` (jeden
  plik na sekcję) — patrz sekcja 3 „Struktura repo".
- **Cache-aside w SQLite** (`services/cache.py`) dla WSZYSTKICH sekcji
  wzbogacających dane, z osobnym TTL na sekcję (`config.py`, `TTL_*`) —
  **plan zagospodarowania dołączony 2026-09-04** (`TTL_ZONING`, 7 dni,
  krótszy niż reszta — patrz sekcja 3, „Trzy optymalizacje wydajności").
  Identyfikacja działki (ULDK) świadomie wciąż NIE jest cache'owana.
  **`_conn_lock` (threading.RLock)** serializuje cały dostęp do SQLite —
  naprawione 2026-09-04 po realnym wyścigu wątków znalezionym przy
  testowaniu trwałego klienta HTTP (patrz ta sama sekcja).
- **Werdykt/checklista** (`services/verdict.py`, wzorowana na darmowym
  raporcie Działkopedii): score 0-100, poziom (dobra/do_sprawdzenia/
  wysokie_ryzyko), pełna lista wierszy — **4 poziomy**: `risk` (odejmuje
  punkty), `warning` (odejmuje punkty), `ok`, i **`unknown`** (sekcja bez
  danych — dodane 2026-09-04, NIGDY nie odejmuje punktów, ale dostaje
  własny wiersz z plakietką „BRAK DANYCH" zamiast być całkiem niewidoczna).
  `incomplete_sections` nadal istnieje osobno jako jedno zdanie
  podsumowujące nad kartą wyniku.
- **Lista kroków przed zakupem** (`services/due_diligence.py`) — 25-punktowa
  checklista due-diligence, odhaczana automatycznie tam, gdzie appka
  realnie coś sprawdziła.
- **Media/uzbrojenie terenu** (`services/utilities.py`) — detekcja przez
  piksele obrazu WMS GetMap (nie geometria wektorowa), z przybliżonym
  dystansem w metrach dla obecnych mediów, i `status: "error"` (nie
  fałszywe „ok, brak mediów") gdy WSZYSTKIE 6 warstw zawiedzie.
- **Plan zagospodarowania** (`services/zoning.py`) — KIAPP (nowy Rejestr
  Urbanistyczny) i KIMPZP (stary) odpytywane RÓWNOLEGLE, z szybkim
  podglądem GetMap przed wolnym/zawodnym GetFeatureInfo, i **retry przez
  `_get_with_retry` na `ConnectTimeout`/5xx** (naprawione 2026-09-04 — była
  to realna usterka zgłoszona przez Klaudię na żywej działce). Notatka o
  planie ogólnym/OUZ (zasada z 1.09.2026) dołączana, gdy nie znaleziono
  MPZP.
- **Budżet czasu dla `/api/resolve`** (`TIMEOUT_RESOLVE_BUDGET=50s`,
  `config.py`) — cała kaskada wyszukiwania działki (do 5 etapów) owinięta w
  `asyncio.wait_for`, więc appka sama zwraca czytelny błąd PO POLSKU zamiast
  crashować przez limit czasu proxy Render (naprawione 2026-09-04, był to
  KRYTYCZNY błąd całej analizy — patrz sekcja 3, „Krytyczny błąd").
- **Overpass API**: wszystkie skonfigurowane mirrory odpytywane RÓWNOLEGLE
  (wyścig, `asyncio.wait FIRST_COMPLETED`), nie sekwencyjnie — blokada
  jednego mirroru już nie opóźnia sprawdzenia drugiego.
- **Jeden trwały `httpx.AsyncClient`** (`main.py::_get_http_client()`) na
  cały czas życia serwera (utworzony w `lifespan`, zamykany przy
  shutdownie) zamiast nowego klienta przy KAŻDYM żądaniu — dodane
  2026-09-04, patrz sekcja 3, „Trzy optymalizacje wydajności".
- **`GET /api/analyze-stream`** — strumieniowa (Server-Sent Events)
  alternatywa dla `/api/analyze`, używana teraz przez frontend „Analiza
  działki": szybkie sekcje renderują się od razu, plan zagospodarowania i
  werdykt/wycena/lista kroków (które i tak potrzebują wszystkich sekcji
  naraz) dochodzą na końcu. `/api/analyze` nadal istnieje bez zmian —
  patrz sekcja 3.
- Jakość powietrza (GIOŚ), UI checklisty jako zwarty spis treści z linkami
  do kart szczegółów (nie duplikacja tekstu), PWA (manifest + service
  worker, network-first), CI (GitHub Actions), 112 testów pytest
  (`tests/test_pure_logic.py`, logika bez sieci — sieć rządowa jest
  całkowicie niedostępna z tego środowiska, patrz sekcja 8).
- Cache-busting: `static/app.js?v=32`, `CACHE_NAME` service workera `v24`
  — **podnoś oba przy KAŻDEJ zmianie `app.js`**.

**Wyszukiwarka Działek (`wyszukiwarka-dzialek`) — statyczny frontend:**
- Tablica `PORTALS` (5 portali z realnie działającymi filtrami: Otodom,
  OLX, Domiporta, Nieruchomości-online, GetHome), service worker, CI
  (składnia JS + walidacja JSON). Patrz sekcja 5.

### Co jest świadomie ODŁOŻONE (nie zaczynaj tego bez wyraźnej prośby Klaudii)

- **Status planu ogólnego jako osobny sygnał** (Rejestr Urbanistyczny) —
  akty na etapie projektu publikowane są na razie WYŁĄCZNIE na BIP-ach
  poszczególnych gmin, krajowy agregator KIAPP nie ma tych danych do końca
  okresu przejściowego (30.09/30.11.2026). Nie implementuj przed tą datą —
  patrz sekcja 7, „Zbadane: status planu ogólnego".
- **`epodgik.pl` (dostawca Geo-System)** — Klaudia sama znalazła w
  DevTools bezpośredni endpoint WMS jednej gminy zwracający realny status
  prawny MPZP (`Status: prawnie wiążący...`). Obiecujący trop, ale wymaga
  rejestru gmina→dostawca (podobnego do `WFS_POWIAT_REGISTRY` w sekcji 4)
  — większe przedsięwzięcie, NIE zaimplementowane. Patrz sekcja 7.
- **`WebFetch` jest całkowicie zablokowany w tym środowisku dla WSZYSTKICH
  domen** (potwierdzone: gov.pl, geoportal.gov.pl, dane.gov.pl, nawet
  en.wikipedia.org) — działa tylko `WebSearch`. Nie trać na to czasu
  ponownie, po prostu użyj `WebSearch` do researchu.

### TODO — aktualne na 2026-09-04, czeka na decyzję/polecenie Klaudii

1. **WSTRZYMANE: podnieść `TIMEOUT_OVERPASS` dalej** (obecnie 30s) —
   Klaudia potwierdziła na żywo, że odległość do drogi gminnej dalej czasem
   daje `ReadTimeout` nawet przy 30s. Wznów TYLKO gdy wyraźnie poprosi.
2. **WSTRZYMANE: zbadać zasięg sieci komórkowej (UKE)**, jak pokazuje
   Działkopedia. Najpierw research (czy jest darmowe, otwarte API UKE) —
   NIE implementuj bez potwierdzonego, realnego źródła danych.
3. **WSTRZYMANE: zbadać skalę Bortle (zanieczyszczenie światłem/hałas)**,
   jak pokazuje Działkopedia. Jw. — tylko research, dopóki Klaudia nie
   poprosi o wdrożenie.
4. **WYKONANE 2026-09-04: wszystkie 3 propozycje optymalizacji
   wydajności** zgłoszone Klaudii tego samego dnia — (a) trwały
   `httpx.AsyncClient`, (b) cache dla planu zagospodarowania, (c)
   strumieniowanie SSE. Szczegóły i dwa realne błędy współbieżności
   znalezione przy okazji — patrz sekcja 3, „Trzy optymalizacje
   wydajności". **Nadal otwarte, nie wdrożone w tej rundzie**: przycisk
   „odśwież teraz" (pomijanie cache'u na żądanie) i dysk trwały na Render —
   patrz ta sama sekcja.
5. **Działka testowa „Korbielów 3917/5"** (`241704_2.0002.3917/5`, gmina
   Jeleśnia, pow. żywiecki) była użyta na żywo przez Klaudię 2026-09-04
   (m.in. do zgłoszenia błędu ConnectTimeout dla MPZP), ale NIE została
   jeszcze dopisana do `TEST_PARCELS.md` — dopisz ją, jeśli Klaudia znów
   się nią posłuży jako działką testową (patrz sekcja 8, punkt 9).

---

## 1. Dwie osobne aplikacje, dwa osobne repozytoria

| | **Analiza Działki** | **Wyszukiwarka Działek** |
|---|---|---|
| Cel | Pełna analiza jednej, konkretnej działki (ewidencja, zagrożenia, media, hydrologia, plany, wycena) + wyszukiwanie działek po miejscowości/rozmiarze | Szybki dostęp do ofert sprzedaży (linki do portali z filtrami) |
| Repo | `github.com/klaudiamoscinska-art/analiza-dzialki` | `github.com/klaudiamoscinska-art/wyszukiwarka-dzialek` |
| Live URL | `https://analiza-dzialki.onrender.com` | `https://klaudiamoscinska-art.github.io/wyszukiwarka-dzialek/` |
| Hosting | Render.com (Docker, FastAPI) | GitHub Pages (statyczny HTML) |
| Backend | Tak — Python/FastAPI | Nie — czysty HTML/CSS/JS |
| Deploy | Automatyczny po pushu do `main` (Render webhook), ~60-90s | Automatyczny po pushu do `main` (GitHub Pages), ~30-60s |

**Te dwie appki są całkowicie niezależne.** Osobne repo, osobne tokeny,
osobny deploy. Nie mylić kontekstu.

---

## 2. Jak uzyskać dostęp do GitHub (WAŻNE — rób to za każdym razem od nowa)

Klaudia nie przechowuje tokenów między sesjami — trzeba je wygenerować na
nowo na początku każdej rozmowy, w której trzeba coś wgrać.

**Bezpośredni link do tworzenia tokenu (na telefonie ustawienia GitHub są
trudne do znalezienia, ten link omija to):**
`github.com/settings/personal-access-tokens/new`

Poproś Klaudię o token z takimi ustawieniami:
- **Repository access**: „Only select repositories" → wybierz repo, nad
  którym akurat pracujesz (`analiza-dzialki` lub `wyszukiwarka-dzialek`)
- **Permissions → Repository permissions → Contents**: „Read and write"
- **Expiration**: 7 dni (rozsądne minimum)

Po zakończeniu pracy przypomnij Klaudii, żeby usunęła token w
`github.com/settings/personal-access-tokens` — to dobra praktyka
bezpieczeństwa, o którą sama prosiła na początku współpracy. **Nigdy nie
proponuj "zapamiętania" tokenu na stałe** — to był świadomy wybór Klaudii
po tym, jak raz o to poprosiła i odmówiono jej z wyjaśnieniem dlaczego.

### Mechanika wgrywania (sprawdzony, działający wzorzec)
Używaj GitHub Contents API bezpośrednio przez `curl` w `bash_tool`:
1. `GET /repos/{owner}/{repo}/contents/{path}` żeby dostać aktualne `sha`
   pliku (potrzebne do aktualizacji istniejącego pliku)
2. `PUT /repos/{owner}/{repo}/contents/{path}` z treścią w base64 i tym
   `sha`
3. **Zawsze weryfikuj po wgraniu** — pobierz plik ponownie i zrób `diff`
   względem lokalnej wersji. Był realny przypadek, że appka nie
   aktualizowała się mimo poprawnego push (patrz sekcja 6, Dockerfile).
4. Po weryfikacji na GitHub, **poczekaj i sprawdź na żywo** na
   wdrożonym adresie (Render: ~90s, GitHub Pages: ~45-60s) — nie
   zakładaj, że push = appka już działa.

---

## 3. Analiza Działki — pełna specyfikacja techniczna

### Struktura repo
Backend był do 2026-09-01 jednym 1720-liniowym `main.py`; od tego dnia jest
podzielony na moduły (identyczna logika, tylko przeniesiona — patrz `git log`
dla commitu "Podziel main.py na moduły" jeśli potrzebujesz historii):
```
main.py                      — tylko: FastAPI app, middleware, 4 trasy HTTP
config.py                     — stałe: URL-e usług, warstwy, timeouty, tabele cen
geo_utils.py                  — parsowanie/pomiary geometrii, czyszczenie tekstu
http_utils.py                 — generyczne helpery HTTP (retry, Overpass, WMS GetFeatureInfo)
services/
  uldk.py                      — sekcja 0: lookupy ULDK (po punkcie, po ID/nr, skan obrębów)
  geocoding.py                 — geokodowanie GUGiK (adresy, gminy) + resolve_address_to_parcels
  wfs_search.py                 — "Szukaj działki": rejestr WFS, enumeracja+dopasowanie osi,
                                   search_parcels_universal (najbardziej złożona część appki)
  cadastre.py                   — sekcja 1: KIEG + budynki (OSM)
  hazards.py                    — sekcja 2/4: osuwiska (SOPO), powódź (ISOK), podtopienia (PIG-PIB)
  utilities.py                  — sekcja 3: media/GESUT (KIUT)
  nearby_features.py            — droga gminna + cieki (oba z Overpass)
  zoning.py                     — sekcja 5: plany zagospodarowania (KIAPP/KIMPZP)
  valuation.py                  — sekcja 6/7: linki (GUNB/geoportal/e-mapa) + wycena statystyczna
tests/                        — pytest dla logiki bez zależności sieciowych (patrz niżej)
requirements.txt              — zależności produkcyjne (pinned)
requirements-dev.txt          — + pytest/pytest-asyncio, do lokalnego dev/CI
pytest.ini
.github/workflows/ci.yml      — py_compile + pytest + import + walidacja JSON + node --check na JS
Dockerfile                    — MUSI kopiować main.py+config.py+geo_utils.py+http_utils.py+
                                 services/, wfs_powiat_registry.json, static/ (sprawdź to PIERWSZE,
                                 jeśli dodasz nowy plik .py na topie repo albo nowy moduł w services/
                                 — brakujący COPY tu daje dokładnie tę samą cichą awarię co kiedyś
                                 z wfs_powiat_registry.json)
wfs_powiat_registry.json     — rejestr 380 bezpośrednich serwerów WFS per powiat (wczytywany przez
                                 services/wfs_search.py — ścieżka liczona względem project root,
                                 NIE względem services/, bo plik JSON leży w topie repo)
static/
  index.html                 — cały HTML+CSS, zakładki, formularze
  app.js                     — cała logika frontendu (jeden plik)
  manifest.json
  service-worker.js          — CACHE_NAME="analiza-dzialki-v12", network-first,
                                 fetch(..., {cache:"no-store"}) — patrz notatka niżej
  icons/                     — wygenerowane programowo (PIL), patrz sekcja 7
```

**Dlaczego podział:** `main.py` miał 1720 linii / 45 funkcji top-level w jednym
pliku, bez testów. Podział 1:1 zachowuje dokładnie tę samą logikę (zweryfikowane:
identyczny zestaw tras w `/openapi.json` przed/po, `pyflakes` bez ostrzeżeń na
wszystkich nowych plikach, pełny lokalny smoke-test serwera, 30/30 testów pytest).
Backend nie mógł być przetestowany end-to-end na żywo w sandboxie, w którym
robiono ten refaktor — rządowe API (ULDK/WFS/Overpass/ISOK/PIG-PIB) są tam
zablokowane przez proxy. **Jeśli po tej zmianie appka na Render zwraca 500 albo
się nie uruchamia, sprawdź NAJPIERW logi startowe** (import mógł się nie udać) —
ale zestaw testów lokalnych był kompletny na tyle, na ile dało się to zrobić bez
sieci do prawdziwych usług.

### Testy (pytest, dodane 2026-09-01)
`tests/test_pure_logic.py` — 110 testów dla logiki bez zależności sieciowych:
parsowanie geometrii ULDK (WKT/EWKT/WKB), `_rectangle_side_lengths`,
`_feature_info_has_data`, `estimate_value`, buildery linków (GUNB/geoportal/
e-mapa), `_within_poland`, rejestr WFS (`_lookup_wfs_config`), i najważniejsze —
pełna logika dopasowania/rankingu w `search_parcels_universal` (filtr
powierzchni, filtr prostokątności, RMS, tryb `dims_as_maximum`, tryb
pojedynczego wymiaru) przez monkeypatch `_gather_nearby_parcels` (żeby nie
dotykać sieci). **Nie testują** samych wywołań do usług rządowych — do tego
nadal służy metodologia z sekcji 8 (curl na żywo). Uruchom: `pip install -r
requirements-dev.txt && pytest`.

### Logging (dodane 2026-09-01)
Wcześniej `main.py` nie miał żadnego loggera — błędy zewnętrznych usług były
całkowicie ciche (`except Exception: pass`/`return {"status": "error", ...}`
bez śladu po stronie serwera). Teraz każdy moduł ma `logger =
logging.getLogger("analiza_dzialki")` (zdefiniowany raz w `config.py`,
importowany wszędzie indziej) i loguje `logger.warning(...)` przy każdej
awarii zewnętrznej usługi (WFS, ULDK, Overpass, SOPO, ISOK, PIG-PIB, KIEG,
MPZP/APP, geokoder). To ułatwia odróżnianie przejściowych awarii od trwałych
(patrz metodologia, sekcja 8, punkt 7) — sprawdzaj logi na Render, nie tylko
odpowiedź API.

### Timeouty i retry (dodane 2026-09-01)
Timeouty są teraz nazwanymi stałymi w `config.py` (`TIMEOUT_WFS_POWIAT=45s`,
`TIMEOUT_OVERPASS=14s` itd. — te same wartości co wcześniej, tylko
scentralizowane) zamiast rozrzuconych literałów. WFS powiatowe (najbardziej
zawodne z całej appki) mają teraz jeden retry z 2s opóźnieniem
(`_get_with_retry` w `http_utils.py`) na błędy połączenia/timeoutu — wcześniej
nie miały żadnego retry poza ULDK.

### Cache-busting — KRYTYCZNE, rób to przy KAŻDEJ zmianie app.js
`index.html` ładuje skrypt jako `<script src="/static/app.js?v=N"></script>`.
**Aktualny numer: v=20.** Przy każdej zmianie `app.js` podbij `N` o 1 i
zaktualizuj `index.html`. Bez tego przeglądarka użytkowniczki może pokazywać
starą, zbuforowaną wersję — to się realnie zdarzyło kilkukrotnie i było
mylące (appka "nie widziała" zmian, mimo że kod na GitHub był poprawny).

Backend ma też middleware wymuszający `Cache-Control: no-store` na
wszystkich `/api/*` — to osobna warstwa ochrony przed cache'owaniem
odpowiedzi API przez przeglądarkę (inny problem niż cache samego JS).

**Trzeci, osobny poziom cache'owania — service worker + PWA dodana do
ekranu głównego telefonu (potwierdzone na żywo 2026-09-03).** Nawet przy
poprawnym cache-bustingu `app.js` i middleware `no-store` na `/api/*`,
appka zainstalowana z ikonki na telefonie (zwłaszcza iOS Safari „Dodaj do
ekranu głównego") może nadal pokazywać starą wersję UI, bo:
1. `/static/*` (w tym `index.html` i `app.js`) nie ma jawnego nagłówka
   `Cache-Control` (StaticFiles w main.py go nie ustawia) — przeglądarka
   stosuje wtedy własną heurystykę i może serwować plik z dysku bez
   zapytania do sieci, nawet gdy service worker robi `fetch()` „najpierw
   sieć". Naprawione: `service-worker.js` teraz woła
   `fetch(event.request, { cache: "no-store" })` i precache'uje
   `APP_SHELL` przez `new Request(url, { cache: "reload" })` — obie ścieżki
   wymuszają realne zapytanie do sieci, nie tylko odpytanie SW.
2. iOS Safari w trybie „dodane do ekranu głównego" rzadko sprawdza, czy
   plik `service-worker.js` się zmienił (rzadziej niż zwykła karta
   przeglądarki) — SW może więc zostać stary przez dłuższy czas nawet po
   deployu. Jedyny pewny sposób wymuszenia aktualizacji z naszej strony:
   zmienić bajty `service-worker.js` (np. podbić `CACHE_NAME`) przy każdym
   deployu, który dotyka `static/`. **Rób to przy każdej zmianie
   `index.html`/`app.js`/`manifest.json`, tak jak cache-bust `?v=N`.**
   Aktualny `CACHE_NAME`: `analiza-dzialki-v12`.
3. Jeśli mimo to appka na telefonie nadal pokazuje starą wersję: to nie
   błąd kodu — poproś użytkowniczkę, żeby całkowicie zamknęła appkę
   (nie tylko zminimalizowała) i otworzyła ją ponownie z siecią; jeśli to
   nie pomoże, usunięcie i ponowne dodanie ikonki z ekranu głównego
   wymusza świeżą rejestrację service workera.

### Endpointy API (aktualny stan)
- `GET /api/resolve?query=...` — przyjmuje "Miejscowość numer" albo pełny
  TERYT. Zwraca `{resolved: true, teryt_id}` (jednoznaczne) albo
  `{resolved: false, candidates: [...]}` (do wyboru — frontend pokazuje
  listę). Wewnętrznie: (1) bezpośrednie dopasowanie nazwy obrębu przez
  ULDK, (2) fallback: geokodowanie nazwy → skan wszystkich obrębów gminy,
  (3) fallback: wzorzec „Nazwa-N" dla miast podzielonych na numerowane
  obręby (np. „Bochnia-6").
- `GET /api/resolve-address?query=...` — szukanie po adresie (ulica+numer).
  Geokoduje przez oficjalne API GUGiK, potem `GetParcelByXY`.
- `GET /api/analyze?parcel_id=...` — główny endpoint, zwraca pełną analizę
  w jednym JSON-ie (ewidencja, budynki, osuwiska, media, hydrologia, plany,
  pozwolenia, wycena, linki do map, `nearest_road`). Bez zmian od 2026-09-04
  poza wewnętrznym refaktorem (dzieli `_section_specs()`/`_compute_derived()`
  z endpointem niżej) — kontrakt/odpowiedź identyczne jak wcześniej.
- `GET /api/analyze-stream?parcel_id=...` — **dodane 2026-09-04**,
  strumieniowa (Server-Sent Events) alternatywa dla powyższego, której teraz
  używa frontend „Analiza działki" (patrz sekcja 3, „Trzy optymalizacje
  wydajności" — pełny opis formatu zdarzeń `meta`/`section`/`done`).
- `GET /api/search-by-parcel-size?place=&area_m2=&width_m=&length_m=&dims_as_maximum=`
  — „Szukaj działki": jeden uniwersalny endpoint, dowolna kombinacja
  kryteriów (patrz sekcja 3.3).

### 3.1 Zakładka „Analiza działki"
Dwa sposoby wejścia — numer działki (pole `parcelInput`) albo adres (pole
`addressInput`) — oba prowadzą do tej samej funkcji `analyzeTerytId()` w
`app.js`, która renderuje wszystkie sekcje. Jeśli wyszukiwanie zwróci kilka
kandydatów (np. ta sama nazwa miejscowości w kilku gminach, albo ten sam
numer działki w kilku obrębach), pokazuje się lista wyboru
(`renderCandidatePicker`), a po wybraniu — trwały przełącznik
(`renderSwitcherBar`) zostaje widoczny nad wynikami, żeby można było się
przełączać między kandydatami bez ponownego wyszukiwania.

**Źródła danych per sekcja:**

| Sekcja | Usługa | Status/uwagi |
|---|---|---|
| Ewidencja gruntów (identyfikator, gmina, powiat, obręb) | ULDK `GetParcelById`/`GetParcelByIdOrNr` | Działa dobrze, podstawowe dane |
| Ewidencja gruntów (klasoużytki, grupa rejestrowa, dokładniejsze dane) | KIEG WMS `GetFeatureInfo` | **Częściowo martwe** — dla wielu działek zwraca ogólny komunikat zamiast danych. Sprawdzone wielokrotnie, różne wersje WMS/CRS — to strukturalne ograniczenie usługi, nie błąd kodu |
| Budynki (obrys, ale NIE liczba pięter/atrybuty) | OpenStreetMap Overpass API | KIEG/BDOT nie udostępniają atrybutów budynku przez żadne wolne API (potwierdzone) — OSM to najlepsza dostępna alternatywa, z zastrzeżeniem że dane mogą być niepełne |
| Zagrożenie osuwiskowe | SOPO PIG-PIB, ArcGIS `identify` (NIE `GetFeatureInfo` — zablokowane przez WAF) | Działa dobrze |
| Media/GESUT | KIUT, metoda pikselowa przez `GetMap` (GetFeatureInfo strukturalnie zepsute — zwraca ten sam komunikat zawsze) | Działa, ale to przybliżenie wizualne, nie dokładne atrybuty. **Od 2026-09-03 też jako warstwa mapy** — zbiorczy checkbox "Media / uzbrojenie terenu (GESUT)" + 6 podkategorii (Wodociąg/Kanalizacja/Gaz/Elektroenergetyka/Ciepłociąg/Telekomunikacja) w rozwijanym `<details>` pod nim. **Potwierdzone na żywo przez Klaudię: przełącznik działa, linie mediów faktycznie rysują się na mapie.** Historia UI (żeby nie powtarzać tych samych błędów) — patrz notatka "Wzorzec: rozwijane podkategorie warstw mapy" niżej. |
| Plany zagospodarowania | **Dwa źródła próbowane po kolei**: (1) nowy `KrajowaIntegracjaAktowPlanowaniaPrzestrzennego` (Rejestr Urbanistyczny, wystartował 1 lipca 2026, **obecnie ogólnokrajowo prawie pusty** — gminy dopiero wgrywają dane), (2) stary `KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego` jako fallback | Oba używają strategii: `GetMap` (szybkie, niezawodne) jako sonda, dopiero potem `GetFeatureInfo` z limitem czasu (część serwerów gmin wisi w nieskończoność na `GetFeatureInfo`). **Historia**: usunięte jako warstwy mapy 2026-09-03, przywrócone tego samego dnia na życzenie Klaudii — teraz zbiorczy checkbox "Plany zagospodarowania" (`L.layerGroup` z obu źródeł naraz, bo to dwa różne serwery WMS, nie da się połączyć w jedno GetMap jak w GESUT) + 2 podkategorie (MPZP starszy / Rejestr Urbanistyczny) w rozwijanym `<details>`. Tabelaryczny panel (sekcja 5 w `main.py`) bez zmian przez całą tę historię. **Zdiagnozowane i naprawione 2026-09-03 (dwa etapy — pierwsza diagnoza była błędna, patrz niżej)**: Klaudia zgłosiła "checkbox jest, ale warstwa się nie rysuje". Etap 1: zweryfikowane w Playwright (podstawione odpowiedzi WMS), że kod poprawnie wysyła `GetMap`; zapytana, czy panel tekstowy dla tego samego adresu też mówi "brak planu" — potwierdziła, że tak, więc wstępny wniosek brzmiał "to nie błąd, po prostu brak danych u źródła". **To była zbyt wczesna konkluzja.** Etap 2: Klaudia przysłała zrzut ekranu z oficjalnego viewera GEOPORTAL, gdzie dla TEJ SAMEJ okolicy po włączeniu warstwy "Plany zagospodarowania" (i jej dzieci: "Rysunki miejscowych planów...", "Granice opracowania...", "Studium") coś się jednak rysuje — sprzeczność z "brak danych". Poproszona o zrzut ekranu `GetCapabilities` usługi KIMPZP (bo sieć rządowa zablokowana w tym sandboksie, także dla `WebFetch`) — **ujawnił prawdziwą przyczynę**: `KIMPZP_LAYERS = "plany"` to nazwa ogólnej warstwy-grupy, która nie renderuje się niezawodnie; prawdziwe warstwy z treścią to `raster` (gminy z planami rastrowymi) i `wektor-str,wektor-lzb,wektor-pow,wektor-lin,wektor-pkt` (gminy z planami wektorowymi) — każda gmina publikuje TYLKO jeden z tych dwóch formatów, więc jedna generyczna nazwa `"plany"` nigdy nie trafiała w oba. **Naprawione**: `KIMPZP_LAYERS` w `config.py` (używane przez backend: i sondę `GetMap`, i `GetFeatureInfo` panelu) oraz osobna, zduplikowana definicja w `static/app.js` (`mpzpLayer`) — obie zmienione na `"raster,wektor-str,wektor-lzb,wektor-pow,wektor-lin,wektor-pkt"`. To naprawia jednocześnie panel tekstowy I mapę, bo obie ścieżki dotąd używały tej samej złej nazwy `"plany"`. **Lekcja na przyszłość**: gdy WMS "działa" (zwraca 200, poprawny PNG) ale wizualnie nic nie pokazuje mimo że dane na pewno gdzieś są (potwierdzone np. w oficjalnym viewerze GUGiK) — nie zakładaj automatycznie "brak danych", sprawdź `GetCapabilities` tej usługi (`?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.1.1` na tym samym URL-u) pod kątem tego, czy używana nazwa warstwy to faktycznie renderowalna warstwa z treścią, czy tylko nazwa grupy/kontenera. **KIAPP (`layers=app`, Rejestr Urbanistyczny) NIE był tak sprawdzony** — możliwe, że ma tę samą klasę błędu, ale HANDOFF już wcześniej dokumentował ten rejestr jako "ogólnokrajowo prawie pusty", więc na razie zostawione bez zmian; jeśli problem z tą warstwą wróci, zrób ten sam test `GetCapabilities` na `KrajowaIntegracjaAktowPlanowaniaPrzestrzennego` zanim uznasz to za brak danych. |

**Fallback `GetParcelById` w `/api/resolve` (od 2026-09-03)**: Klaudia
zgłosiła, że ręczne wpisanie poprawnego, zweryfikowanego identyfikatora
TERYT (`121505_2.0001.636/3`, obręb Łętownia, gmina Jordanów, powiat
suski — sprawdzone niezależnie przez polska.e-mapa.net, inny dostawca
danych EGiB) w "Analiza działki" dawało "nie znaleziono", mimo że
działka realnie istnieje. Próba obejścia przez wpisanie "Łętownia
636/3" (nazwa obrębu + numer) też zawiodła — bo Łętownia to obręb, nie
gmina, a krok 2 pipeline'u (`geocode_gmina_candidates`) próbuje
geokodować podaną nazwę WYŁĄCZNIE jako gminę.

Diagnoza: `/api/resolve` (`main.py`) próbował znaleźć pełny identyfikator
TERYT tylko przez `GetParcelByIdOrNr` (`uldk_search_candidates`,
`services/uldk.py`) — combo endpoint ULDK, który wewnętrznie próbuje "po
ID" i "po nazwie+numerze" naraz. Ten konkretny przypadek nie został przez
niego znaleziony. **Naprawione**: nowa funkcja `find_parcel_by_id_direct()`
(`services/uldk.py`) próbuje bardziej bezpośredniego, pojedynczego
zapytania `GetParcelById` (ten sam typ zapytania, którego już używa
`scan_gmina_obreby_for_parcel` — potwierdzona różnica w kształcie
odpowiedzi między tymi dwoma typami zapytań, więc jeden może zadziałać
tam gdzie drugi zawodzi). Wywoływane w `/api/resolve` gdy zapytanie
wygląda jak pełny TERYT (≥2 kropki, brak spacji) i pierwsza próba nic
nie zwróciła. 2 nowe testy pytest (`find_parcel_by_id_direct` z fałszywym
klientem HTTP — parsowanie udanej i nieudanej odpowiedzi).

**To NIE wystarczyło** — Klaudia potwierdziła na żywo (2026-09-03), że
`121505_2.0001.636/3` nadal nie jest znajdywane, na żadnej z trzech
niezależnych ścieżek: surowy TERYT (`GetParcelByIdOrNr`), surowy TERYT
(`GetParcelById`, ten fallback), ani "Jordanów 636/3" (skan obrębów
gminy — `scan_gmina_obreby_for_parcel`, która też w środku woła
`GetParcelById`). Wszystkie trzy w końcu odpytują ULDK o dokładnie ten
sam identyfikator, więc zbieżna porażka wszystkich trzech to mocny
sygnał, że **ULDK po prostu nie ma tej konkretnej działki
zaindeksowanej** (potwierdzona istnieje w szerszym systemie EGiB — inny
dostawca, polska.e-mapa.net/Geo-System, ją widzi) — nie błąd w naszym
kodzie. `polska.e-mapa.net` też jest zablokowane w tym sandboksie
(sprawdzone przez `WebFetch`), więc nie dało się tego zweryfikować z
tej strony niezależnie.

**Dodane zamiast czwartej ślepej próby**: logowanie surowej odpowiedzi
ULDK w `uldk_search_candidates()` i `find_parcel_by_id_direct()`
(`logger.info`, pierwsze 300 znaków tekstu odpowiedzi) przy każdym "nie
znaleziono". Jeśli to się powtórzy, **sprawdź logi Render** zamiast
zgadywać kolejną strategię zapytania — dokładny tekst odpowiedzi ULDK
powinien jednoznacznie pokazać, czy to "brak w rejestrze" (nic do
zrobienia po naszej stronie) czy coś diagnostycznie innego (błąd
formatu, ukryty timeout w treści 200 OK, itp.), zamiast dalej zgadywać
w ciemno bez możliwości zweryfikowania czegokolwiek na żywo.

**Etap 4 w `/api/resolve` — szukanie przez powiat + numer (od 2026-09-03,
na życzenie Klaudii)**: to NIE jest kolejna ślepa próba tego samego
zapytania ULDK co wyżej — to nowa, dodatkowa interpretacja wpisanego
tekstu. Wcześniej "Name Number" próbowało tylko: (2) "Name" = gmina, (3)
"Name" = wzorzec numerowanego obrębu miasta. Teraz dochodzi (4) "Name" =
**powiat** (np. "suski 636/3") — nowa funkcja
`geocode_powiat_gmina_prefixes()` (`services/geocoding.py`, ten sam
wzorzec `pow_nazwa` co już użyty w `geocode_powiat_gmina_points()` dla
"Szukaj działki", ale zwraca `gmina_prefix` zamiast współrzędnych) +
`scan_gmina_obreby_for_parcel()` uruchomiony dla KAŻDEJ gminy w tym
powiecie. To pokrywa dokładnie przypadek Łętowni: nazwa obrębu nie jest
nazwą gminy (etap 2 nie znajdzie), nie pasuje do wzorca numerowanego
miasta (etap 3 nie znajdzie), ale nazwa powiatu ("suski") jest znana.
Kosztowniejsze niż etapy 2-3 (skan wszystkich gmin powiatu × do 40
obrębów każda, współbieżnie) — akceptowalne dla ręcznego, jednorazowego
wyszukiwania, nie coś do wywoływania automatycznie/często. 2 nowe testy
pytest (`geocode_powiat_gmina_prefixes` z fałszywym klientem HTTP —
parsowanie + deduplikacja, i pusta lista przy błędzie sieci). **Też NIE
zweryfikowane na żywo** — jeśli to zadziała, świetnie; jeśli nie, to
kolejny mocny dowód na "ULDK nie ma tej działki", nie warty kolejnej
próby innej kombinacji zapytań.

**Uwaga dla przyszłej sesji**: Klaudia zwróciła też uwagę, że "po
wpisaniu całego numeru powinna się odnaleźć bez dodatkowych pytań" — to
już jest zaimplementowane poprawnie (`if len(candidates) == 1: return
{"resolved": True, ...}` w `main.py` — brak pickera dla jednoznacznego
trafienia), po prostu było niewidoczne, bo samo wyszukiwanie nic nie
zwracało. Nie ma tu osobnego błędu do naprawienia — sprawdzenie się
samo, jak tylko któryś z etapów faktycznie znajdzie działkę.

**Etap 4 też zawiódł ("suski 636/3" → nadal "Nie znaleziono działki") —
prawdziwy przełom: Klaudia zgłosiła identyczny objaw na INNYM dostawcy
(2026-09-03).** Po tym jak wszystkie 4 dotychczasowe strategie (wszystkie
oparte o zapytania ID-owe do ULDK: `GetParcelByIdOrNr`, `GetParcelById`,
skan obrębów gminy, skan obrębów powiatu) zawiodły identycznie dla
`121505_2.0001.636/3`, Klaudia opisała, że widziała **ten sam objaw na
polska.e-mapa.net** — dla zupełnie innej, niepowiązanej działki (Koszarawa
8451/19) bezpośrednie wyszukanie po numerze też nic nie dawało, ale
działka stała się "widoczna"/wyszukiwalna dopiero PO tym, jak znalazła
sąsiednią działkę (Koszarawa 9102/117) przeglądając mapę. To silna
poszlaka, że to nie błąd w tej aplikacji ani w konkretnych danych ULDK,
tylko realna cecha/usterka tego, jak EGiB/ULDK indeksuje niektóre działki
pod kątem wyszukiwania PO NUMERZE — skoro dwóch niezależnych dostawców
(ULDK i cokolwiek stoi za e-mapa) pokazuje ten sam wzorzec, to nie
przypadek.

**Naprawa: `scan_wfs_for_parcel_number()` (nowa funkcja w
`services/wfs_search.py`) — wyszukiwanie PRZEZ GEOMETRIĘ zamiast przez
indeks ID**, dodane 2026-09-03 zaraz po etapie 4. Zamiast pytać ULDK "czy
istnieje działka o tym identyfikatorze" (co zawodzi), automatyzuje
dokładnie to, co podobno działa ręcznie na e-mapa — "przeglądanie
sąsiednich działek":
1. Pobiera prawdziwe GEOMETRIE działek z własnego serwera WFS danego
   powiatu (ten sam mechanizm co "Szukaj działki", potwierdzony na żywo
   dla powiatu suskiego) w promieniu wokół punktu-kotwicy danej gminy —
   `enumerate_parcel_points_in_area()`, już istniejąca funkcja, ponownie
   użyta.
2. Dla KAŻDEGO znalezionego punktu-kandydata odpytuje ULDK o
   `GetParcelByXY` (zapytanie PRZESTRZENNE, indeksowane inaczej niż
   zapytanie po ID — to samo zapytanie używane wszędzie indziej w tej
   aplikacji, m.in. do rozwiązywania każdego wyniku "Szukaj działki").
3. Filtruje po numerze działki (`teryt_id` kończący się dokładnie na
   szukanym numerze).

Podłączone jako etap 3 (po ID-owym skanie obrębów gminy, gdy ten nic nie
znajdzie) ORAZ etap 5b (analogicznie, po ID-owym skanie obrębów powiatu) —
`main.py`'s `resolve_parcel()` ma teraz lokalną funkcję pomocniczą
`scan_gminas_both_ways()` używaną w obu miejscach, żeby nie duplikować tej
logiki. Wymaga współrzędnych (`lon`/`lat`) danej gminy — dodane teraz do
`geocode_gmina_candidates()` i `geocode_powiat_gmina_prefixes()`
(wcześniej zwracały tylko `gmina_prefix`, nie współrzędne; wyciągane z
tego samego pola `geometry.coordinates`, które geokoder GUGiK już zwraca —
bez dodatkowego zapytania sieciowego). 6 nowych testów pytest (w sumie
48): parsowanie współrzędnych w obu funkcjach geokodujących (w tym
przypadek braku geometrii — pole `lon`/`lat` po prostu nie występuje w
wyniku), i `scan_wfs_for_parcel_number` z fałszywym `enumerate`/`find_by_xy`
(dopasowanie po numerze, brak dopasowania, błąd enumeracji). **Nadal NIE
zweryfikowane na żywo** (patrz uzasadnienie wyżej) — ale to jest
GENUINE nowa ścieżka odkrywania działek, nie kolejna wariacja tego samego
zapytania ID, więc uzasadnia kolejną próbę mimo wcześniejszej deklaracji
"nie zgaduję czwartej kombinacji". Jeśli to też zawiedzie, kolejnym krokiem
NIE powinna być kolejna kombinacja zapytań, tylko realny dostęp do logów
Render (`logger.info` z etapu wcześniejszego) albo do samego ULDK/WFS z
środowiska, które nie ma zablokowanych domen rządowych.

**Wzorzec: rozwijane podkategorie warstw mapy (`static/app.js`, `addLayerGroupRow()`)**
GESUT i Plany zagospodarowania to jedyne dwie warstwy z podkategoriami;
obie używają wspólnej funkcji `addLayerGroupRow(container, label,
subcategories)`. Historia czterech iteracji tego dnia (2026-09-03), warto
znać zanim dotkniesz tego kodu:
1. **Płaskie checkboxy obok EGiB** (pierwsza wersja GESUT: 6 podkategorii
   jako osobne pozycje w `L.control.layers`) — Klaudia oceniła jako
   "brzydkie", bo nic nie sugerowało, że to podgrupa GESUT.
2. **`<details>` wstrzyknięty do `L.control.layers`, ale tylko dla GESUT**
   — zadziałało dla jednej grupy (bo była ostatnia w liście), ale przy
   dodaniu drugiej grupy (Plany zagospodarowania) okazało się, że to
   podejście nie skaluje się: `L.Control.Layers._update()` czyści tylko
   swoje wewnętrzne listy (`_baseLayersList`/`_overlaysList`), więc
   wstrzykiwanie wewnątrz nich znika przy pierwszym kliknięciu
   jakiegokolwiek checkboxa — a doklejanie PO całej sekcji (jak zrobiono
   dla GESUT) układa oba `<details>` jedno pod drugim na samym dole,
   niezależnie od kolejności wierszy, więc rozwinięcie jednej grupy
   wizualnie "podszywa się" pod inną.
3. **Finalne rozwiązanie**: EGiB zostaje jedynym natywnym wpisem
   `L.control.layers` (bez podkategorii, nie trzeba). GESUT i Plany
   zagospodarowania to w pełni własne wiersze (`<div class="layer-group-row">`
   z checkboxem + `<details class="layer-subcategories">`), budowane przez
   `addLayerGroupRow()` i doklejane do `layersControl.getContainer()` w
   kolejności wywołania — każdy rozwija się dokładnie pod swoim checkboxem,
   bez ryzyka nadpisania przez `_update()`.
4. **Synchronizacja nadrzędny↔dzieci**: na życzenie Klaudii nadrzędny
   checkbox teraz włącza/wyłącza WSZYSTKIE podkategorie naraz, a każda
   podkategoria da się też przełączać osobno, z nadrzędnym odzwierciedlającym
   stan dzieci (`input.indeterminate = true` — natywny wygląd przeglądarki,
   myślnik zamiast znaczka — gdy włączona jest tylko część). Wymagało to
   zmiany warstwy nadrzędnej z osobnego zapytania WMS (GESUT) / osobnego
   `L.layerGroup` zbudowanego RAZ przy starcie (Plany zagospodarowania) na
   `L.layerGroup` budowany WEWNĄTRZ `addLayerGroupRow()` z DOKŁADNIE TYCH
   SAMYCH instancji warstw co podkategorie — inaczej odznaczenie jednej
   podkategorii nie miałoby żadnego efektu wizualnego, bo nadrzędna warstwa
   (osobna instancja) zostałaby na mapie bez zmian. Sygnatura funkcji się
   zmieniła: `addLayerGroupRow(container, label, subcategories)` — bez
   parametru `mainLayer`, budowany teraz wewnętrznie jako
   `L.layerGroup(subcategories.map(([, layer]) => layer))`.
   Zweryfikowane w Playwright (4 kroki): klik nadrzędnego zaznacza
   wszystkie dzieci → odznaczenie jednego dziecka daje nadrzędnemu
   `indeterminate=true` i kafelki pozostałych 5 nadal widoczne na mapie →
   ponowne zaznaczenie tego dziecka wraca nadrzędny do w pełni
   zaznaczonego → odznaczenie nadrzędnego odznacza wszystkie dzieci.
- **Pułapka CSS**: reguła `label{display:flex}` bez `:not([open])`
  nadpisuje domyślne ukrywanie zamkniętego `<details>` przez przeglądarkę
  (selektor autorski ma wyższą specyficzność niż domyślna reguła UA) —
  trzeba jawnie `.layer-subcategories:not([open]) label{display:none}`,
  inaczej podkategorie są tylko wizualnie zwinięte, ale nadal renderowane
  (i "widoczne" programowo, np. w testach).
- **Pułapka wysokości**: `#map-wrap` to tylko `40vh`, a Leaflet ma
  `.leaflet-container{overflow:hidden}` — przy rozwinięciu obu grup naraz
  treść kontrolki bywa wyższa niż dostępna przestrzeń i zostaje po cichu
  ucięta (dolne checkboxy niewidoczne i nieklikalne, bez żadnego błędu).
  Naprawione przez `max-height:calc(40vh - 24px); overflow-y:auto;` na
  `.leaflet-control-layers` — kontrolka przewija się sama zamiast tnąć
  zawartość. Zweryfikowane w Playwright: `scrollHeight > clientHeight` przy
  obu grupach rozwiniętych, a ostatni checkbox nadal klikalny po scrollu.
| Hydrologia (cieki, powódź, podtopienia) | ISOK/Wody Polskie WMS + PIG-PIB + Overpass (cieki) | Działa dobrze |
| Pozwolenia na budowę | GUNB/RWDZ — **tylko link-out**, brak API (CAPTCHA) | Nie próbuj scrapować — świadoma decyzja po analizie regulaminu |
| Odległość do drogi gminnej | Overpass API, przybliżenie przez `highway=unclassified/residential` (OSM nie ma pola „kategoria zarządzania drogą") | Działa, ale to przybliżenie — jasno oznaczone w UI |
| Wycena | Statyczna tabela cen GUS per województwo × powierzchnia | Czysto statystyczne, appka to jasno komunikuje |
| Link „Polska mapa" | `mapy.geoportal.gov.pl/imap/?identifyParcel=TERYT` | **Stary viewer** (`/imap/`), NIE nowy (`/imapnext/`) — patrz sekcja 6 |
| Link „Polska.e-mapa.net" | `https://polska.e-mapa.net?identifyParcel=TERYT` | Potwierdzone przez zrzut ekranu użytkowniczki z ich własnej funkcji „udostępnij" |
| Link „Google Maps" | `https://www.google.com/maps?q={lat},{lon}` | Budowany z `centroid` już obliczanego przez appkę |
| Plan ogólny / OUZ | Wzmianki wykrywane w tekście już pobieranym z KIAPP (bez nowego źródła) | Tylko keyword-matching, nie strukturalny parsing — patrz niżej |
| Księga wieczysta | Ogólny link do przeglądarki EKW MS — **tylko link-out**, bez numeru KW | Brak numeru KW w żadnym już używanym źródle (ULDK/EGiB) |

**Plan Ogólny / OUZ — dodane 2026-09-03, po analizie konkurencji (Działkopedia).**
Kontekst: od 1 września 2026 r. (czyli już teraz) decyzję o warunkach
zabudowy dla działki bez MPZP dostaje się TYLKO wtedy, gdy leży w
obszarze uzupełnienia zabudowy (OUZ) wyznaczonym w planie ogólnym gminy —
to zupełnie inna zasada niż wcześniej ("brak planu = można próbować o
WZ"). Konkurencja (dzialkopedia.pl) ma to jako osobną, zdekodowaną daną
per gmina; my NIE mamy strukturalnego dostępu do schematu atrybutów
KIAPP (nie da się go zweryfikować na żywo w tym środowisku — patrz
sekcja 6), więc zamiast zgadywać nazwy pól, `services/zoning.py` robi
**tylko wyszukiwanie słów kluczowych** w tekście, który KIAPP i tak już
zwraca (`_mentions_any` + `_PLAN_OGOLNY_KEYWORDS`/`_OUZ_KEYWORDS`) —
`mentions_plan_ogolny`/`mentions_ouz` w odpowiedzi `found: "yes"`,
wyświetlane jako dodatkowe wskazówki pod tabelą. Fałszywe negatywy
(przeoczenie prawdziwej wzmianki) są akceptowalne, fałszywe pozytywy
(twierdzenie że coś jest planem ogólnym/OUZ, gdy nie jest) — nie, stąd
proste dopasowanie słów, a nie zgadywana struktura.

Ważniejsza zmiana: gdy `found: "no"` (ani KIAPP, ani KIMPZP nic nie
znalazły), `get_zoning()` dołącza teraz pole `note` z wyjaśnieniem
dokładnie tej zasady — to jest miejsce w appce, gdzie ktoś mógłby
wcześniej wyjść z fałszywym przekonaniem "nie ma planu, więc dostanę
WZ". Wyświetlane w `app.js` jako czerwony akapit pod komunikatem o braku
planu. 3 nowe testy pytest (`test_mentions_any_*`,
`test_try_zoning_source_sets_plan_ogolny_and_ouz_flags`,
`test_try_zoning_source_no_flags_for_plain_mpzp`,
`test_get_zoning_attaches_ouz_note_when_no_plan_found_anywhere`).

**Księga wieczysta — dodane 2026-09-03, ten sam powód.** Stan prawny
(dział 02 audytu Działkopedii) był całkowitym brakiem w tej appce.
`services/valuation.py::get_ekw_link()` zwraca ogólny link do
`przegladarka-ekw.ms.gov.pl/eukw_ogl/menu.do` (oficjalna przeglądarka
MS) — **NIE deep-link do konkretnej księgi**, bo żadne już używane
źródło (ULDK, EGiB) nie zwraca numeru KW dla działki; osoba musi
znać/znaleźć swój numer KW samodzielnie (zwykle w akcie notarialnym albo
wypisie z rejestru gruntów). Ten sam wzorzec co istniejący `gunb_link`
(sekcja "Pozwolenia na budowę" — też tylko link-out, bo RWDZ nie ma
otwartego API). Nowa sekcja w `/api/analyze` (`land_registry.ekw_link`)
i nowa karta w `app.js`. **URL NIE zweryfikowany na żywo** (domeny
rządowe zablokowane w tym środowisku) — sprawdź ręcznie, że wciąż
działa, zanim uznasz to za gotowe.

Obie zmiany wynikają z opublikowanego wcześniej raportu-artefaktu
"Rozpoznanie Działkopedii" (analiza funkcjonalności konkurencji +
priorytetowy plan prac P0/P1/P2) — to pozycje z priorytetu P0. Reszta
P0 (realna wycena z transakcji RCN zamiast stałej średniej GUS) **nie
została zrobiona** — wymaga potwierdzenia, czy istnieje jakiekolwiek
publicznie dostępne, niewymagające uwierzytelnienia źródło danych
transakcyjnych RCN/RCiWN (Rejestr Cen i Wartości Nieruchomości), zanim
warto zgadywać nazwę/URL kolejnej usługi KI* — w przeciwieństwie do
KIMPZP/KIAPP/KIUT (nazwy i działanie potwierdzone wcześniej na żywo),
nie mam żadnego potwierdzenia, że taka usługa w ogóle publicznie
istnieje, więc świadomie tego nie zgadywałam. Do ustalenia z Klaudią,
zanim ktoś zacznie to implementować.

### Pamięć podręczna (`services/cache.py`) — dodane 2026-09-04

Klaudia poprosiła o zbadanie możliwości poprawy wydajności, słusznie
podejrzewając, że część danych nie zmienia się codziennie. Opublikowany
został osobny raport-artefakt „Plan Pamięci Podręcznej" (analiza
teoretycznego sufitu czasu odpowiedzi — do 62s przy złożeniu
skonfigurowanych timeoutów — i tabela TTL per usługa), a po dwóch rundach
pytań Klaudii (jak działa TTL — leniwy cache-aside, NIE cykliczny poller,
bo przestrzeń kluczy (`teryt_id`) jest ogromna i długoogonowa, poller
marnowałby pracę na działki, których nikt już nie odwiedzi; i skąd
pewność że dane są aktualne — patrz niżej) Klaudia zatwierdziła konkretną
kolejność wdrożenia, która została zrealizowana w całości w jednym PR:

1. **Logowanie realnego czasu** każdej z 9 gałęzi `asyncio.gather` w
   `/api/analyze` — nowy helper `_timed()` w `main.py`, `logger.info` z
   nazwą usługi i czasem. To jedyny sposób, żeby liczby z raportu (teraz
   to sufity z konfiguracji, nie pomiary) stały się faktami.
2. **`services/cache.py`** — generyczny, leniwy cache-aside na SQLite
   (`get_or_fetch(service, key, ttl_seconds, fetch)`), klucz =
   `teryt_id` (naturalna tożsamość działki, współdzielona między różnymi
   osobami sprawdzającymi tę samą działkę). Cache'uje WYŁĄCZNIE wyniki
   `status: "ok"` — błąd/timeout usługi rządowej nigdy nie zamraża się w
   cache'u na cały TTL. Każdy zwrócony wynik (trafienie i świeże
   pobranie) dostaje `cached` (bool) i `fetched_at` (unix timestamp).
   Brak dysku trwałego na Render na razie (świadomy zakres v1 — plik
   SQLite w efemerycznym systemie plików kontenera resetuje się przy
   każdym deployu, ale nadal pomaga w ciągu jednego dnia/wielu odwiedzin
   — patrz raport, sekcja 5, dla obu opcji).
3. **Podłączone z TTL z tabeli w raporcie** — osuwiska/SOPO, powódź/ISOK,
   podtopienia/PIG-PIB (180 dni — mapy geologiczne/hydrologiczne,
   ustawowe cykle aktualizacji liczone w latach), cieki wodne/OSM (90
   dni), media/GESUT, ewidencja/KIEG, droga gminna/OSM (30 dni), budynki
   /OSM (14 dni — jedyna z "bezpiecznych" usług z realnie krótszym
   TTL, bo nowa zabudowa to jedyna rzecz w tej grupie, która wiarygodnie
   może się zmienić szybciej). Stałe `TTL_*` w `config.py`, ten sam
   wzorzec co istniejące `TIMEOUT_*`.
   **Świadomie NIE podłączone**: plan zagospodarowania (jedyna usługa z
   realnym ryzykiem decyzyjnym — gmina może uchwalić nowy plan w trakcie
   czyjejś decyzji o zakupie) i identyfikacja działki przez ULDK (dana
   „tożsamościowa"). Zostają live do czasu wdrożenia widocznego
   timestampu i przycisku odświeżenia (patrz punkt 4) — dopiero wtedy
   cache dla nich będzie bezpieczny, nie wcześniej.
4. **„Dane z: [data]" w UI** — `dataAgeNote()` w `app.js`, renderowane
   pod każdą sekcją, która ma `fetched_at` w odpowiedzi. To NIE jest
   opcjonalny dodatek: Klaudia wprost zapytała "skąd pewność, że dane są
   aktualne" — odpowiedzią nie jest "zaufaj TTL-owi", tylko "zawsze
   widzisz, kiedy dana sekcja faktycznie została pobrana, i sama
   oceniasz". Przycisk „odśwież teraz" (wymuszenie pominięcia cache'u
   dla jednej sekcji) jest w raporcie jako kolejny krok, ale NIE został
   jeszcze zrobiony w tym PR — bez niego jedyny sposób na wymuszenie
   świeżych danych to poczekać na wygaśnięcie TTL.
5. **`services/zoning.py::get_zoning` — KIAPP i KIMPZP równolegle, nie
   sekwencyjnie** (niezależne od cache'u, ale ta sama runda pracy).
   Wcześniej KIMPZP było próbowane dopiero gdy KIAPP nic nie znalazło
   (sondowanie GetMap do 15s + szczegóły do 12s, RAZ dla każdego z dwóch
   źródeł z rzędu) — to był pojedynczy największy udział w teoretycznym
   suficie czasu odpowiedzi całej appki. Teraz oba źródła pytane
   naraz przez `asyncio.gather`, ta sama logika pierwszeństwa (KIAPP
   wygrywa, gdy ma wynik) zachowana, tylko bez płacenia za to czasem.

**Nadal do zrobienia (świadomie odłożone, nie zapomniane)**: przycisk
„odśwież teraz", cache dla planu zagospodarowania (7 dni, dopiero po
przycisku odświeżenia) i dla identyfikacji ULDK (7 dni), decyzja o dysku
trwałym na Render, progresywne renderowanie wyniku. Wszystko to jest w
pełnej wersji raportu-artefaktu „Plan Pamięci Podręcznej". **Aktualizacja
2026-09-04 (później tego samego dnia)**: cache dla planu zagospodarowania
i progresywne renderowanie wyniku zostały jednak wdrożone (razem z trwałym
`httpx.AsyncClient`, na żądanie Klaudii) — patrz „Trzy optymalizacje
wydajności" niżej. Przycisk „odśwież teraz", cache dla identyfikacji ULDK i
dysk trwały na Render NADAL nie są zrobione.

11 nowych testów pytest dla `services/cache.py` (fikstura `cache_db` z
`tmp_path` + `monkeypatch.setattr(cache, "CACHE_DB_PATH", ...)` +
`cache._reset_for_tests()`) — trafienie, brak w cache'u, wygasły wpis,
błąd nigdy nie cache'owany, niezależność kluczy/usług. Razem 59 testów.
`node --check` i `python3 -c "import main"` na zielono. **Rzeczywiste
przyspieszenie NIE zostało zmierzone na żywo** (te same ograniczenia
sieciowe co zawsze w tym środowisku) — logi z punktu 1 to pierwszy krok
do tego, nie coś, co dało się zweryfikować z tego sandboksa.

### Werdykt, obszary chronione i geologia (items 6, 8, 9 z „Rozpoznania Działkopedii") — dodane 2026-09-04

Klaudia poprosiła o realizację pozycji 6, 8, 9 z raportu-artefaktu
„Rozpoznanie Działkopedii" naraz. Item 6 (syntetyczny werdykt) jest
logicznie zależny od item 8 (obszary chronione jako jeden z sygnałów
werdyktu), więc zanim cokolwiek się podłączyło do `main.py`, GDOŚ i
PIG-PIB zostały zbadane przez subagenta (WebSearch, bo bezpośredni
dostęp do `*.gov.pl` jest w tym środowisku zablokowany identycznie jak
wcześniej) — **żeby nie zgadywać kolejnego URL-a bez podstaw**, tak jak
przy KIMPZP/KIAPP/KIUT. Pełne ustalenia researchu (z cytowaniami) niżej;
kod trafia dopiero po nich.

**Obszary chronione (GDOŚ, item 8) — `services/nature.py::get_protected_areas()`.**
Wysoka pewność researchu: WFS `https://sdi.gdos.gov.pl/wfs` (GeoServer,
workspace `GDOS`), sześć warstw (`typeNames`): `ParkiNarodowe`,
`Rezerwaty`, `ParkiKrajobrazowe`, `ObszaryChronionegoKrajobrazu`,
`ObszarySpecjalnejOchrony` (Natura 2000 ptasi/OSO),
`SpecjalneObszaryOchrony` (Natura 2000 siedliskowy/SOO) — potwierdzone
przez kilka niezależnych open-source'owych projektów, które już z tego
serwisu korzystają (nie z własnego GetCapabilities GDOŚ, do którego nie
ma dostępu z tego środowiska). Stary adres (`wms.gdos.gov.pl/geoserver/wms`)
jest wycofany — świadomie NIE użyty.

Realna niepewność, którą kod obsługuje empirycznie zamiast zakładać:
GeoServer czasem ignoruje `srsName` przy `outputFormat=application/json`
i zwraca współrzędne WGS84 zamiast żądanego EPSG:2180 — dokładnie ten
sam rodzaj niepewności, który `wfs_search.enumerate_parcel_points_in_area`
już rozwiązuje dla innego przypadku (kolejność osi). Tutaj: pierwsza
zwrócona współrzędna jest sprawdzana po wielkości liczb (czy wygląda jak
długość/szerokość geograficzna Polski, czy jak EPSG:2180) i punkt
zapytania jest transformowany odpowiednio — zamiast zakładać jedno
zachowanie serwera. GetFeatureInfo NIE jest potwierdzone (oficjalna
przeglądarka Geoserwis używa własnego, nieudokumentowanego proxy zamiast
zwykłego WMS GetFeatureInfo) — użyte jest WFS GetFeature zamiast tego,
co i tak jest lepsze (prawdziwa geometria do testu przecięcia z punktem,
nie tylko trafienie w piksel).

**Tereny górnicze (PIG-PIB MIDAS, item 9) — `services/geology.py::check_mining_areas()`.**
Ten sam host i wzorzec ArcGIS REST `identify` co już potwierdzone na
żywo SOPO/podtopienia (`cbdgmapa.pgi.gov.pl`), tylko `midas/MapServer`
zamiast `geozagrozenia/sopo_obszary`. W przeciwieństwie do SOPO (warstwy
potwierdzone przez `?f=json`), URL i istnienie tej usługi na tym hoście
są potwierdzone tylko pośrednio (cytaty niżej), nie bezpośrednim
sprawdzeniem. Nazwa terenu/obszaru górniczego wyciągana z pola `value` w
odpowiedzi `identify` — to pole protokołu ArcGIS REST (podstawowa
wartość wyświetlana obiektu), nie zgadywana nazwa atrybutu usługi, więc
powinno działać niezależnie od wewnętrznego schematu MIDAS.

**Hałas — świadomie NIE zintegrowane.** Research jednoznacznie: w Polsce
nie ma jednej krajowej usługi WMS/API dla map akustycznych. GIOŚ
agreguje dane do raportowania unijnego, ale niczego nie publikuje jako
jedną usługę — realne mapy hałasu publikują osobno GDDKiA (drogi
krajowe), PKP PLK (kolej), lotniska i każde miasto powyżej 100 tys.
mieszkańców, każde na własnym portalu z własnym schematem. Dla typowej
wiejskiej/małomiasteczkowej działki (czyli większości użytkowniczek tej
appki) odpowiedź byłaby prawie zawsze "brak danych", co czytałoby się
fałszywie jako "brak hałasu", a nie "nie sprawdzono". Zamiast integracji
appka pokazuje statyczną notatkę w karcie "Obszary chronione i
geologia" z sugestią sprawdzenia mapy właściwego miasta osobno.
Rozważane, ale odrzucone jako zbyt spekulatywne bez weryfikacji na
żywo: MGśP (mapa geośrodowiskowa — brak potwierdzonego endpointu),
podatność gleb na suszę (IUNG/GUGiK — endpoint nieznaleziony), erozja
(brak usługi, tylko opracowania PDF), spadki terenu przez NMT GUGiK
(`services.gugik.gov.pl/nmt/` — istnienie potwierdzone, ale dokładna
składnia zapytania nieznana, niewarta zgadywania).

**Syntetyczny werdykt (item 6) — `services/verdict.py::build_verdict()`,
teraz podłączony w `main.py`.** Czysta, deterministyczna funkcja
punktowa (start 100, odejmowanie za każdy sygnał ryzyka, każde odjęcie
nazwane w `flags`, nigdy czarna skrzynka): 40 pkt za osuwisko, 35 za
strefę zalewową, 15 za ryzyko podtopień, 10 za obszar chroniony, 10 za
brak planu miejscowego, 20 za brak drogi publicznej w pobliżu (5 za
tylko drogę wyższej kategorii), 15 za brak wykrytych mediów (5 przy
1-2 typach). Sekcja, która nie odpowiedziała (`status != "ok"`), NIGDY
nie obniża wyniku — trafia do osobnej listy `incomplete_sections`, żeby
"nie wiemy" nigdy nie wyglądało jak "wszystko OK". Wynik 0-100 mapowany
na 3 poziomy: `dobra` (≥80), `do_sprawdzenia` (50-79),
`wysokie_ryzyko` (<50). Karta werdyktu na samej górze wyniku w
`app.js`/`index.html`, z kolorami per poziom (nowe tokeny CSS
`--warn`/`--warn-bg` obok istniejących `--ok`/`--danger`).

**Cache**: `protected_areas` i `mining_areas` podłączone do
`services/cache.py` z TTL 180 dni (`TTL_PROTECTED_AREAS`,
`TTL_MINING_AREAS`) — ten sam poziom pewności co inne dane
geologiczne/administracyjne o wieloletnim cyklu aktualizacji (patrz
tabela w artefakcie „Plan Pamięci Podręcznej").

18 nowych testów pytest (76 razem): parsowanie GeoJSON i test
przecięcia z punktem w `nature.py` (w tym dedykowany test na
wykrywanie-i-transformację WGS84 z prawdziwym `pyproj.Transformer` —
nie mockiem), ekstrakcja pola `value` w `geology.py`, pełne pokrycie
reguł punktowych w `verdict.py`. **Żadna z trzech nowych usług (GDOŚ
WFS, MIDAS, i sam fakt, że werdykt sensownie się liczy na prawdziwych
danych) nie została zweryfikowana na żywo** — domeny rządowe
zablokowane w tym środowisku identycznie jak zawsze. Jeśli po
wdrożeniu na produkcję `protected_areas`/`mining_areas` zawsze wracają
`status: "error"`, to pierwsze miejsce do sprawdzenia — być może URL
albo nazwy warstw wymagają korekty na podstawie realnej odpowiedzi
serwera (message z wyjątku powinien to pokazać).

**Źródła researchu GDOŚ/MIDAS** (dla przyszłej weryfikacji na żywo):
GEOBID „Nowy adres usługi WMS dla danych GDOŚ", GDOŚ „Dostęp do danych
geoprzestrzennych" (gov.pl), rekordy metadanych na bankdanych.gdos.gov.pl,
oraz cztery niezależne projekty open-source na GitHubie, które już
odpytują `sdi.gdos.gov.pl/wfs` z tymi samymi nazwami warstw:
`majkrzak/sp-sota-maps` (`lib/src/sota/park.py`, 9 typeNames naraz —
stąd pewność, że multi-typeName GetFeature działa),
`agsti/highline_scout`, `Eksploracja/MapoTero`, `mpraz/gpx-tracks`. Dla
MIDAS: `INSPIRE-MIF/mr-tools` (logi INSPIRE Monitoring & Reporting
sprawdzające dostępność usług PIG-PIB) i katalog usług PIG-PIB
(geoportal.pgi.gov.pl/portal/page/portal/uslugi_gis — nie sprawdzony
bezpośrednio z tego środowiska, warto zajrzeć ręcznie przy okazji).

### Wynik pogrupowany w sekcje — dodane 2026-09-04

Klaudia zauważyła, że appka nie prezentuje wyniku tak "ładnie" jak
Działkopedia, i poprosiła o pierwszą rundę poprawek wizualnych (świadomie
NIE pełny redesign od podstaw — to była jej decyzja po przedstawieniu
dwóch opcji: szybki przegląd vs. pełny redesign, wybrała szybki przegląd
jako pierwszy krok).

Diagnoza: appka rosła całą sesję funkcja po funkcji — każda nowa sekcja
(werdykt, obszary chronione, księga wieczysta...) była dokładana jako
kolejna karta `<div class="card muted">` na końcu tej samej, coraz
dłuższej kolumny. Efekt: ~10 wizualnie identycznych kart bez żadnej
hierarchii poza kolorem ryzyka.

Zmiana w `app.js::renderResults()` (bez zmiany logiki poszczególnych
kart — każda karta budowana dokładnie tak jak wcześniej, tylko zebrana
do zmiennej zamiast bezpośrednio dopisywana do `html`) + nowe klasy CSS
w `index.html`:

- **Link row** — 3 przyciski (e-mapa, Geoportal, Google Maps), wcześniej
  osobno w pełnej szerokości jeden pod drugim, teraz jeden rząd trzech
  kompaktowych przycisków (`.link-row`).
- **4 grupy sekcji** w `<details open class="section-group">` (domyślnie
  ROZWINIĘTE — świadomie, żeby nic nie było ukryte przy pierwszym
  wejściu, ale zwijalne dla kogoś, kto chce szybko przeskanować wynik):
  „Plany, stan prawny i ewidencja" (plan zagospodarowania, księga
  wieczysta, ewidencja+budynki), „Ryzyka środowiskowe" (osuwiska,
  hydrologia, obszary chronione i geologia), „Media i dostęp do drogi"
  (GESUT, droga gminna), „Wycena i pozwolenia" (wycena, GUNB). Nagłówek
  grupy wizualnie cięższy niż nagłówek karty (podkreślenie kolorem
  `--survey`), żeby dało się od razu odróżnić poziom hierarchii —
  dokładnie ten sam mechanizm mentalny co "8 obszarów audytu" u
  konkurencji, tylko dopasowany do tego, co appka faktycznie ma.
- Werdykt i teryt-echo zostają NAD grupami, zawsze widoczne, bez
  zwijania — to jest "so what" wyniku, nie szczegół do przejrzenia.

**Zweryfikowane wizualnie, nie tylko składniowo** — pierwszy raz w tej
sesji faktyczny zrzut ekranu, nie tylko `node --check`/pytest. Playwright
(Node, `npx playwright install chromium` — pobrało się, `registry.npmjs.org`
jest w noProxy tego środowiska) z lokalnym serwerem statycznym,
przechwyconym Leaflet z lokalnego `npm install leaflet` (ten sam wzorzec
co opisany wcześniej w tym dokumencie), zamockowanym `/api/resolve` i
`/api/analyze` (realistyczny payload z każdą sekcją, w tym stanem błędu
dla `protected_areas`, żeby sprawdzić że błąd też wygląda dobrze w nowym
układzie). Zero błędów JS w konsoli (poza oczekiwanymi błędami sieciowymi
kafelków mapy, nie zamockowanych). Zrzut ekranu potwierdził: hierarchia
czytelna, `dataAgeNote()` działa pod każdą kartą, kolory stanu (ok/warn/
danger) zachowane wewnątrz grup, długi wynik nie sprawia już wrażenia
"ściany identycznych kart".

Cache-bust: `app.js?v=24`, service worker `v16`.

**Nie zrobione w tej rundzie (świadomie, to nie był zakres "szybkiego
przeglądu")**: pełna zmiana typografii/palety, ikony przy nagłówkach
sekcji, progresywne renderowanie (patrz P2 w artefakcie "Plan Pamięci
Podręcznej" — to osobna, większa zmiana architektoniczna). Jeśli Klaudia
zechce iść dalej niż ten pierwszy krok, to naturalne miejsce na pełny
redesign wizualny jako osobną decyzję.

### Lista statusów (checklist) + lista kroków przed zakupem — dodane 2026-09-04

Po poprzedniej rundzie (grupowanie w sekcje) Klaudia przesłała 4 realne
zrzuty ekranu darmowej oceny Działkopedii ORAZ pełny PDF tej oceny (nie
płatnego Audytu — bezpłatny „Ocena działki", ale ze szczegółową treścią,
nie tylko UI). To dało pierwszy raz w tej analizie konkurencji prawdziwą,
nie zgadywaną treść (poprzedni raport-artefakt „Rozpoznanie Działkopedii"
opierał się wyłącznie na WebSearch, bo bezpośredni dostęp jest
zablokowany w tym środowisku).

**Kluczowe odkrycie z PDF-a**: ich cały wynik to zwarta lista ~14 wierszy
(etykieta · pill RYZYKO/UWAGA/OK · jedno zdanie), z licznikiem na górze
(np. „4 do sprawdzenia · 9 bez zastrzeżeń · 1 ryzyko") — dokładnie to, co
Klaudia miała na myśli mówiąc "nie pokazuje tak ładnie". To nie wymagało
ŻADNYCH nowych danych — appka miała już prawie wszystkie te sygnały,
tylko rozbite na osobne karty zamiast jednej listy.

**`services/verdict.py::build_verdict()` przebudowany** (dodane
`mining_areas` jako nowy parametr, WYMAGANY — main.py zaktualizowany):
zamiast zwracać tylko `flags` (lista TYLKO problemów), teraz zwraca
`rows` — pełną listę WSZYSTKICH sprawdzonych sygnałów, łącznie z tymi
"OK" (dokładnie jak w PDF-ie Działkopedii) — plus `counts`
(`{"risk":n,"warning":n,"ok":n}`). Każdy sygnał interpretowany RAZ (nowy
helper `add_row()`) — ta sama interpretacja decyduje i o tym, jaki wiersz
się pokazuje, i o tym, ile punktów odjąć, więc te dwie rzeczy nie mogą
się rozjechać (wcześniej `flag()` robił tylko to drugie, dla samych
problemów). Wagi punktowe zachowane z poprzedniej wersji (osuwisko=40,
powódź=35, drogi brak=20, podtopienia/media-brak=15, przyroda/plan/
kopalnia=10, drogi-fallback=5) — jawnie przekazywane jako `points=`,
NIE spłaszczone do dwóch poziomów, żeby nie stracić wcześniej
przemyślanego różnicowania wagi w obrębie tej samej kategorii "warning".
Stary klucz `flags` USUNIĘTY z odpowiedzi (nic poza tym appką go nie
konsumowało) — 9 testów pytest zaktualizowanych pod nowy kształt.

**Nowy `services/due_diligence.py::build_due_diligence_checklist()`** —
25-punktowa lista kroków przed zakupem w 7 kategoriach (stan prawny,
planowanie przestrzenne, bezpieczeństwo, infrastruktura, koszty zakupu,
wycena, teren i otoczenie) — to jest ostatnia strona PDF-u Działkopedii.
Kategorie to standardowa, generyczna wiedza branżowa o due diligence
działki (każdy poradnik zakupu działki wymienia te same punkty), NIE
skopiowana treść — appka po prostu odhacza, które z tych 25 kroków
faktycznie już sama sprawdziła (`covered: set[str]` budowany w
`main.py` z tego, które sekcje zwróciły `status: "ok"`). Czysto
prezentacyjne — zero nowych źródeł danych. 4 nowe testy pytest.
Ciekawostka: appka niezależnie wychodzi na "8 z 25" auto-sprawdzonych —
dokładnie tyle samo, ile pokazuje PDF Działkopedii, mimo że liczone
zupełnie inną logiką — zbieżność, nie kopiowanie.

**Frontend** (`app.js`/`index.html`): werdykt (`.verdict-card`) zostaje
(wynik 0-100 + poziom), ale usunięta stara lista `<ul class="verdict-
flags">` (tylko problemy) — zastąpiona przez `.checklist-counts` (3
kafelki risk/warning/ok) + `.check-rows` (pełna lista, posortowana
risk→warning→ok, każdy wiersz: etykieta + kolorowy pill + tekst).
Osobna nowa sekcja `<details open>` „Lista kroków przed zakupem" z
`.dd-category`/`.dd-item` (checkboxy `disabled`, `checked` gdy
`auto_checked`).

**Zweryfikowane wizualnie** (Playwright, ten sam wzorzec co poprzednia
runda) — zrzut ekranu potwierdził czytelny układ, poprawne sortowanie
wierszy, działające liczniki, checkboxy w liście kroków renderujące się
poprawnie. Zero błędów JS.

Cache-bust: `app.js?v=25`, service worker `v17`.

**Zbadane z PDF-a, ale NIE zrobione w tej rundzie** — nowe kategorie
danych, których appka nie ma wcale (azbest, jakość powietrza GIOŚ,
osiadanie terenu Copernicus EGMS, ryzyko Seveso, zasięg sieci UKE, ceny
z rzeczywistych transakcji RCN — ich PDF pokazuje medianę z 361
transakcji, co jest zaskakujące, bo wcześniejszy research nie znalazł
potwierdzonego publicznego API RCN; warto zbadać ponownie, może się
mylili co do dostępności, albo Działkopedia ma nietypowy dostęp).
Klaudia zaakceptowała plan: najpierw ta runda (prezentacja, zero nowych
usług), następnie zbadanie GIOŚ (jakość powietrza) jako najbardziej
obiecującej kolejnej integracji — to jeszcze nie zostało zrobione, patrz
koniec tego dokumentu / rozmowa z Klaudią o kolejnych krokach.

### Jakość powietrza — GIOŚ (item 3 z planu po Działkopedii) — dodane 2026-09-04

Trzeci i ostatni punkt zaakceptowanego planu: po prezentacji (checklist +
lista kroków, bez nowych danych) — pierwsza faktycznie NOWA kategoria
danych od czasu poprzedniej rundy usług (osuwiska/powódź/podtopienia/
przyroda/kopalnie). Źródło: GIOŚ (Główny Inspektorat Ochrony Środowiska),
publiczne REST API `api.gios.gov.pl/pjp-api/v1/rest`, bez klucza API.

**Wiarygodność wyraźnie wyższa niż większość innych nowych integracji w
tym projekcie**: URL bazowy i kształt JSON-a zostały potwierdzone przez
kilkanaście niezależnych projektów open-source, które przechwyciły
prawdziwe odpowiedzi tego API (nie tylko wygląda prawdopodobnie — ma
realne, powtarzające się fixture'y). Mimo to NIE zweryfikowane na żywo z
tej appki — domeny rządowe są zablokowane w tym środowisku (piaskownica).

**Trzy pułapki, które ten research znalazł i którym implementacja
świadomie zapobiega:**
1. **Stary, nie-wersjonowany URL (`pjp-api/rest/`, bez `/v1/`) jest
   martwy** — wycofany 30.06.2025, zwraca teraz HTTP 410 Gone. Każda
   implementacja ściągnięta ze starszego poradnika/przykładu na to
   wpadnie. `config.py::GIOS_API_URL` używa jawnie `/v1/`.
2. **Nie istnieje endpoint „najbliższa stacja"** — API ma tylko listę
   WSZYSTKICH stacji (`station/findAll`, ~288 stacji, jedna strona przy
   `size=500`, ale kod i tak paginuje po `totalPages` zamiast zakładać).
   `air_quality.py` pobiera całą listę RAZ (cache pod stałym kluczem
   `"all"`, nie per-działka — to dane takie same dla całej Polski) i sam
   sortuje po odległości (`geod.inv`, ten sam wzorzec co `wfs_search.py`).
3. **~42% polskich stacji jest manualnych** (pomiary laboratoryjne, bez
   danych na bieżąco — `data/getData` po prostu nic nie zwraca) —
   implementacja próbuje do 5 najbliższych kandydatów po kolei, cicho
   pomijając te bez działającego czujnika PM2.5/PM10 lub bez świeżego
   odczytu, zamiast ufać samej najbliższej stacji. Dodatkowo najnowszy
   wiersz (czasem dwa) w `data/getData` często ma `"Wartość": null`
   (pomiar jeszcze niesfinalizowany) — kod skanuje w przód do pierwszej
   niepustej wartości zamiast ufać indeksowi 0.

**Świadoma decyzja zakresu**: appka pokazuje surowy odczyt (stężenie +
jednostka + stacja + odległość + czas pomiaru), BEZ własnej oceny ryzyka
zdrowotnego — nie ma logiki progów WHO/UE. Dokładnie jak konkurencja
(Działkopedia też tylko pokazuje liczbę, bez interpretacji). Dlatego w
`verdict.py` to jedyny sygnał zawsze w tierze `"ok"` — informacyjny, nigdy
nie odejmuje punktów.

**TTL świadomie inny niż reszta appki**: `TTL_AIR_QUALITY = 3600` (1h) —
jedyny wyjątek od filozofii „cachuj agresywnie" (30-180 dni dla reszty
usług) przyjętej wcześniej w projekcie, bo odczyty faktycznie aktualizują
się co godzinę i dłuższy cache pokazywałby nieaktualne dane. Lista stacji
(rzadko się zmienia) ma osobny, długi TTL: `TTL_AIR_QUALITY_STATIONS = 30
dni`.

**ToS GIOŚ wymaga widocznej atrybucji źródła** — appka pokazuje
`"Źródło danych: GIOŚ — EKOINFONET"` w karcie wyniku.

**Pliki**: nowy `services/air_quality.py` (paginacja `station/findAll`,
`_nearest_stations` z cache, `_find_pollutant_sensor` z fallbackiem PM2.5
→ PM10, `_latest_value` ze skanowaniem null-i). `verdict.py::build_verdict()`
przyjmuje teraz `air_quality` jako 9. parametr (WYMAGANY). `due_diligence.py`
— pozycja „Sprawdź jakość powietrza" pokryta kluczem `"air_quality"`.
`main.py` — nowa gałąź w `asyncio.gather()`, cache pod kluczem per-działka
(`teryt_id`, TTL 1h), nowy klucz `"air_quality"` w odpowiedzi `/api/analyze`.
Frontend — nowa karta „Jakość powietrza" w grupie sekcji „Ryzyka
środowiskowe” (`app.js`), z atrybucją i zastrzeżeniem o braku oceny
ryzyka. Cache-bust: `app.js?v=26`, service worker `v18`.

**Testy**: 8 nowych testów pytest dla `air_quality.py` — sukces PM2.5,
fallback na PM10, skanowanie po `null`, pominięcie stacji manualnej (brak
czujnika), pominięcie stacji bez świeżego odczytu, wyczerpanie
kandydatów, brak stacji w bazie, błąd pobrania listy stacji. Wymagały
nowego fake-klienta HTTP (`_FakeAirQualityClient`) routującego po ścieżce
URL, bo `get_air_quality` robi 3 różne typy zapytań sekwencyjnie (lista
stacji → czujniki stacji → dane czujnika) — istniejące fake'y w tym pliku
testowym zwracały tylko jedną, stałą odpowiedź na klienta. `_clean_signals()`
i test `..._all_clean_scores_100_dobra` zaktualizowane pod nowy wymagany
parametr (`counts.ok` 8→9). Zweryfikowane wizualnie (Playwright, ten sam
skrypt/wzorzec co poprzednie 2 rundy) — karta renderuje się poprawnie w
sekcji „Ryzyka środowiskowe", zero błędów JS.

To zamyka 3-punktowy plan zaakceptowany przez Klaudię po analizie
Działkopedii. Pozostałe zbadane-ale-nie-zrobione kategorie danych z tego
researchu (azbest, Copernicus EGMS, Seveso, UKE, RCN) patrz punkt wyżej —
wciąż otwarte, nie rozpoczynać bez wyraźnej prośby Klaudii.

### Cztery poprawki błędów znalezionych przy przeglądzie kodu — dodane 2026-09-04

Po zamknięciu planu Działkopedii Klaudia poprosiła o dalsze sensowne
poprawki wedle własnego uznania. Uruchomiony pełny przegląd kodu
(`services/`, `main.py`, `config.py`, `geo_utils.py`, `http_utils.py`) —
znalazł 4 realne błędy logiczne, wszystkie naprawione, każdy z nowym
testem pytest (96 testów łącznie, było 89):

1. **`services/zoning.py::get_zoning()` — błąd KIAPP maskował udany wynik
   KIMPZP.** `asyncio.gather` odpytuje oba źródła naraz; `if kiapp_result
   is not None: return kiapp_result` traktował TAKŻE słownik błędu (`{"status":
   "error", ...}`) jako "KIAPP ma wynik, użyj go" — więc przejściowy błąd
   sieciowy KIAPP potrafił nadpisać realne dane MPZP, które KIMPZP w tym
   samym momencie poprawnie znalazł. To bezpośrednio wynikło z dzisiejszej
   analizy działki testowej Zawoja (błąd niepowiązany z tym, co tam się
   faktycznie stało, ale znaleziony przy okazji czytania tego samego
   pliku). **Naprawa**: `_has_real_data()` sprawdza `status != "error"`,
   nie tylko `is not None` — błąd jest zwracany dopiero gdy ŻADNE źródło
   nie ma realnych danych, a nawet wtedy woli pokazać "usługa niedostępna"
   niż fałszywie pewną notatkę "brak planu". 2 nowe testy.
2. **`services/air_quality.py::get_air_quality()` — porzucał najbliższą
   stację na samym niepowodzeniu PM2.5**, nie próbując PM10 na TEJ SAMEJ
   stacji, mimo że moduł deklarował w docstringu dokładnie odwrotne
   zachowanie. `_find_pollutant_sensor()` (liczba pojedyncza) zwracał
   tylko PIERWSZY pasujący czujnik; jeśli ten czujnik akurat nie miał
   świeżego odczytu, kod przechodził od razu do DALSZEJ stacji zamiast
   spróbować PM10 na obecnej. **Naprawa**: nowy `_find_pollutant_sensors()`
   (liczba mnoga) zwraca WSZYSTKIE dostępne czujniki stacji w kolejności
   preferencji, pętla w `get_air_quality()` próbuje ich po kolei zanim
   przejdzie do następnej stacji. 1 nowy test (stacja z PM2.5 bez odczytu
   + PM10 z odczytem na TEJ SAMEJ stacji musi zwrócić tę stację, nie
   dalszą).
3. **`http_utils.py::_get_with_retry()` — nie ponawiał przy 5xx.** Retry
   łapał tylko `httpx.TimeoutException`/`TransportError`, więc
   przeciążony serwer WFS powiatu zwracający 503/500 (realny,
   udokumentowany w HANDOFF.md tryb awarii dla ~380 niezależnych
   serwerów) failował od razu, tracąc jedyny retry, po który ten helper
   istnieje. **Naprawa**: dodano `except httpx.HTTPStatusError` — retry
   przy kodzie ≥500, natychmiastowe `raise` przy 4xx (to prawdziwa,
   trwała odpowiedź o TYM zapytaniu, nie coś do ponawiania). 4 nowe testy.
4. **`services/cache.py::get_or_fetch()` — synchroniczne SQLite blokowało
   pętlę zdarzeń.** `main.py` odpytuje cache współbieżnie przez
   `asyncio.gather` (~9 wywołań na jeden `/api/analyze`), ale
   `conn.execute()`/`conn.commit()` był wywoływany bezpośrednio w
   korutynie — blokujące wywołanie na wątku pętli zdarzeń serializuje
   współbieżne requesty zamiast pozwolić im się przeplatać podczas
   oczekiwania na I/O, czyli dokładnie odwrotność tego, po co jest
   `asyncio.gather`. **Naprawa**: odczyt (`_read_row`) i zapis
   (`_write_row`) wydzielone do osobnych funkcji, wywoływane przez
   `asyncio.to_thread()`. Zachowanie identyczne (istniejące testy cache'u
   przechodzą bez zmian), tylko już nie na wątku event loopa.

**Świadomie odłożone** (znalezione w tym samym przeglądzie, ale
nie naprawione w tej rundzie): `services/geocoding.py` ma trzy niemal
identyczne funkcje geokodujące (`geocode_powiat_gmina_points`,
`geocode_powiat_gmina_prefixes`, `geocode_gmina_candidates`), które już
raz się rozjechały (trzecia ma dodatkowe pola, których dwie pierwsze nie
mają) — realna okazja do uproszczenia, ale to refaktor dotykający
ostrożnie dostrojonej logiki geokodowania, nie prosta poprawka błędu;
wymaga osobnej, uważniejszej rundy, nie warto łączyć z poprawkami błędów
powyżej.

### Zbadane: status planu ogólnego jako osobny sygnał — ODŁOŻONE do 30.09/30.11.2026

Po poprawce notatki testowej (Zawoja: MPZP jest, plan ogólny to osobny,
nieuchwalony akt) Klaudia zapytała, czy MPZP i plan ogólny nie powinny
być ocieniane osobno, zamiast dzisiejszego jednego `zoning.py::get_zoning()`
zwracającego wspólne `found: tak/nie` z KIAPP (Rejestr Urbanistyczny) LUB
KIMPZP (stare MPZP), którykolwiek coś znajdzie pierwszy. Zbadane
(WebSearch — `WebFetch` jest w tym środowisku całkowicie zablokowany, na
KAŻDĄ domenę, nie tylko gov.pl — sprawdzone nawet na Wikipedii):

- **Status aktu to realny, udokumentowany atrybut** obiektu
  `app:AktPlanowaniaPrzestrzennego` (schemat `planowaniePrzestrzenne.xsd`),
  z cyklem `projekt → w uzgodnieniu → uchwalony → obowiązujący`,
  publikowany przez ten sam agregator, którego już używamy
  (`KrajowaIntegracjaAktowPlanowaniaPrzestrzennego`, WMS/WFS/CSW).
- **Ale**: oficjalny harmonogram wdrożenia Rejestru Urbanistycznego wprost
  mówi, że **do 30 września 2026 projekty aktów planowania przestrzennego
  (w tym plany ogólne w trakcie procedury) nadal są publikowane wyłącznie
  w BIP każdej gminy z osobna**, nie w krajowym rejestrze. Czyli dopóki
  status to „projekt" (dokładnie przypadek Zawoi), nasz istniejący
  KIAPP prawdopodobnie nic nie zwróci — nie z powodu błędu, tylko dlatego,
  że te dane świadomie zostały tam zostawione na czas przejściowy. Nawet
  Działkopedia w swoim PDF-ie pisze „nie udało się potwierdzić" — czyli
  oni też nie mają tego ze strukturalnego źródła.
- Scraping ~2477 osobnych stron BIP gmin (bez wspólnego formatu) to
  dokładnie ten rodzaj rozwiązania, którego świadomie unikamy w tym
  projekcie — niestabilne, drogie w utrzymaniu, żadnej gwarancji formatu.

**Decyzja (Klaudia, 2026-09-04)**: zostawić na razie, nie implementować.
**Wrócić do tematu po 30.09.2026** (koniec okresu przejściowego BIP) albo
**po 30.11.2026** (deklarowana pełna kompletność rejestru) — wtedy ten sam
endpoint KIAPP, którego już używamy, może zacząć zwracać status
strukturalnie, bez żadnej nowej integracji z naszej strony. Do tego czasu
`zoning.py` zostaje bez zmian (jeden łączny `found`, KIAPP/KIMPZP
równoważne źródła tego samego pytania „czy jest jakiś plan").

### Wynik: zwarta lista jako spis treści, nie druga kopia treści — dodane 2026-09-04

Klaudia zauważyła, że lista statusów pod oceną (dodana w poprzedniej
rundzie, wzorowana na Działkopedii) i karty szczegółów niżej w dużej
mierze powtarzają to samo zdanie dwa razy — i że bardziej podobają jej
się karty szczegółów. Pokazany był podgląd (Playwright) przed wdrożeniem,
zaakceptowany bez zmian.

**Zmiana**: `.check-rows` (lista pod `.checklist-counts`) już NIE pokazuje
zdania (`r.text` — usunięte razem z CSS `.check-text`) — zostaje tylko
etykieta + kolorowy pill, czyli zwarty, skanowalny **spis treści**, nie
streszczenie. Każdy wiersz to teraz `<a href="#card-{key}">` — klik
przewija (`scroll-behavior:smooth` na `#panel`) do właściwej karty
szczegółów niżej. Mapowanie klucz→kotwica (`rowAnchor` w `app.js`)
uwzględnia, że kilka kluczy werdyktu dzieli jedną kartę (`flood_zone` +
`waterlogging` → karta Hydrologia; `protected_areas` + `mining_areas` →
karta Obszary chronione), więc dodane id (`id="card-{key}"`) tylko na
KIEROWCZYCH kluczach tych kart, nie duplikowane. `cardHTML()` (helper dla
kart "tylko-błąd") dostał nowy opcjonalny parametr `id`.

**Zweryfikowane funkcjonalnie, nie tylko wizualnie**: osobny skrypt
Playwright klika w wiersz "Powietrze" i sprawdza realny `boundingBox()`
karty `#card-air_quality` względem `#panel` po scrollu — potwierdzone, że
karta faktycznie ląduje w widocznym obszarze, nie tylko że link istnieje
w HTML-u. Zero błędów JS.

Cache-bust: `app.js?v=27`, service worker `v19`.

### Dwa realne błędy zgłoszone przez Klaudię na żywej działce testowej — dodane 2026-09-04

Klaudia sprawdziła działkę testową (Zawoja) na żywej appce zaraz po
powyższej zmianie i napisała "wygląda jakby niektóre elementy przestały
działać np. media" + zrzut ekranu. Diagnoza (bez dostępu do usług
rządowych z tego środowiska — same domeny gov.pl są zablokowane) wykazała
DWIE osobne, prawdziwe, ale niezwiązane z dzisiejszą zmianą UI usterki:

1. **Puste komunikaty błędów** (`"Usługa ... niedostępna: "` z niczym po
   dwukropku, widoczne dla sekcji "Odległość do drogi gminnej"). Przyczyna:
   kilka wyjątków `httpx` (np. `ConnectTimeout`, `ReadTimeout`) rzuconych
   bez jawnej wiadomości ma PUSTY `str()` — `f"...: {exc}"` cicho gubił
   jedyną informację, o co chodziło. Ten sam wzorzec (`{exc}` wprost w
   f-stringu) występował w **~15 miejscach w 8 plikach** (wszystkie usługi
   zewnętrzne). **Naprawa**: nowy `http_utils.describe_exc(exc)` —
   `str(exc) or type(exc).__name__` (fallback do nazwy klasy wyjątku, gdy
   treść jest pusta) — podmieniony wszędzie, gdzie `{exc}` trafiał
   bezpośrednio do komunikatu błędu. 3 nowe testy.
2. **`services/utilities.py::check_utilities()` — całkowita awaria usługi
   KIUT wyglądała identycznie jak "sprawdzone, naprawdę brak mediów".**
   Każda z 6 warstw (`one()`) łapie swój WŁASNY wyjątek i cicho zwraca
   `present: False` (celowo — żeby jeden padły typ medium nie ukrywał
   wyników pozostałych pięciu) — ale to oznacza, że gdy WSZYSTKIE 6 warstw
   padnie naraz (np. przejściowa awaria KIUT), funkcja i tak zwracała
   `status: "ok"` z samymi `present: False`. To miało DWA realne skutki:
   frontend pokazywał siatkę kafelków "wszystko szare" zamiast komunikatu
   błędu (bo `app.js` w ogóle nie sprawdzał flagi `error` per-warstwa —
   ona była w danych, po prostu nieużywana), A `verdict.py` odejmowało 15
   pkt i pisało "Nie wykryto żadnych mediów w pobliżu działki (GESUT)" —
   fałszywie pewny sygnał ryzyka zamiast "nie wiemy". **Naprawa**:
   `check_utilities()` zwraca teraz `status: "error"` (trafia do
   `incomplete_sections`, nie punktowane), gdy WSZYSTKIE warstwy zawiodły.
   Częściowa awaria (niektóre warstwy OK, inne nie) zostaje statusem "ok",
   ale `app.js` dostał nowy stan kafelka `.chip.unknown` (bursztynowy,
   `title` z wyjaśnieniem) dla warstw z `error: true` — odróżnialny
   wizualnie od "sprawdzone, brak" (szary) i "sprawdzone, jest" (zielony).
   4 nowe testy (fake klient renderujący prawdziwe obrazy PNG przez PIL,
   routowany po nazwie warstwy — pełny sukces, brak poniżej progu pikseli,
   całkowita awaria wszystkich 6, częściowa awaria z flagą `error` na
   pojedynczym kaflu). Zweryfikowane wizualnie w Playwright.

**Ważna lekcja**: to, co Klaudia zobaczyła jako "coś się zepsuło po
zmianie UI", było w rzeczywistości dwoma NIEZALEŻNYMI, przedistniejącymi
błędami w warstwie danych, ujawnionymi przez przypadek dokładnie w tym
momencie (prawdopodobnie chwilowa niedostępność Overpass/KIUT podczas
sprawdzania). Same zmiany UI (`.check-row-link`, `id="card-*"`) były
przetestowane i poprawne — ale skoro Klaudia już patrzyła na żywy wynik,
warto było przy okazji sprawdzić rzeczy, których nie dało się złapać w
tym środowisku (sandbox nie ma dostępu do żadnej z tych usług na żywo).

Cache-bust: `app.js?v=28`, service worker `v20`.

### Kolejne dwie usterki z żywego testu — "The string did not match" + droga gminna "czasem nie działa" — dodane 2026-09-04

Klaudia od razu po powyższej naprawie zgłosiła (ze zrzutem ekranu z iOS
Safari, PWA na ekranie głównym) surowy, angielski komunikat przeglądarki
**"The string did not match the expected pattern."** w miejscu, gdzie
appka pokazuje własne (zawsze polskie) błędy — plus, osobno: "odległość
od drogi gminnej nie działa, tu też potrzeba testów". Diagnoza (bez
możliwości odtworzenia na żywo — sandbox nie ma dostępu do gov.pl ani do
prawdziwego iOS Safari) dała DWIE kolejne, niezależne poprawki:

1. **`TIMEOUT_OVERPASS` (14s) był KRÓTSZY niż `[timeout:25]`** — dyrektywa
   wpisana we WSZYSTKIE 3 zapytania Overpass w `services/nearby_features.py`
   (droga gminna + cieki wodne). To znaczy: appka sama mówiła serwerowi
   "masz 25 sekund", ale własny klient httpx poddawał się już po 14 —
   REZYGNOWAŁ, ZANIM Overpass zdążyłby dokończyć, gdy był choć trochę
   obciążony. Dokładnie pasuje do "czasem działa, czasem nie" — nie awaria
   usługi, tylko wewnętrzna niespójność dwóch liczb w naszym własnym
   kodzie. **Naprawa**: `TIMEOUT_OVERPASS` podniesiony do 30s (z marginesem
   nad 25). Nowy test regresyjny parsuje FAKTYCZNE zapytania z pliku (nie
   twardo wpisaną liczbę) i pilnuje, że `TIMEOUT_OVERPASS` zawsze
   przewyższa najdłuższą dyrektywę `[timeout:N]` — potwierdzone, że test
   faktycznie łapie ten dokładny błąd (ręcznie cofnięty do 14s, test
   czerwony z jasnym komunikatem, przywrócony).
2. **`new URL(event.request.url)` w `service-worker.js` mogło rzucić i
   crashować cały handler `fetch`** — to jest DOKŁADNIE komunikat, jaki
   WebKit (Safari/iOS) rzuca dla `URL()`/`fetch()` na nieparsowalnym
   ciągu; w tej appce nie ma ŻADNEGO innego `new URL()` w kodzie
   frontendowym, więc to najbardziej prawdopodobne źródło. **Naprawa**:
   `try/catch` wokół konstrukcji `URL` — przy błędzie service worker po
   prostu NIE przechwytuje tego jednego żądania (`return`), zamiast
   crashować, i przepuszcza je do przeglądarki tak, jakby service workera
   tam nie było.
3. **Dodatkowo, niezależnie od powyższego**: appka mogła pokazać
   UŻYTKOWNIKOWI surowy, nieprzetłumaczony komunikat silnika przeglądarki
   (dowolny `TypeError`/`SyntaxError`, nie tylko z `URL()`) wprost w
   `errorBox`, bo `catch (err) { showError(err.message || "...") }` ufało
   `err.message` bezwarunkowo. **Naprawa**: nowy `friendlyErrorMessage(err)`
   w `app.js` — pokazuje `err.message` WPROST tylko dla błędów, które
   appka sama rzuciła (`err.constructor === Error`, zawsze polski,
   sensowny tekst), a dla KAŻDEGO innego typu błędu (natywny
   `TypeError`/`SyntaxError` z przeglądarki) pokazuje generyczny polski
   komunikat + loguje pełny błąd do konsoli. Zweryfikowane funkcjonalnie
   w Playwright dwoma scenariuszami: (a) zasymulowany natywny błąd
   (uszkodzony JSON w odpowiedzi `/api/resolve` → prawdziwy `SyntaxError`
   z `resp.json()`) → pokazuje generyczny komunikat, NIE surowy tekst
   silnika; (b) normalny błąd z backendu (404 z `detail`) → pokazuje
   dokładnie własny, oryginalny tekst appki bez zmian.

1 nowy test pytest (104 łącznie, było 103) + 2 nowe scenariusze
zweryfikowane w Playwright (nie dodane na stałe do repo — ad-hoc w
scratchpadzie, ten sam wzorzec co poprzednie wizualne/funkcjonalne
weryfikacje w tej sesji).

**Ważna lekcja z całej tej rundy** (3 kolejne zgłoszenia od Klaudii z
żywej appki): sandbox, w którym pracuję, nie ma dostępu do ŻADNEJ z
usług rządowych/OSM ani do prawdziwej przeglądarki mobilnej — więc
regresje takie jak niespójny timeout czy specyficzny dla WebKit błąd
`URL()` da się znaleźć TYLKO przez czytanie kodu i wnioskowanie, nigdy
przez odtworzenie na żywo tutaj. Kiedy Klaudia zgłasza coś z żywej appki,
to jedyny sposób na sprawdzenie danej klasy błędów — warto to robić
systematycznie (przeczytać CAŁĄ ścieżkę kodu, nie tylko fragment, który
akurat wygenerował błąd), zamiast zakładać z góry "to na pewno przez
ostatnią zmianę UI".

Cache-bust: `app.js?v=29`, service worker `v21`.

### Dystans mediów, wyścig zamiast kolejki dla Overpass, i realne dowody na MPZP z e-mapa.net — dodane 2026-09-04

Klaudia dalej testowała działkę testową Zawoja i przysłała: (1) drogę
gminną WCIĄŻ niedziałającą mimo poprzedniej poprawki timeoutu, (2) prośbę
o pokazywanie odległości w metrach dla obecnych mediów (jak Działkopedia:
"71m dobry dojazd"), (3) trzy zrzuty ekranu z **własnych DevTools na
polska.e-mapa.net**, pokazujące realne zapytanie/odpowiedź WMS dla MPZP
tej działki — z konkretnym, ustrukturyzowanym wynikiem: **Status: prawnie
wiążący lub realizowany** (uchwała X/84/2019, weszła w życie 2019-07-31).

**1. Dystans mediów w metrach — dodane.** `services/utilities.py::check_utilities()`
liczyła dotąd tylko liczbę nieprzezroczystych pikseli w kaflu WMS (obecność
tak/nie). Ponieważ rozmiar kafla w metrach i w pikselach są znane
(120 m / 240 px = 0.5 m/px), teraz znajdowana jest też odległość
NAJBLIŻSZEGO nieprzezroczystego piksela od środka kafla (czyli od
działki) i przeliczana na metry — przybliżenie z detekcji obrazu, nie
geometria wektorowa, ale ten sam rodzaj odpowiedzi co Działkopedia.
Nowe pole `distance_m` w każdym elemencie `utilities.utilities`, pokazywane
pod etykietą w kafelku (`.chip-dist`, tylko dla `present: true`). 1 nowy
test (kalibrowany klaster pikseli w znanej odległości od środka,
sprawdza że przeliczenie na metry jest poprawne).

**2. `TIMEOUT_MPZP_DETAIL` (12s) podniesiony do 20s.** Zrzuty ekranu
Klaudii potwierdziły z zewnętrznego źródła (patrz punkt 3), że plan
miejscowy Zawoi jest w pełni "prawnie wiążący" — nie ma powodu, żeby nasz
`zoning.py` pokazywał to jako niepewny "partial" (żółty), skoro dane
istnieją. Najbardziej prawdopodobne wyjaśnienie: 12s to WYRAŹNY wyjątek
na tle reszty timeoutów w tym pliku (wszystkie inne "pobranie
szczegółów z niepewnego serwera rządowego" to 20-45s) — podniesiony do
20s, świadomie NIE do 45s jak WFS powiatowe, bo (w przeciwieństwie do
Overpass, gdzie niespójność była pewna) HANDOFF.md już wcześniej
udokumentował, że GetFeatureInfo KIMPZP dla NIEKTÓRYCH gmin wisi
NIESKOŃCZENIE — więc nieograniczony timeout tu nie byłby dobrym
rozwiązaniem. **To najlepiej uzasadniona hipoteza, NIE gwarantowana
naprawa** (nie da się zweryfikować bez żywego dostępu z tego środowiska).

**3. `http_utils._overpass_query` — wyścig zamiast kolejki.** Mimo
poprawki niespójności timeoutu (poprzednia runda), Klaudia zgłosiła że
droga gminna WCIĄŻ nie działa. Zdiagnozowany kolejny, niezależny problem
architektoniczny: dwa lustra Overpass były próbowane PO KOLEI — jeśli
pierwsze jest zablokowane/przeciążone (realne ryzyko dla współdzielonych
adresów IP hostingu typu Render wobec darmowych publicznych instancji
Overpass), MUSI najpierw wyczerpać swój PEŁNY timeout, zanim drugie w
ogóle zostanie spróbowane — więc dodanie kolejnych luster tylko
pogarszałoby najgorszy przypadek czasu odpowiedzi. **Naprawa**: oba
lustra odpytywane RÓWNOCZEŚNIE (`asyncio.wait(..., FIRST_COMPLETED)`),
pierwsze, które faktycznie odpowie, wygrywa, reszta anulowana — to
poprawia jednocześnie odporność (zablokowane lustro już nie opóźnia
zdrowego) I najgorszy czas odpowiedzi (jeden timeout, nie N timeoutów).
3 nowe testy, w tym jeden mierzący realny czas wykonania (`time.monotonic`),
potwierdzający, że wolne/zablokowane lustro faktycznie nie blokuje
zwrócenia wyniku ze zdrowego. **Nadal nie mam pewności, że to naprawia
cały problem** — jeśli oba lustra są faktycznie zablokowane dla ruchu z
Render (nie tylko wolne), żadna zmiana architektury zapytań tego nie
naprawi; to już wymaga sprawdzenia z żywego wdrożenia, nie z tego
środowiska (gov.pl i inne domeny rządowe/OSM są tu całkowicie
zablokowane).

**4. WAŻNE ODKRYCIE od Klaudii, jeszcze NIE zaimplementowane**: zrzuty
ekranu z DevTools na `polska.e-mapa.net` pokazują, że ta strona (dla
działek w gminach obsługiwanych przez firmę Geo-System) odpytuje
BEZPOŚREDNIO serwer WMS dostawcy gminy —
`https://wms16.epodgik.pl/cgi-bin/int_mpzp?...&LAYERS=app.RysunkiAktuPlanowania.MPZP`
— zamiast krajowego agregatora GUGiK (KIMPZP/KIAPP), którego używa nasz
`zoning.py`. Odpowiedź ma ustrukturyzowane pola (TERYT, Nazwa planu,
Uchwała, Data wejścia w życie, **Status**, Aktualność danych) — dokładnie
to, czego szukaliśmy dla statusu planu ogólnego/MPZP, i to z realnym,
działającym, szybkim (~90ms w Network tab) źródłem, nie z niepewnego
krajowego agregatora. **Świadomie NIE zaimplementowane w tej rundzie**:
to adres konkretnego DOSTAWCY GIS jednej gminy (Geo-System/ePODGiK), nie
krajowy standard — uogólnienie na całą Polskę wymagałoby rejestru
gmina→dostawca→URL, podobnego do istniejącego `WFS_POWIAT_REGISTRY` w
`wfs_search.py` (budowanego gmina po gminie / dostawca po dostawcy przez
wiele rund pracy), nie prostej podmiany jednego URL-a. Warto rozważyć
jako osobny, świadomie zaplanowany projekt, jeśli Klaudia zdecyduje że to
priorytet — nie zgadywać/hardkodować jednego adresu na próbę.

4 nowe testy pytest w tej rundzie (108 łącznie, było 104) — 1 dla dystansu
mediów, 3 dla wyścigu Overpass. Cache-bust: `app.js?v=30`, service worker `v22`.

### Krytyczny błąd: cała analiza padała z niezrozumiałym błędem — dodane 2026-09-04

Klaudia zgłosiła "Wystąpił nieoczekiwany błąd sieci lub przeglądarki" (nasz
własny, generyczny fallback z `friendlyErrorMessage()` — patrz poprzednia
runda) dla zapytania **"Korbielów 3917/5"** — cała analiza padała, żadnego
wyniku, poprosiła o priorytetowe potraktowanie i wstrzymanie innych prac
(zapisane jako TODO w tej sesji: dokończenie podniesienia
`TIMEOUT_OVERPASS`, zbadanie zasięgu sieci UKE, zbadanie skali Bortle —
wszystkie wróciły do kolejki po tej naprawie).

**Diagnoza**: `/api/resolve`'s kaskada wieloetapowa (patrz docstring w
`main.py` — do 5 etapów: bezpośrednie ULDK, geokodowanie gminy + skan ID,
skan geometrii WFS, warianty numerowanych obrębów, geokodowanie POWIATU +
powtórka skanów dla KAŻDEJ gminy w powiecie) **nie miała ŻADNEGO łącznego
budżetu czasu**. Dla nazwy, która nie rozpoznaje się jako gmina na
wcześniejszych etapach (jak najwyraźniej "Korbielów"), kaskada spada aż do
etapu 5 — skanowania wszystkich gmin całego powiatu, gdzie
`scan_wfs_for_parcel_number()` samo w sobie może trwać do
`TIMEOUT_WFS_POWIAT=45s` NA GMINĘ. Całkowity czas takiego zapytania mógł
więc realnie przekroczyć minutę.

**Dlaczego to dawało akurat TEN konkretny, niezrozumiały błąd**: gdy
zapytanie trwa dłużej niż limit czasu serwera proxy Render (nieznany z
tego środowiska — dashboard Render też jest niedostępny stąd), proxy samo
przerywa połączenie i zwraca WŁASNĄ stronę błędu (HTML, nie JSON). Frontend
zawsze robi `resp.json()` na odpowiedzi — dla HTML-a to rzuca prawdziwym
`SyntaxError` (natywny wyjątek przeglądarki, nie nasz `Error`), który
`friendlyErrorMessage()` (dodane poprzednią rundą) poprawnie rozpoznaje
jako "nieprzewidziany" i pokazuje generyczny komunikat — mechanizm
zadziałał zgodnie z projektem, ale sam PRZYCZYNA (appka pozwoliła sobie na
nieograniczony czas wyszukiwania) pozostawała nienaprawiona.

**Naprawa**: nowy `TIMEOUT_RESOLVE_BUDGET = 50.0` (`config.py`) — cała
kaskada `/api/resolve` owinięta w `asyncio.wait_for()`
(`_do_resolve()` — wydzielona jako wewnętrzna funkcja z istniejącej logiki,
bez zmiany samej logiki wyszukiwania). Jeśli przekroczy budżet, appka SAMA
zwraca czysty, zrozumiały komunikat PO POLSKU (`HTTPException(504, ...)`,
poprawny JSON) — **zanim** zrobi to za nią proxy Render. To nie sprawia,
że "Korbielów 3917/5" nagle się znajdzie (jeśli kaskada faktycznie
potrzebuje więcej niż 50s, wciąż dostanie błąd) — ale zamienia mylący,
nieprzetłumaczony crash w jasny komunikat z konkretną sugestią (spróbuj
pełnego identyfikatora TERYT). Wybrana wartość (50s) jest świadomym
kompromisem: bezpiecznie mieści jeden najgorszy przypadek gminy (45s) z
marginesem, ale NIE jest potwierdzona względem faktycznego limitu Render
(nie da się tego sprawdzić z tego środowiska) — jeśli to nadal się zdarza,
oznacza to że limit Render jest krótszy niż 50s i budżet trzeba obniżyć,
nie podnieść.

1 nowy test pytest (109 łącznie) — monkeypatch `TIMEOUT_RESOLVE_BUDGET` na
bardzo małą wartość + zawieszona pierwsza funkcja kaskady, potwierdza że
`resolve_parcel()` rzuca czysty `HTTPException(504, ...)` z nazwą
zapytania w treści, zamiast wisieć bez końca.

### KIMPZP ConnectTimeout bez retry + brak wiersza w checkliście dla sekcji niepełnych — dodane 2026-09-04

Klaudia zgłosiła na żywo (działka "Korbielów 3917/5") dwie osobne
usterki widoczne na jednym zrzucie ekranu: (1) plan zagospodarowania
pokazywał błąd `ConnectTimeout` mimo że warstwa planu WYRAŹNIE renderowała
się na mapie chwilę później (czyli usługa faktycznie odpowiadała, tylko
nie za pierwszym razem), i (2) 3 sekcje, które przez to (i przez inne,
przejściowe błędy) zakończyły się statusem innym niż `ok`, w ogóle nie
miały wiersza w checkliście nad listą "kroków przed zakupem" — było tylko
jedno zdanie podsumowujące ("Niepełne dane: ...") nad kartą wyniku, żadnego
śladu w samej liście statusów.

**Naprawa 1 — retry dla `_mpzp_has_plan_drawn()` (`services/zoning.py`)**:
ta funkcja (szybki podgląd GetMap, używany PRZED znacznie mniej
niezawodnym `GetFeatureInfo`) robiła dotąd surowe, jednorazowe
`client.get()` bez żadnego retry — każda przejściowa usterka sieciowa do
`mapy.geoportal.gov.pl` (ten sam realny ryzyko, już udokumentowane dla
INNYCH usług WMS/WFS tej appki) od razu kończyła się pełnym błędem sekcji.
Teraz używa współdzielonego `http_utils._get_with_retry()` (ta sama
ochrona, którą miały już zapytania WFS powiatowe) — dodano do niego
parametr `follow_redirects: bool = False` (potrzebny tu, bo GetMap czasem
przechodzi przez przekierowanie), domyślnie wyłączony, więc nic innego się
nie zmienia. Nowy test (`test_mpzp_has_plan_drawn_retries_on_connect_timeout`)
symuluje `httpx.ConnectTimeout` przy pierwszej próbie i sukces przy
drugiej — potwierdza, że funkcja teraz zwraca poprawny wynik zamiast
rzucać błąd.

**Naprawa 2 — wiersz "BRAK DANYCH" dla sekcji niepełnych
(`services/verdict.py`, `static/app.js`, `static/index.html`)**: dodany
nowy, neutralny poziom `"unknown"` (obok istniejących `risk`/`warning`/
`ok`) — **nigdy nie odejmuje punktów** (świadomie: brak danych to nie
dowód ryzyka, tak samo jak nie jest dowodem "wszystko w porządku" — to
właśnie filozofia, dla której ta checklista w ogóle istnieje). Każda
sekcja, która wcześniej trafiała tylko do `incomplete_sections` (bez
żadnego wiersza), teraz DODATKOWO dostaje wiersz w checkliście z etykietą
sekcji, szarą plakietką "BRAK DANYCH" i linkiem do tej samej karty
szczegółów niżej (identyczny mechanizm jak dla pozostałych wierszy) —
`incomplete_sections` i zdanie podsumowujące nad kartą wyniku zostały bez
zmian, to uzupełnienie, nie zamiana. Zmiany frontendu: `pillLabel.unknown`,
kolejność sortowania (`risk, warning, unknown, ok`), nowy token kolorów
`--neutral`/`--neutral-bg` i CSS dla `.cc.unknown` / `.check-row.tier-unknown`.
`counts` w odpowiedzi API ma teraz 4 klucze zamiast 3
(`{risk, warning, ok, unknown}`). Cache-bust: `app.js?v=31`,
`CACHE_NAME` service workera → `v23`.

Istniejący test dla sekcji, która się nie udała, przemianowany i
rozszerzony (`test_build_verdict_failed_section_is_incomplete_and_gets_unknown_row`)
— teraz potwierdza zarówno brak odjęcia punktów, jak i obecność nowego
wiersza `tier: "unknown"`. Łącznie 110 testów pytest.

### Trzy optymalizacje wydajności (a/b/c) + dwa realne błędy współbieżności znalezione przy okazji — dodane 2026-09-04

Klaudia poprosiła o wdrożenie wszystkich 3 propozycji z poprzedniego punktu
TODO naraz: (a) jeden trwały `httpx.AsyncClient`, (b) cache dla planu
zagospodarowania, (c) strumieniowanie wyników (SSE).

**(a) Trwały `httpx.AsyncClient` (`main.py`).** Każda z 4 tras HTTP otwierała
dotąd własny `async with httpx.AsyncClient(...) as client:` — nowe
połączenie TCP+TLS do KAŻDEGO z kilkunastu zewnętrznych hostów rządowych/OSM
przy KAŻDYM żądaniu, zamiast ponownego użycia już otwartych połączeń
keep-alive z puli httpx. **Naprawa**: `main.py::_get_http_client()` — leniwie
tworzony, modułowy singleton, tworzony też przez FastAPI `lifespan` (nowy
`_lifespan()`, zamyka klienta przy shutdownie) i leniwie w razie potrzeby
(dla wywołań route'ów bezpośrednio z testów, z pominięciem `lifespan` — np.
istniejący `tests/test_pure_logic.py::test_resolve_parcel_times_out_...`,
który woła `main.resolve_parcel()` wprost). `httpx.AsyncClient` jest
udokumentowany jako bezpieczny do współbieżnego użycia przez wiele żądań
naraz, więc to nie jest obejście, tylko właściwe rozwiązanie. Wszystkie 4
trasy (`/api/resolve`, `/api/resolve-address`, `/api/search-by-parcel-size`,
`/api/analyze`) zaktualizowane; ujednolicony User-Agent
(`"AnalizaDzialkiGIS/2.0"` — wcześniej `/api/analyze` miał osobny, niespójny
`"AnalizaDzialki/2.0"`, bez `GIS`, czysty przeoczony detal sprzed podziału
`main.py` na moduły).

**(b) Cache dla planu zagospodarowania.** Plan zagospodarowania był jedyną
z sekcji wzbogacających świadomie NIE cache'owaną (patrz „Pamięć podręczna"
wyżej) — do czasu, aż zaistniałby widoczny w UI mechanizm „dane z: [data]".
Ten mechanizm (`fetched_at`/`dataAgeNote()`) już istniał dla WSZYSTKICH
innych cache'owanych sekcji od tamtego dnia, więc podłączenie zoningu do
DOKŁADNIE TEGO SAMEGO `cache.get_or_fetch()` dało mu tę samą przejrzystość
za darmo — bez tego kryterium blokującego nie było już powodu czekać.
Nowy `TTL_ZONING = 7 dni` (`config.py`) — świadomie krótszy niż 30-180 dni
reszty usług, bo to jedyna sekcja z realną wagą decyzyjną (gmina może
uchwalić nowy plan w trakcie czyjejś decyzji o zakupie). `cache.get_or_fetch`
cache'uje WYŁĄCZNIE `status: "ok"` — `"partial"` (szczegóły MPZP nie zdążyły
dojść, ale plan widoczny) i `"error"` nigdy się nie zamrażają, dokładnie jak
dla reszty usług. Przycisk „odśwież teraz" (ręczne pominięcie cache'u) i
cache dla identyfikacji ULDK (7 dni) NADAL nie są zrobione — patrz TODO w
sekcji 0.

**(c) Strumieniowanie wyników — nowy `GET /api/analyze-stream` (SSE).**
`/api/analyze` (bez zmian, nadal działa dokładnie jak wcześniej) czekał na
`asyncio.gather()` WSZYSTKICH 12 gałęzi analizy naraz, w tym najwolniejszą
i najmniej niezawodną (plan zagospodarowania) — appka pokazywała pusty
ekran ładowania, dopóki nawet najszybsza sekcja (np. osuwiska, ~1-2s) nie
mogła się wyświetlić. Nowy endpoint strumieniuje Server-Sent Events:
- `event: meta` — tożsamość działki, geometria, centroid, powierzchnia,
  statyczne linki (`permits`/`land_registry`/`map_layers`) — nic tu nie
  wymaga sieci poza już wykonanym lookupem ULDK, więc to zawsze pierwszy
  fragment, renderowany natychmiast (mapa + linia „teryt-echo").
- `event: section` — `{"key": <jedna z 12 nazw>, "value": <wynik sekcji>}`,
  po jednym na gałąź, w kolejności w jakiej faktycznie się kończą
  (`asyncio.as_completed`, nie `asyncio.gather`).
- `event: done` — `{"verdict", "due_diligence", "valuation"}` — te trzy pola
  potrzebują WSZYSTKICH 12 wyników naraz (patrz `_compute_derived()`), więc
  fizycznie nie mogą przyjść wcześniej niż na końcu.

Refaktor w `main.py`: `_section_specs()` (buduje listę 12 par
nazwa+awaitable) i `_compute_derived()` (werdykt/lista kroków/wycena z
kompletu wyników) są teraz WSPÓLNE dla `/api/analyze`
(`asyncio.gather`) i `/api/analyze-stream` (`asyncio.as_completed` +
strumieniowanie) — żeby dwie kopie tej samej logiki (12 usług, TTL, kolejność
pól) nie mogły się po cichu rozjechać. Podobnie `_analyze_meta()` (parcel/
geometria/linki) i `_resolve_parcel_geometry()` (lookup ULDK + geometria
pochodna) są dzielone przez oba endpointy.

**Ważny szczegół implementacyjny**: `_resolve_parcel_geometry()` (lookup
ULDK, może rzucić `HTTPException` 404/502) jest wywoływane PRZED
utworzeniem `StreamingResponse` — raz nagłówki odpowiedzi 200 zostaną
wysłane (co Starlette robi zaraz po rozpoczęciu iteracji po generatorze),
nie da się już zmienić kodu statusu na normalny błąd JSON. Dzięki temu
`/api/analyze-stream?parcel_id=zły_numer` nadal zwraca czysty `404` z
`detail`, dokładnie jak `/api/analyze` — nie „udaje" sukcesu SSE dla
nieistniejącej działki. Każda POJEDYNCZA sekcja natomiast MOŻE się nie
udać już PO rozpoczęciu strumienia (np. nieoczekiwany wyjątek zamiast
zwróconego `{"status": "error", ...}`, którego usługi z zasady same nie
powinny rzucać, ale to ostatnia linia obrony) — `_named()` łapie to i
zamienia w wiersz `{"status": "error", "message": "Wewnętrzny błąd sekcji: ..."}`
zamiast wywalić cały strumień z 500 (nie da się już wtedy zwrócić błędu
HTTP, nagłówki 200 już poszły). Rozłączenie w trakcie strumienia (albo
inne przedwczesne wyjście z generatora) anuluje wszystkie jeszcze
niedokończone z 12 zadań `asyncio.create_task`/`asyncio.ensure_future`,
zamiast zostawić je samopas.

**Frontend (`static/app.js`)**: `analyzeTerytId()` woła teraz nowy
`streamAnalysis(terytId)` zamiast pojedynczego `fetch('/api/analyze?...')`.
Świadomie `fetch()` + `resp.body.getReader()`, NIE natywny `EventSource` —
`EventSource` nie daje dostępu do treści/kodu odpowiedzi błędu, a appka
zawsze pokazywała dokładny polski komunikat backendu (`data.detail`) przy
błędzie 400/404, co dla nieistniejącej działki musiało zostać zachowane.
Ręczny parser SSE dzieli strumień na ramki po `\n\n` (obsługuje ramki
podzielone między kolejne odczyty `reader.read()`, buforując resztki).
`renderResults(data, pending)` dostał drugi, opcjonalny parametr —
`pending`, `Set` kluczy sekcji, których jeszcze nie ma w `data`. Każda karta
sekcji (`cardEgib`, `cardLandslide`, `cardUtilities`, `cardHydrology`,
`cardNature`, `cardAirQuality`, `cardRoad`, `cardZoning`) sprawdza teraz
najpierw `pending.has(...)` i renderuje `loadingCardHTML()` („Sprawdzam…")
zamiast prawdziwej treści, dopóki dane nie dotrą — logika budowania
KAŻDEJ karty z prawdziwych danych jest niezmieniona, tylko owinięta w
`if/else`. Werdykt/checklista/wycena (`v`/`dd`/`val`) czekają na zdarzenie
`"done"` — dopóki go nie ma, karta werdyktu pokazuje pasek postępu
(„Sprawdzam działkę… (N/12)"), bez zwodniczego pustego/częściowego wyniku.
`renderMap()` woła się raz, przy zdarzeniu `"meta"` — mapa i linia
tożsamości działki pojawiają się natychmiast, zanim jakakolwiek sekcja
zdąży się policzyć.

**Dwa realne błędy współbieżności znalezione przy weryfikacji (nie
związane z (a)/(b)/(c) wprost, ale ujawnione przez nie) —
`services/cache.py`.** Zweryfikowane przez Starlette `TestClient` +
zamockowane usługi (bo sieć rządowa jest niedostępna z tego środowiska —
patrz sekcja 8): pierwsze prawdziwe wywołanie `/api/analyze` na świeżym
`cache.db` (czyli DOKŁADNIE stan po KAŻDYM deployu na Render, bo cache
resetuje się przy restarcie kontenera — patrz „Pamięć podręczna" wyżej)
uruchamia 12 współbieżnych `cache.get_or_fetch()` naraz przez
`asyncio.gather`/`asyncio.as_completed`. To ujawniło:
1. **Wyścig przy tworzeniu połączenia** — kilka wątków roboczych
   (`asyncio.to_thread`) mogło jednocześnie zobaczyć `_conn is None` i
   zacząć czytać z bazy, zanim `CREATE TABLE` innego wątku zdążyło się
   zatwierdzić (`sqlite3.OperationalError: no such table: cache_entries`).
2. **Wyścig przy współdzielonym połączeniu** — `check_same_thread=False`
   znosi TYLKO ograniczenie „ten sam wątek", nie czyni jednego obiektu
   `sqlite3.Connection` bezpiecznym do naprawdę współbieżnego użycia z
   wielu wątków; dwa wątki wołające `conn.execute()`/`conn.commit()` w tym
   samym momencie na tym samym połączeniu ścigały się o własne, niejawne
   transakcje (`sqlite3.OperationalError: cannot commit - no transaction
   is active`).

**Naprawa**: jeden `threading.RLock` (`_conn_lock`, RLock nie zwykły Lock,
bo `_read_row`/`_write_row` trzymają go i wołają `_get_conn()`, który
próbuje go wziąć ponownie na tym samym wątku — zwykły `Lock` by się
zakleszczył) trzymany przez CAŁY czas trwania tworzenia połączenia, odczytu
i zapisu — serializuje cały dostęp do SQLite w tym module. Pojedyncza
operacja SQLite trwa mikrosekundy, więc to nie zjada współbieżności, którą
`asyncio.gather`/`asyncio.to_thread` dają reszcie `/api/analyze` (prawdziwe
zapytania sieciowe biegną poza tym lockiem). Nowy test regresyjny
(`test_get_or_fetch_concurrent_first_touch_does_not_race`) uruchamia 24
współbieżne pierwsze dotknięcia cache'u na świeżym `cache.db` — bez locka
rzucał którymś z dwóch powyższych wyjątków, z lockiem przechodzi.

**Testy**: nowy `test_get_or_fetch_concurrent_first_touch_does_not_race`
(opisany wyżej) i
`test_analyze_and_analyze_stream_agree_on_sections_and_derived_fields` —
end-to-end przez `fastapi.testclient.TestClient` (realne uruchomienie
`lifespan` + routing FastAPI, z zamockowanymi funkcjami usług), sprawdzający
że `/api/analyze` i zebrany-z-SSE `/api/analyze-stream` zwracają dokładnie
te same 12 wyników sekcji oraz to samo `verdict`/`due_diligence`/`valuation`
(pomijając `cached`/`fetched_at`, które różnią się między dwoma kolejnymi
wywołaniami przez sam fakt trafienia w cache przy drugim). Razem 112 testów
pytest (było 110).

**Zweryfikowane wizualnie i funkcjonalnie w Playwright** (ten sam wzorzec co
poprzednie rundy — lokalny `npm install leaflet`, bo `unpkg.com` jest
zablokowane przez proxy tego środowiska identycznie jak domeny rządowe) —
ad-hoc serwer Node serwujący `static/` z podmienionym źródłem Leaflet i
własnym, kontrolowanym `/api/analyze-stream` (12 sekcji z opóźnieniem
120ms między każdą, zoning celowo ostatni). Potwierdzone na zrzutach
ekranu: ~500ms po kliknięciu „Sprawdź działkę" mapa i linia tożsamości są
już widoczne, karta „Plany zagospodarowania" pokazuje „Sprawdzam…", karta
„Zagrożenie osuwiskowe" (jedna z pierwszych 4 gotowych sekcji) już pokazuje
prawdziwą treść, a werdykt pokazuje pasek postępu „Sprawdzam działkę…
(4/12)"; po dojściu zdarzenia `"done"` werdykt pokazuje finalny wynik
(„90 · Dobra"), a karta zoningu pokazuje prawdziwą treść. Zero błędów JS w
konsoli poza oczekiwanymi, niezamockowanymi zapytaniami o kafelki
mapy/warstwy WMS (blokowane przez proxy tej piaskownicy, nie błąd appki).

**Nie zweryfikowane na żywo**: rzeczywiste przyspieszenie na produkcji
(Render) — logowanie czasu z `_timed()` (dodane wcześniej, patrz „Pamięć
podręczna") to jedyny sposób to zmierzyć stąd, tak jak zawsze w tym
środowisku (sieć rządowa niedostępna — patrz sekcja 8). Także: czy proxy
Render bufory całą odpowiedź `StreamingResponse` zamiast przepuszczać ją
fragmentami na bieżąco — jeśli tak, strumieniowanie nadal zadziała
POPRAWNIE (frontend dostanie te same dane), tylko straci część zysku w
postrzeganej szybkości (wszystko przyjdzie naraz na końcu zamiast
progresywnie) — nie regresja, tylko brak pełnego zysku z (c) w tym
konkretnym scenariuszu; dodany nagłówek `X-Accel-Buffering: no` to
standardowa, ale niepotwierdzona z tego środowiska, próba wyłączenia
takiego buforowania po stronie proxy.

Cache-bust: `app.js?v=32`, `CACHE_NAME` service workera → `v24`.

### 3.2 Zakładka „Szukaj działki" — wyszukiwanie po miejscowości + rozmiarze

To jest najbardziej złożona część appki. Pełny pipeline:

1. **Geokodowanie miejscowości** → punkt reprezentatywny. **WAŻNE**: sama
   nazwa miejscowości (np. „Kasinka Mała") pasuje do dziesiątek pojedynczych
   adresów domów w tej wsi — branie pierwszego z brzegu dawało **niestabilne
   wyniki** (różne przy tym samym zapytaniu!). Rozwiązanie: bierzemy
   **medianę** wszystkich dopasowanych punktów (funkcja
   `_gather_nearby_parcels`), nie pierwszy wynik.
2. **Ustalenie powiatu** tego punktu przez `find_parcel_by_xy` (ULDK).
3. **Wyszukanie WFS** — zobacz sekcję 4 poniżej, to osobny, ważny temat.
4. **Rozwiązanie każdego kandydata** przez ULDK `GetParcelByXY` (z
   geometrią), obliczenie **prawdziwej powierzchni geodezyjnej** oraz
   **wymiarów** (najmniejszy prostokąt otaczający — `minimum_rotated_rectangle`
   z Shapely, bo prawdziwe działki rzadko są idealnymi prostokątami).
5. **Filtrowanie i sortowanie** wg podanych kryteriów.

**Kryteria wyszukiwania (dowolna kombinacja, funkcja `search_parcels_universal`):**
- **Powierzchnia** (±10%)
- **Szerokość i długość razem** (±10% każda — **zmienione z ±20% na
  ±10% 2026-09-03 na życzenie Klaudii**, dopasowanie niezależne od
  kolejności — nie trzeba wiedzieć, które z dwóch podanych liczb to
  „szerokość")
- **Jeden wymiar** (tylko w połączeniu z powierzchnią — sam jeden wymiar to
  za mało informacji, appka to blokuje z jasnym komunikatem)
- **Tryb „maksimum"** (`dims_as_maximum=true`, checkbox w UI) — oba wymiary
  wymagane, traktowane jako **twardy sufit**, nie przybliżenie (bez
  koncepcji tolerancji, więc ta zmiana go nie dotyczy). Sortowanie
  wtedy inne: od najpełniej wykorzystujących dostępną przestrzeń (blisko
  limitu), nie od najbliższych jakiemuś celowi.
- **Liczba wyników: bez limitu od 2026-09-03** (na życzenie Klaudii;
  wcześniej `max_results=10` obcinał do 10 najlepszych). Teraz
  `search_parcels_universal(..., max_results=None)` zwraca WSZYSTKIE
  działki w promieniu spełniające podane kryteria, posortowane
  od najlepiej dopasowanej. `matches[:None]` w Pythonie to cała lista
  (bez zmian w kodzie poza domyślną wartością parametru) — `max_results`
  zostawiony jako parametr na wypadek, gdyby limit kiedyś jednak był
  potrzebny (np. dla bardzo gęsto podzielonych osiedli).

**Promień wyszukiwania i szukanie po całym powiecie (od 2026-09-03)**:
Klaudia próbowała wpisać "Powiat suski" jako miejscowość — nic nie
znalazło. Poproszona o wyjaśnienie, świadomie wybrała **bezpieczniejszą**
z dwóch opcji: znacznie większy promień (nie prawdziwe przeszukanie
całego powiatu bez limitu). Powód: `enumerate_parcel_points_in_area`
zwraca surowe punkty z WFS, ale każdy kandydat i tak trzeba osobno
doresolvować przez ULDK (`find_parcel_with_area_by_xy`) żeby dostać
prawdziwą geometrię/wymiary — prawdziwy powiat to nawet dziesiątki
tysięcy działek, co przy obecnej architekturze (jeden ULDK call na
kandydata, `asyncio.gather` na wszystkie naraz) oznaczałoby lawinę
równoległych zapytań do rządowego serwisu i realne ryzyko timeoutu/
przeciążenia — dokładnie to, czego chcieliśmy uniknąć.
- `enumerate_parcel_points_in_area`: `radius_m` domyślnie **15000** (z
  2000). `max_features` **zostaje na 500** — to jest właściwy "wentyl
  bezpieczeństwa" ograniczający liczbę downstream wywołań ULDK,
  niezależnie od promienia. W gęstej okolicy przy 15km promieniu i
  limicie 500 realnie sprawdzana jest tylko najbliższa część obszaru
  (kolejność zwracana przez serwer WFS, nie odległość) — to świadomy
  kompromis, nie błąd.
- **Wpisanie "Powiat X" jako miejscowości**: darmowy geokoder GUGiK
  (`geocode_address_points`, pole `q`) nie zna nazw powiatów — to
  wyszukiwarka adresów/miejscowości, nie jednostek administracyjnych.
  Dodany fallback w `_gather_nearby_parcels` (`services/wfs_search.py`):
  jeśli zapytanie zaczyna się od "Powiat "/"pow. "/"pow " i zwykłe
  geokodowanie nic nie znalazło, zdejmujemy przedrostek i pytamy nową
  funkcję `geocode_powiat_gmina_points()` (`services/geocoding.py`) o
  strukturalne pole `pow_nazwa` — dokładnie ten sam wzorzec, który już
  działa dla gmin (`geocode_gmina_candidates`, pole `gm_nazwa`, patrz
  sekcja 3.2 wyżej o „Milówka 2994/4"). **Potwierdzone na żywo przez
  Klaudię 2026-09-03: `pow_nazwa` działa, "Powiat suski" faktycznie
  zwraca wyniki.**
- **Druga usterka, znaleziona przez Klaudię od razu po pierwszym teście
  ("wyszukuje ale mało pozycji, chyba mały obszar")**: pierwsza wersja
  brała medianę współrzędnych WSZYSTKICH centroidów gmin z powiatu i
  szukała tylko w jednym promieniu (15km) wokół tego jednego punktu —
  ale powiat to zwykle kilka gmin rozrzuconych na dziesiątki km, więc
  jeden okrąg 15km wokół geograficznego środka realnie pokrywał tylko
  wąski wycinek całego obszaru. **Naprawione**: `_gather_nearby_parcels`
  ma teraz osobną gałąź dla zapytań powiatowych (`is_powiat_query`) —
  zamiast jednej mediany, wywołuje `enumerate_parcel_points_in_area`
  OSOBNO dla każdej gminy (współbieżnie, `asyncio.gather`), z mniejszym
  promieniem per gmina (10km, wystarcza na typową gminę) i wyniki łączy
  w jedną listę kandydatów. Wszystkie zapytania per-gmina lecą do TEGO
  SAMEGO serwera WFS (jeden serwer obsługuje cały powiat — patrz
  `_lookup_wfs_config`), więc to bezpieczne zrównoleglenie, nie N
  niezależnych serwerów. `max_features` per gmina skalowany jako
  `max(50, 500 // liczba_gmin)`, żeby SUMA (nie promień) zostawała w
  tym samym bezpiecznym budżecie ok. 500 kandydatów co wcześniej —
  to ona determinuje liczbę downstream wywołań ULDK, niezależnie od
  tego ile gmin ma dany powiat. Jeśli WSZYSTKIE gminy zawiodą z tym
  samym błędem "nie jest jeszcze w naszym rejestrze" (współdzielą ten
  sam serwer, więc to spójny sygnał), błąd jest re-raise'owany zamiast
  cicho zwracać pustą listę — użytkowniczka dostaje właściwy komunikat,
  nie mylące "nic nie znaleziono". 2 nowe testy pytest weryfikujące ten
  fan-out (osobne wywołanie per gmina, poprawne skalowanie
  `max_features`, wyniki z KAŻDEJ gminy w finalnej liście).

**Miniatura kształtu działki (od 2026-09-03)**: każdy wynik ma teraz mały
SVG-obrys działki obok tekstu (`shape-thumb` w `static/index.html`/`app.js`).
Backend: `geo_utils._polygon_outline_normalized(geometry_2180)` bierze
`geometry_2180` już obliczoną w `find_parcel_with_area_by_xy`
(`services/uldk.py`) — nic nowego nie pobiera z sieci — upraszcza ją
(`shapely .simplify`, próg 0.5m, twardszy 2m gdy nadal >40 wierzchołków,
żeby payload nie puchł dla bardzo poszarpanych działek) i normalizuje do
zakresu ~0..64 (dłuższy bok = 64, krótszy proporcjonalnie — prawdziwe
proporcje kształtu zachowane, absolutny rozmiar nie, bo miniatura i tak
go nie pokaże) z odwróconą osią Y (north-up — northing w EPSG:2180 rośnie
w górę, SVG w dół). Nowe pole `shape_points` w każdym elemencie `matches[]`
z `/api/search-by-parcel-size`. Frontend: `shapeThumbnailSVG()` w
`static/app.js` buduje `<svg><polygon>` z tych punktów; pusta/brakująca
tablica → nic się nie renderuje (nie błąd, tylko brak danych — nie
powinno się zdarzać w praktyce, bo geometria zawsze jest dostępna razem z
resztą pól tego samego obiektu, ale frontend i tak to obsługuje na
wszelki wypadek). 3 nowe testy pytest dla `_polygon_outline_normalized`
(proporcje zachowane, north-up, uproszczenie przy wielu wierzchołkach).

**Trzy poprawki jakości dopasowania po wymiarach (ważne, nie usuwaj bez
zastanowienia):**
1. **Filtr prostokątności** (`min_rectangularity=0.65`) — odrzuca działki,
   których prostokąt otaczający jest dużo większy niż rzeczywista
   powierzchnia (czyli działka jest bardzo nieregularna — L-kształt,
   trójkąt) — bo dla takich „szerokość×długość" nic nie znaczy.
2. **RMS zamiast średniej** przy łączeniu błędów szerokości/długości —
   karze nierówne dopasowanie mocniej niż plaska średnia.
3. **Krzyżowa weryfikacja powierzchnią** — gdy podano tylko wymiary (bez
   osobnej powierzchni), iloczyn szerokość×długość jest porównywany z
   prawdziwą powierzchnią jako dodatkowy sygnał.

---

## 4. WFS EGiB — to była największa, najtrudniejsza część projektu

### Co jest zepsute (nie próbuj tego naprawiać, to nie Twoja wina)
Oficjalna, **zbiorcza** usługa WFS GUGiK
(`mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza`) ma
**trwałą awarię bazy danych** — sprawdzone wielokrotnie w różnych dniach,
zawsze ten sam błąd: `msPostGISLayerOpen(): Query error. Database
connection failed`. To nie jest coś, co można naprawić po naszej stronie —
to awaria infrastruktury GUGiK.

### Rozwiązanie, które wdrożyliśmy: bezpośredni routing per powiat
Zamiast czekać na naprawę usługi zbiorczej, **omijamy ją całkowicie** —
każdy z 380 powiatów w Polsce prowadzi **własny, niezależny serwer WFS**.
Znaleźliśmy zweryfikowaną listę tych adresów (nie oficjalny, ale
wiarygodny — oparty na oficjalnym rejestrze GUGiK EZiU) na
`geoinformatyka.com.pl/raporty/analiza_uslug_wfs.html` i sparsowaliśmy do
`wfs_powiat_registry.json` — **380 wpisów, każdy: TERYT powiatu → {url,
version, layer}**.

### Ważne, praktyczne szczegóły tej implementacji
- **Kolejność osi współrzędnych EPSG:2180 różni się per serwer** — część
  serwerów zwraca (northing, easting), część (easting, northing). Zamiast
  zakładać jedną konwencję, kod **wykrywa to automatycznie** dla każdego
  zestawu wyników, licząc odległość do znanego punktu kotwiczącego
  (funkcja `enumerate_parcel_points_in_area`) i wybierając interpretację,
  która realnie ląduje blisko szukanej miejscowości. **Nie zastępuj tego
  jedną, zahardkodowaną konwencją** — to naprawdę się różni.
- **WFS 2.0 używa `typenames` (liczba mnoga), WFS 1.1.0 używa `typename`**
  (liczba pojedyncza) — rejestr zawiera pole `version`, kod dobiera
  właściwy parametr.
- **Poszczególne serwery powiatowe bywają wolne/niedostępne** — to normalne
  dla systemu 380 niezależnych serwerów rządowych, nie traktuj tego jako
  błąd do naprawienia. Kod ma ponawianie (`_uldk_get_by_xy_raw`) dla
  krótkich, przejściowych awarii.
- **Rejestr może mieć luki** — powiat limanowski (1207) był pierwotnie
  pominięty (bo w momencie tworzenia tabeli społecznościowej ten akurat
  serwer nie działał) — dodaliśmy go ręcznie po weryfikacji na żywo. Jeśli
  trafisz na podobny przypadek (komunikat „ten powiat nie jest jeszcze w
  naszym rejestrze"), **sprawdź czy serwer działa teraz** (szukaj po nazwie
  powiatu + "webewid.pl" albo "geoportal2.pl" — to dwaj najczęstsi
  dostawcy oprogramowania) i dodaj wpis do JSON, tak jak zrobiliśmy dla
  limanowskiego.

### ⚠️ NIE próbuj ponownie tych ścieżek dla danych masowych (sprawdzone, ślepe zaułki)
- `EZiUDP` (`integracja.gugik.gov.pl/eziudp`) — legacy formularz PHP
  wymagający sesji przeglądarki/JS, nie da się tego zeskryptować
  bezpośrednio przez `curl`
- `walidator.gugik.gov.pl` — istnieje, ale to narzędzie do walidacji
  POJEDYNCZEGO, już znanego adresu URL, nie do odkrywania nowych

---

## 5. Wyszukiwarka Działek — pełna specyfikacja

Prosta, statyczna appka (jeden plik `index.html` ze wszystkim w środku —
HTML+CSS+JS). Brak backendu — wszystko dzieje się w przeglądarce
użytkowniczki, budując linki do zewnętrznych portali.

### Struktura repo (od 2026-09-01)
```
index.html                 — cały HTML+CSS+JS (REGIONS, PORTALS, logika)
service-worker.js           — CACHE_NAME="wyszukiwarka-dzialek-v1", network-first
                               (ten sam wzorzec co w analiza-dzialki — patrz sekcja 3)
manifest.json, icons/       — istniały już wcześniej
README.md
.github/workflows/ci.yml    — sprawdza składnię JS w <script>, service-worker.js, manifest.json
                               ⚠️ patrz niżej: realny błąd YAML, który tu był, i jak go znaleźć
```

### CI był czerwony przez 2 dni (2026-09-01 do 2026-09-03) — prawdziwa przyczyna
Trzy kolejne pushe do PR-a (`3d28299`, `926eab6`, `de6bd97`) dawały ten sam,
mylący objaw: workflow kończył się `failure`, **0 uruchomionych jobów**,
brak logów, GitHub odrzucał nawet ręczne ponowienie (`rerun_workflow_run`
→ `403 This workflow run cannot be retried`). To wyglądało jak problem z
uprawnieniami/ustawieniami repo (i tak to pierwotnie zdiagnozowano — błędnie),
ale Klaudia potwierdziła, że „Actions permissions" było już ustawione na
„Allow all actions and reusable workflows".

**Prawdziwa przyczyna**: realny błąd składni YAML w `ci.yml`, linia 43.
Krok „Waliduj manifest.json" miał jednoliniowe `run: node -e '...'` (bez
bloku `|`), a treść tej komendy zawierała `"OK: manifest.json..."` —
sekwencję **dwukropek+spacja** wewnątrz zwykłego (nieblokowego) skalara
YAML. To jest zabronione w specyfikacji YAML (koliduje ze składnią
`klucz: wartość`) i GitHub odrzuca cały plik workflow jako nieprawidłowy —
stąd 0 jobów (GitHub nie mógł nawet sparsować pliku, więc nie było czego
uruchomić) i błąd nie do ponowienia (nigdy nie było poprawnego runa do
powtórzenia).

**Jak to w końcu znaleziono**: `python3 -c "import yaml; yaml.safe_load(...)"`
i lokalne `node -e` na samej treści komend nie łapały tego (sprawdzały
tylko JS wewnątrz, nie strukturę samego YAML-a) — Python's PyYAML też
parsował plik "poprawnie" dopóki błąd nie został naprawiony (po naprawie
sparsował się bez błędu). **Jedyne miejsce, gdzie błąd był faktycznie
widoczny, to strona pojedynczego runu na GitHub** (Actions → konkretny
run → sekcja „Annotations"), NIE lista runów, NIE żadne API użyte tutaj
(`list_workflow_runs`, `list_workflow_jobs`, `get_workflow` — żadne z nich
nie zwróciło komunikatu błędu, tylko suchy status "failure"). Klaudia
znalazła to dopiero po otwarciu konkretnego runu na telefonie i rozwinięciu
„Show less/more" przy sekcji Annotations.

**Wniosek na przyszłość**: jeśli GitHub Actions daje `failure` z **0 jobami**
i nie da się ponowić — to prawie na pewno błąd składni w samym pliku
`.yml`, nie problem z uprawnieniami repo. Sprawdzaj **najpierw** stronę
pojedynczego runu (sekcja Annotations), zanim zaczniesz podejrzewać
ustawienia konta/repo. I waliduj YAML lokalnie przez faktyczny parser YAML
(`python3 -c "import yaml; yaml.safe_load(open('plik.yml'))"`), nie tylko
przez wyciąganie i testowanie samej treści komend `run:`.

### Funkcje
- **26+9=35 konkretnych miejscowości górskich**, pogrupowanych w 9
  regionów (Tatry, Podhale, Beskid Żywiecki/Mały, Beskidy, Beskid
  Makowski/Wyspowy przez powiat myślenicki, Beskid Wyspowy, Bieszczady,
  Sudety) + całe województwa jako opcja szersza
- **Formularz filtrów**: rodzaj (działka/dom), region, cena od/do,
  powierzchnia od/do → generuje **5 linków naraz** (Otodom, OLX, Domiporta,
  Nieruchomości-online, GetHome) — każdy z osobnym, zweryfikowanym formatem
  adresu URL tego portalu. **Od 2026-09-01 skonfigurowane deklaratywnie**:
  jedna tablica `PORTALS` (`{id, label, buildUrl(filters)}`) w `index.html`,
  renderowana jedną pętlą — wcześniej było to 5 osobnych funkcji
  (`buildOtodomUrl` itd.) plus osobny ręczny render dla każdej. **Dodanie
  6. portalu = dopisanie jednego obiektu do `PORTALS`, nic więcej.**
  Refaktor zweryfikowany przez porównanie bajt-po-bajcie 300 wygenerowanych
  URL-i (60 kombinacji filtrów × 5 portali) między starą a nową wersją —
  zero różnic.
- **OLX zawsze sortuje od najnowszych** (`search[order]=created_at:desc`
  — potwierdzone, że to realnie działa). **Otodom NIE ma parametru
  sortowania w adresie URL** mimo sprawdzenia wielu wariantów — to trzeba
  ustawić ręcznie po otwarciu strony
- **Zapisywanie wyszukiwań** w `localStorage` (nazwij, zapisz, wczytaj
  jednym kliknięciem, usuń) — to lokalne skróty w tej appce, nie
  prawdziwe alerty e-mail
- Link-out do ogólnej wyszukiwarki **Trovit** — ale **NIE agreguje
  Otodom/OLX** (sprawdzone: to metawyszukiwarka mniejszych/średnich
  portali). Od 2026-09-02 disclaimer pod przyciskiem Trovit mówi to
  wprost ("nie obejmuje Otodom ani OLX") — wcześniej było to tylko
  pośrednio sugerowane ogólnym opisem
- **Service worker (od 2026-09-01)**: appka jest teraz instalowalna z
  działającym trybem offline dla powłoki strony (index.html/manifest/ikony)
  — wcześniej był tylko `manifest.json` bez service workera. Ten sam wzorzec
  network-first co w Analiza Działki (żeby uniknąć problemu "appka nie widzi
  zmian" opisanego w sekcji 3).
- **Usunięta (2026-09-02) statyczna sekcja „Bezpośrednio na portalu — domy"**
  (4 linki: Otodom/OLX/Gratka/Morizon, bez żadnego filtra, tylko dla „domy").
  Była myląca: nagłówek appki obiecywał wyniki „z Otodom, OLX, Gratki,
  Morizon i innych", ale filtrowany formularz (region/cena/powierzchnia)
  nigdy nie obejmował Gratki ani Morizon — te dwa portale istniały wyłącznie
  jako niepowiązane, niefiltrowane linki na dole strony. Relikt z wczesnej
  wersji (commit „Dodaj wiecej portali (dzialki+domy)", 21 sierpnia), sprzed
  powstania właściwego mechanizmu `PORTALS`. Nagłówek appki poprawiony, żeby
  wymieniał tylko 5 portali, które appka realnie obsługuje przez filtry
  (Otodom, OLX, Domiporta, Nieruchomości-online, GetHome). **Jeśli
  kiedykolwiek zechcesz dodać Gratkę/Morizon z powrotem — rób to przez
  `PORTALS`, ze sprawdzonym na żywo formatem URL, tak jak resztę portali,
  nie jako osobną, niefiltrowaną sekcję.**

### ⚠️ Ślepe zaułki, których nie próbuj ponownie
- **Scraping Otodom/OLX** (własny albo przez płatne usługi typu Apify) —
  świadomie odrzucone, i regulaminowo (ToS obu portali wprost zabrania), i
  praktycznie (oba blokują iframe przez `X-Frame-Options: DENY/SAMEORIGIN`)
- **Oficjalne API OLX Group** (`developer.olxgroup.com`) — istnieje, ale to
  API do **wystawiania** ofert (dla biur nieruchomości/CRM), nie do
  wyszukiwania/pobierania cudzych ofert

---

## 6. Twarde, sprawdzone fakty o mapy.geoportal.gov.pl i polska.e-mapa.net

To były długie dochodzenia — nie powtarzaj ich, oto wynik:

- **`mapy.geoportal.gov.pl/imap/?identifyParcel=TERYT`** (stary viewer,
  ścieżka `/imap/`) — **DZIAŁA**. Potwierdzone: znaleziono realny,
  działający kod JS obsługujący ten parametr bezpośrednio na tej stronie
  (`checkParametersExist()` sprawdzający `identifyparcel` w URL).
- **`mapy.geoportal.gov.pl/imapnext/...`** (nowy viewer) — **NIE
  obsługuje** `identifyParcel`. Sprawdzone bezpośrednio w ich aktualnym
  kodzie JS (`main.js`) — parametr się tam w ogóle nie pojawia. To martwy,
  przestarzały parametr dla tej wersji.
- **`polska.e-mapa.net?identifyParcel=TERYT`** — **DZIAŁA**, potwierdzone
  przez zrzut ekranu Klaudii z ich własnej funkcji „skopiuj link do
  widoku z zaznaczoną działką". To osobna platforma (Geo-System), nie
  GUGiK.
- **Wyszukiwarka działek na `polska.e-mapa.net`** (pole tekstowe w ich
  interfejsie) działa **wyłącznie przez AJAX POST** (`pandora.ajax.post`)
  — nie da się tego zastąpić linkiem GET. Sprawdzone bezpośrednio w ich
  pliku `AppSzukaj.js`.

---

## 7. Znane luki / rzeczy do ewentualnego dociągnięcia

**Rozwiązane 2026-09-01** (były tu opisane jako otwarte luki — zostawione
dla historii, w razie regresji):
- ~~Folder `static/icons/` brakuje~~ — dodane (5 plików, wygenerowane
  programowo przez PIL/Pillow: prosty, płaski design działki geodezyjnej +
  pinezka, w kolorze motywu appki). Zweryfikowane: wszystkie ścieżki z
  `manifest.json`/`index.html`/`service-worker.js` zwracają 200. To wciąż
  placeholder, nie finalne branding — jeśli Klaudia zechce własne logo,
  podmień pliki w `static/icons/` (te same nazwy/rozmiary).
- ~~Brak logowania w `main.py`~~ — dodane (patrz sekcja 3, "Logging").
- ~~Timeouty rozrzucone i niespójne, brak retry dla WFS~~ — scentralizowane
  + dodany retry (patrz sekcja 3, "Timeouty i retry").
- ~~`main.py` to jeden plik, ~1720 linii bez podziału na moduły~~ —
  podzielony na `config.py`/`geo_utils.py`/`http_utils.py`/`services/*`
  (patrz sekcja 3, "Struktura repo").
- ~~Brak testów automatycznych i CI/CD w obu repo~~ — dodane: pytest w
  `analiza-dzialki` (30 testów logiki bez sieci) + GitHub Actions CI w
  obu repo (py_compile/pytest/import w analiza-dzialki; składnia JS +
  walidacja JSON w wyszukiwarka-dzialek). `nodemods/`+jsdom z metodologii
  (sekcja 8, punkt 3) wciąż nie istnieje jako stały folder w repo — jsdom
  był użyty ad-hoc do weryfikacji refaktoru portali w
  wyszukiwarka-dzialek, poza repo, nie zostawiony na stałe.
- ~~5 osobnych funkcji budowania URL portali w wyszukiwarka-dzialek~~ —
  zrefaktorowane na jedną tablicę `PORTALS` (patrz sekcja 5).
- ~~Brak service workera w wyszukiwarka-dzialek~~ — dodany.
- ~~Zależności w `requirements.txt` sprzed ~2 lat~~ — zaktualizowane
  (fastapi 0.115→0.128, uvicorn 0.30→0.34, httpx 0.27→0.28, shapely
  2.0.6→2.1.2, pyproj 3.6→3.7, beautifulsoup4 4.12→4.13, pillow
  10.4→11.3). Świadomie NIE do bezwzględnie najnowszych wersji (np.
  pillow 12.x, fastapi 0.141.x istniały w momencie aktualizacji) — wybrano
  bardziej zachowawczy skok, bo tego refaktoru nie dało się przetestować
  end-to-end na żywo w sandboxie bez dostępu do rządowych API (patrz niżej).
  Zweryfikowane: `pip install` bez konfliktów, `import main` się udaje,
  serwer startuje i odpowiada na `/`, `/openapi.json`, `/api/*` (walidacja
  400, nie testowano faktycznych wywołań zewnętrznych API).

**Wciąż otwarte:**
- **Powiat limanowski (1207)** był ręcznie dodany do rejestru WFS — jeśli
  pojawią się kolejne brakujące powiaty, wzorzec postępowania jest opisany
  w sekcji 4.
- **KIEG (ewidencja gruntów, szczegółowe dane)** wciąż bywa niekompletna
  dla wielu działek — to strukturalne ograniczenie tej usługi rządowej,
  nie coś do naprawienia w kodzie.
- **Rejestr Urbanistyczny (nowy system planów)** wystartował 1 lipca 2026 i
  był (w momencie tworzenia tej integracji) prawie pusty ogólnokrajowo —
  będzie się automatycznie wypełniał w miarę wgrywania danych przez gminy,
  bez potrzeby zmian w kodzie.
- **⚠️ Refaktor main.py (podział na moduły) i bump zależności NIE były
  przetestowane end-to-end na żywo** — sandbox, w którym je zrobiono, nie
  miał dostępu do ULDK/WFS/Overpass/ISOK/PIG-PIB (proxy blokuje te domeny,
  potwierdzone: `uldk.gugik.gov.pl` → 403 od proxy). Weryfikacja była
  ograniczona do: `py_compile`, `pyflakes` (zero ostrzeżeń), import,
  identyczny zestaw tras w `/openapi.json` przed/po, boot serwera + smoke
  test endpointów (walidacja błędów 400/404, statyczne pliki), symulacja
  dokładnego zestawu plików kopiowanych przez Dockerfile, i 30 testów
  pytest dla logiki bez sieci. **Po pierwszym deployu na Render sprawdź
  ręcznie na żywo choć jedną prawdziwą analizę działki i jedno wyszukiwanie
  "Szukaj działki"** — to jedyna rzecz, której nie dało się zweryfikować
  przed pushem.

---

## 8. Metodologia pracy — trzymaj się tego wzorca

Cała ta appka była budowana z bardzo wysokim naciskiem na **weryfikację na
żywo, nie zakładanie**. Trzymaj się tego:

1. **Zawsze testuj bezpośrednio przez `curl`** zanim zaimplementujesz coś w
   kodzie opartym na zewnętrznym API — nie zgaduj formatu parametrów.
2. **Po każdej zmianie kodu** — sprawdź składnię (`python3 -m py_compile`
   / `node -c`), potem uruchom lokalnie i przetestuj żywym zapytaniem.
3. **Do testowania frontendu** używaj `jsdom` (instalacja:
   `npm install jsdom` w osobnym folderze `nodemods/`) — symuluj kliknięcia
   i sprawdzaj wynikowe DOM, nie zgaduj czy JS zadziała.
4. **Po wgraniu na GitHub — zawsze `diff` między lokalnym plikiem a tym
   pobranym z powrotem z GitHub** — potwierdzenie bajt-po-bajcie.
5. **Po deployu — poczekaj i sprawdź na żywo** na prawdziwym, wdrożonym
   adresie, nie tylko lokalnie. Render potrzebuje ~60-90s.
6. **Jeśli appka "nie widzi" zmian mimo poprawnego push** — sprawdź po
   kolei: (a) czy Dockerfile kopiuje wszystkie nowe pliki (to był realny,
   znaleziony błąd — brakujący `COPY` dla nowego pliku JSON powodował
   cichą awarię startu i Render zostawiał starą wersję), (b) czy
   zbindowano nowy numer w `?v=N` dla `app.js`, (c) czy przeglądarka
   użytkowniczki ma starą, zbuforowaną wersję (poproś o zamknięcie i
   ponowne otwarcie appki, albo wyczyszczenie danych strony).
7. **Nie zakładaj, że coś jest zepsute po jednej próbie** — wiele serwerów
   rządowych (i poszczególne serwery WFS powiatów) miewa przejściowe,
   kilkusekundowe/kilkuminutowe awarie. Odróżniaj to od trwałych awarii
   (sprawdzaj wielokrotnie, w odstępach czasu, zanim uznasz coś za martwe
   na stałe).
8. **Ten dokument (`HANDOFF.md`) aktualizuj razem z kodem, nie osobno** —
   patrz notatka na samej górze pliku. Jeśli podczas pracy odkryjesz nowy
   fakt (usługa działa/nie działa, nowy ślepy zaułek, nowa luka), dopisz go
   od razu, w tym samym commit-cie.
9. **`TEST_PARCELS.md`** (root repo, dodane 2026-09-04) — lista realnych
   działek testowych, które Klaudia podaje do zapamiętania (numer TERYT +
   dlaczego dana działka jest przydatna do testów). Kiedy Klaudia pyta o
   „działkę testową" — to jest ten plik. Kiedy podaje nową — dopisz ją
   tam, nie tutaj.

---

## 9. Co jest ważne dla Klaudii (styl współpracy)

- Klaudia oczekuje, że **sam/sama znajdziesz, zdiagnozujesz i wdrożysz**
  poprawki — minimalnie angażując ją w kroki pośrednie.
- **Nie dodawaj elementów UI, które nie działają w pełni** — kilkukrotnie
  odrzucono pomysł dodania czegoś "na pokaz", jeśli nie dawało się tego
  w pełni zweryfikować.
- Klaudia ceni **szczerość o ograniczeniach** — jeśli coś nie działa albo
  nie da się zweryfikować, powiedz to wprost, zaproponuj opcje (przez
  `ask_user_input_v0`), nie udawaj że jest lepiej niż jest.
- Deployment (wgrywanie na GitHub) rób **samodzielnie przez API**, gdy
  masz token — nie proś Klaudii o ręczne wgrywanie plików przez interfejs
  GitHub, chyba że nie masz tokenu.
