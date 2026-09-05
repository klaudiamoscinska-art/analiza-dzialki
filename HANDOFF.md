# Handoff — Analiza Działki / Wyszukiwarka Działek

Dokument dla kolejnej instancji Claude przejmującej pracę nad tymi dwiema
aplikacjami. Cel: żebyś mógł/mogła działać dalej z Klaudią bez zadawania jej
pytań, na które odpowiedź jest już tutaj.

**Ten plik istnieje TYLKO w `analiza-dzialki`** (od 2026-09-05 — wcześniej
była tu identyczna kopia w `wyszukiwarka-dzialek`, usunięta na prośbę
Klaudii, żeby nie dublować pracy). Nie twórz go ponownie w
`wyszukiwarka-dzialek`. Aktualizuj ten plik **w tym samym commicie/PR co
zmiana kodu**, którą opisuje — nie osobno, nie "później". Trzymaj wpisy
**krótkie i faktyczne**: co jest prawdą teraz, jaki jest nieoczywisty
gotcha/pułapka, co jest świadomie odłożone. Nie pisz kroniki „Klaudia
zgłosiła X, zdiagnozowałem Y, okazało się że Z" — sama historia dochodzenia
nie ma wartości dla kolejnej sesji, liczy się wynik.

---

## 0. Zacznij tutaj — stan projektu na 2026-09-05

### Co działa (nie trzeba robić od nowa)

**Analiza Działki — backend FastAPI**, 12 sekcji analizy liczonych
równolegle (z limitem współbieżności, patrz niżej), plus osobny endpoint
wyszukiwania po miejscowości/rozmiarze. Pełny opis architektury, wszystkich
usług i znanych pułapek — sekcja 3.

**Wyszukiwarka Działek — statyczny frontend** (brak backendu): tablica
`PORTALS` (5 portali z działającymi filtrami), service worker, CI. Sekcja 5.

### Otwarte tematy (czekają na decyzję/polecenie Klaudii)

1. **MPZP `ConnectTimeout` dla niektórych gmin** (np. Korbielów/Jeleśnia) —
   prawdopodobnie realna niestabilność backendu TEJ gminy za krajowym
   agregatorem KIMPZP, nie coś naprawialne po stronie appki. Wyczerpane:
   IPv4, retry, limit współbieżności. Jedyna pozostała droga: bezpośredni
   WMS dostawcy gminy (epodgik.pl), osobny, większy projekt — patrz sekcja
   3, „Plan zagospodarowania (MPZP/KIAPP)".
2. **Status planu ogólnego jako osobny sygnał** — odłożone do 30.09/30.11.2026
   (dane strukturalne w KIAPP mają się wtedy pojawić bez zmian w kodzie).
3. **Realna wycena z transakcji RCN** zamiast stałej średniej GUS — brak
   potwierdzonego, publicznego, bezuwierzytelnieniowego źródła danych.
   Nie zgadywać URL-a bez potwierdzenia.
4. **WSTRZYMANE, nie zaczynaj bez wyraźnej prośby Klaudii**: zasięg sieci
   komórkowej (UKE), skala Bortle (hałas/światło) — najpierw research czy
   istnieje darmowe, otwarte API.
5. **Nadal nie zrobione**: przycisk „odśwież teraz" (pomijanie cache'u na
   żądanie), cache dla identyfikacji ULDK, dysk trwały na Render,
   uproszczenie 3 niemal identycznych funkcji geokodujących w
   `services/geocoding.py`.

### Środowisko tej sesji — ograniczenia, które się nie zmienią

- **Sieć rządowa (ULDK/WFS/Overpass/ISOK/PIG-PIB/GIOŚ/GDOŚ/Render) jest
  CAŁKOWICIE zablokowana z tego środowiska** — `WebFetch` też, na WSZYSTKIE
  domeny (potwierdzone nawet dla en.wikipedia.org). Nie trać na to czasu —
  testuj logiką (pytest + monkeypatch/fake HTTP client), do researchu użyj
  `WebSearch`. Żadnej integracji z zewnętrznym API nie da się zweryfikować
  na żywo stąd — patrz metodologia, sekcja 8.
- 118 testów pytest, `pip install -r requirements-dev.txt && pytest -q`.

---

## 1. Dwie osobne aplikacje, dwa osobne repozytoria

| | **Analiza Działki** | **Wyszukiwarka Działek** |
|---|---|---|
| Cel | Pełna analiza jednej działki (ewidencja, zagrożenia, media, hydrologia, plany, wycena) + wyszukiwanie po miejscowości/rozmiarze | Szybki dostęp do ofert sprzedaży (linki do portali z filtrami) |
| Repo | `github.com/klaudiamoscinska-art/analiza-dzialki` | `github.com/klaudiamoscinska-art/wyszukiwarka-dzialek` |
| Live URL | `https://analiza-dzialki.onrender.com` | `https://klaudiamoscinska-art.github.io/wyszukiwarka-dzialek/` |
| Hosting | Render.com (Docker, FastAPI, plan darmowy) | GitHub Pages (statyczny HTML) |
| Backend | Tak — Python/FastAPI | Nie — czysty HTML/CSS/JS |
| Deploy | Automatyczny po pushu do `main`, ~60-90s | Automatyczny po pushu do `main`, ~30-60s |

Całkowicie niezależne — osobne repo, osobne tokeny, osobny deploy.

---

## 2. Jak uzyskać dostęp do GitHub

Klaudia nie przechowuje tokenów między sesjami — generuj nowy na początku
każdej rozmowy, w której trzeba coś wgrać.

**Link do tworzenia tokenu**: `github.com/settings/personal-access-tokens/new`

Poproś Klaudię o token: **Repository access** → tylko repo, nad którym
pracujesz; **Permissions → Contents** → „Read and write"; **Expiration** →
7 dni. Po pracy przypomnij o usunięciu tokenu
(`github.com/settings/personal-access-tokens`) — **nigdy nie proponuj
"zapamiętania" tokenu na stałe**, to świadomy wybór Klaudii.

**Mechanika** (jeśli nie masz natywnego narzędzia git/PR): GitHub Contents
API przez `curl` — `GET .../contents/{path}` po `sha`, `PUT
.../contents/{path}` z treścią base64 + tym `sha`. **Zawsze zweryfikuj po
wgraniu** (pobierz z powrotem, `diff`) i **poczekaj + sprawdź na żywo** na
wdrożonym adresie — push ≠ appka już działa.

---

## 3. Analiza Działki — specyfikacja techniczna

### Struktura repo
```
main.py                      — FastAPI app, middleware, 4 trasy HTTP, orkiestracja
config.py                     — stałe: URL-e usług, warstwy, timeouty, tabele cen
geo_utils.py                  — parsowanie/pomiary geometrii, czyszczenie tekstu
http_utils.py                 — generyczny retry, wyścig+retry Overpass, WMS GetFeatureInfo
services/
  uldk.py                      — lookupy ULDK (po punkcie, po ID/nr, skan obrębów)
  geocoding.py                 — geokodowanie GUGiK (adresy, gminy, powiaty)
  wfs_search.py                 — "Szukaj działki": rejestr WFS, enumeracja+dopasowanie
                                   osi, search_parcels_universal (najbardziej złożona część)
  cadastre.py                   — KIEG + budynki (OSM)
  hazards.py                    — osuwiska (SOPO), powódź (ISOK), podtopienia (PIG-PIB)
  utilities.py                  — media/GESUT (KIUT)
  nearby_features.py            — droga gminna + cieki (Overpass)
  zoning.py                     — plany zagospodarowania (KIAPP/KIMPZP)
  nature.py                     — obszary chronione (GDOŚ)
  geology.py                    — tereny górnicze (PIG-PIB MIDAS)
  air_quality.py                — jakość powietrza (GIOŚ)
  verdict.py                    — syntetyczny werdykt/checklista
  due_diligence.py              — 25-punktowa lista kroków przed zakupem
  valuation.py                  — linki (GUNB/geoportal/e-mapa/EKW) + wycena statystyczna
  cache.py                      — cache-aside SQLite dla sekcji wzbogacających
tests/test_pure_logic.py     — pytest, logika bez sieci (patrz sekcja 0)
requirements.txt / requirements-dev.txt
.github/workflows/ci.yml     — py_compile + pytest + import + walidacja JSON/JS
Dockerfile                    — MUSI kopiować main.py/config.py/geo_utils.py/http_utils.py/
                                 services/, wfs_powiat_registry.json, static/ — **sprawdź to
                                 PIERWSZE przy nowym pliku .py na topie repo albo w services/**,
                                 brakujący COPY = cicha awaria startu na Render
wfs_powiat_registry.json     — rejestr 380 serwerów WFS per powiat (ścieżka liczona
                                 względem project root, NIE services/)
static/
  index.html / app.js         — cały frontend (jeden plik JS)
  manifest.json / service-worker.js — CACHE_NAME, network-first, fetch(..., {cache:"no-store"})
  icons/                       — wygenerowane programowo (PIL), placeholder branding
```

### Testy, logging, timeouty
- `tests/test_pure_logic.py` (118 testów) — logika bez zależności
  sieciowych: parsowanie geometrii, dopasowanie/ranking w
  `search_parcels_universal`, rejestr WFS, retry, cache, verdict, itd.
  Wzorzec: fake HTTP client / monkeypatch, zero prawdziwych requestów.
  **Nie testują** samych wywołań do usług rządowych.
- Każdy moduł ma `logger = logging.getLogger("analiza_dzialki")`
  (zdefiniowany w `config.py`) i loguje `logger.warning(...)` przy awarii
  zewnętrznej usługi — sprawdzaj logi Render, nie tylko odpowiedź API.
- Timeouty to nazwane stałe w `config.py` (`TIMEOUT_*`) — WFS powiatowe
  (najbardziej zawodne) i większość innych usług mają retry przez
  `http_utils._get_with_retry()` (1 dodatkowa próba, 2s odstępu, retry na
  `TimeoutException`/`TransportError`/5xx, NIE na 4xx).

### Cache-busting — rób to przy KAŻDEJ zmianie `static/`
`index.html` ładuje `<script src="/static/app.js?v=N">` — podbij `N` przy
każdej zmianie `app.js`. Backend ma middleware `Cache-Control: no-store` na
`/api/*` (osobna warstwa od cache'u JS). **Trzeci poziom**: PWA/service
worker na telefonie może pokazywać starą wersję nawet przy poprawnym `?v=N`
— `service-worker.js` woła `fetch(..., {cache:"no-store"})` i musi dostać
podbity `CACHE_NAME` przy KAŻDEJ zmianie `index.html`/`app.js`/`manifest.json`,
inaczej iOS Safari (PWA z ekranu głównego) rzadko sprawdza, czy SW się
zmienił. Jeśli mimo to appka na telefonie pokazuje starą wersję: to nie
błąd kodu — poproś o pełne zamknięcie i ponowne otwarcie appki, w
ostateczności usunięcie i dodanie ikonki na nowo.

### Endpointy API
- `GET /api/resolve?query=...` — "Miejscowość numer" albo pełny TERYT.
  Zwraca `{resolved: true, teryt_id}` albo `{resolved: false, candidates}`.
  Kaskada do 5 etapów (bezpośrednie ULDK → geokodowanie gminy + skan ID →
  skan geometrii WFS → warianty numerowanych obrębów → geokodowanie powiatu
  + skan każdej gminy) — owinięta w `TIMEOUT_RESOLVE_BUDGET=50s`
  (`asyncio.wait_for`), więc appka sama zwraca czytelny błąd PO POLSKU
  zamiast crashować przez limit czasu proxy Render (który zwraca HTML
  zamiast JSON — frontendowy `resp.json()` rzucał wtedy surowy,
  nieprzetłumaczony `SyntaxError`).
- `GET /api/resolve-address?query=...` — adres (ulica+numer), geokodowanie
  GUGiK + `GetParcelByXY`.
- `GET /api/analyze?parcel_id=...` — pełna analiza w jednym JSON-ie.
- `GET /api/analyze-stream?parcel_id=...` — strumieniowa (SSE) alternatywa,
  używana przez frontend. `event: meta` (tożsamość/geometria/linki, zawsze
  pierwszy fragment) → `event: section` (`{"key", "value"}` per gałąź, w
  kolejności ukończenia, `asyncio.as_completed`) → `event: done`
  (`verdict`/`due_diligence`/`valuation`, potrzebują kompletu wyników).
  `_section_specs()`/`_compute_derived()` współdzielone z `/api/analyze`,
  żeby dwie kopie tej samej logiki się nie rozjechały. Lookup ULDK
  (może rzucić 404/502) wykonywany PRZED `StreamingResponse` — po
  rozpoczęciu strumienia nagłówki 200 już poszły, więc pojedyncza sekcja,
  która nieoczekiwanie rzuci, staje się wierszem `status: "error"` zamiast
  wywalać cały strumień. Frontend: własny parser SSE przez
  `fetch()`+`reader.read()`, NIE `EventSource` (nie daje dostępu do treści
  błędu 400/404).
- `GET /api/search-by-parcel-size?place=&area_m2=&width_m=&length_m=&dims_as_maximum=`
  — patrz sekcja 3.2.

### Współbieżność i wydajność
- **Jeden trwały `httpx.AsyncClient`** (`main.py::_get_http_client()`) na
  cały czas życia serwera (FastAPI `lifespan`) zamiast nowego klienta na
  każde żądanie.
- **`asyncio.Semaphore(MAX_CONCURRENT_SECTIONS=5)`** (`config.py`,
  `main.py::_section_specs()`) — tylko tyle z 12 gałęzi analizy naraz
  faktycznie robi swój fetch, reszta czeka w kolejce. Jeden semafor na
  request, nie globalny. Dodane po realnym problemie: 12 równoległych
  zapytań na jednowątkowym Render (plan darmowy) potrafiło przeciążyć
  proces na tyle, że NIEZWIĄZANE usługi (MPZP i Overpass w tej samej
  analizie) dostawały timeout blisko granicy WŁASNEGO limitu.
- **`asyncio.to_thread()`** dla CPU-bound pracy, która inaczej blokowałaby
  event loop: skan pikseli WMS w `zoning.py` (do 22500 px) i
  `utilities.py` (do ~345600 px łącznie na 6 warstw — najcięższy pojedynczy
  fragment CPU w appce), oraz `resp.json()` dla dużych odpowiedzi Overpass.
- **Cache-aside w SQLite** (`services/cache.py`, `get_or_fetch(service,
  key, ttl_seconds, fetch)`, klucz = `teryt_id`) dla WSZYSTKICH sekcji
  wzbogacających. Leniwy, NIE cykliczny poller (przestrzeń kluczy jest
  ogromna i długoogonowa — poller marnowałby pracę na działki, których
  nikt już nie odwiedzi). Cache'uje WYŁĄCZNIE `status: "ok"` — błąd/timeout
  nigdy się nie zamraża na cały TTL. Każdy wynik ma `cached`/`fetched_at`,
  pokazywane w UI jako „dane z: [data]" (`dataAgeNote()` w `app.js`) — to
  NIE opcjonalny dodatek, tylko odpowiedź na „skąd pewność, że dane są
  aktualne". TTL 30-180 dni dla większości usług (mapy geologiczne/
  hydrologiczne aktualizują się rzadko), wyjątki: budynki/OSM 14 dni, GIOŚ
  1h (odczyty faktycznie zmieniają się co godzinę), MPZP 7 dni (jedyna
  sekcja z realną wagą decyzyjną — gmina może uchwalić nowy plan w trakcie
  czyjejś decyzji o zakupie). **Świadomie NIE cache'owane**: identyfikacja
  działki (ULDK, dana „tożsamościowa"). Brak dysku trwałego na Render —
  SQLite resetuje się przy każdym deployu (nadal pomaga w ciągu jednego
  dnia/wielu odwiedzin tej samej działki).
- **`_conn_lock` (threading.RLock)** w `cache.py` serializuje dostęp do
  SQLite — naprawia dwa realne wyścigi wątków (tworzenie połączenia,
  współdzielone połączenie) znalezione przy 12 współbieżnych
  `get_or_fetch()` na świeżym `cache.db` (czyli stan po KAŻDYM deployu).

### Źródła danych per sekcja

| Sekcja | Usługa | Status/uwagi |
|---|---|---|
| Ewidencja (identyfikator, gmina, powiat, obręb) | ULDK `GetParcelById`/`GetParcelByIdOrNr` | Działa dobrze |
| Ewidencja (klasoużytki, szczegóły) | KIEG WMS `GetFeatureInfo` | Częściowo martwe dla wielu działek — strukturalne ograniczenie usługi |
| Budynki (obrys, bez atrybutów) | OpenStreetMap Overpass | KIEG/BDOT nie udostępniają atrybutów budynku przez żadne wolne API |
| Osuwiska | SOPO PIG-PIB, ArcGIS `identify` (NIE `GetFeatureInfo` — WAF) | Działa dobrze |
| Media/GESUT | KIUT, metoda pikselowa `GetMap` (GetFeatureInfo strukturalnie zepsute) | Przybliżenie wizualne + dystans w metrach; też jako warstwa mapy |
| Plan zagospodarowania | KIAPP (Rejestr Urbanistyczny) + KIMPZP (stary), równolegle | Patrz „Plan zagospodarowania" niżej |
| Hydrologia | ISOK/Wody Polskie WMS + PIG-PIB + Overpass (cieki) | Działa dobrze |
| Obszary chronione | GDOŚ WFS `sdi.gdos.gov.pl/wfs`, 6 `typeNames` | Retry + obsługa "200 OK z pustym ciałem" |
| Tereny górnicze | PIG-PIB MIDAS, ArcGIS `identify` | Nazwa z pola `value` (protokół ArcGIS, nie zgadywany atrybut) |
| Jakość powietrza | GIOŚ REST `api.gios.gov.pl/pjp-api/v1/rest` | Patrz „Jakość powietrza" niżej |
| Pozwolenia na budowę | GUNB/RWDZ — tylko link-out, brak API (CAPTCHA) | Nie scrapować |
| Odległość do drogi gminnej | Overpass, przybliżenie `highway=unclassified/residential` | OSM nie ma pola „kategoria zarządzania drogą" |
| Wycena | Statyczna tabela cen GUS per województwo × powierzchnia | Czysto statystyczne, appka to komunikuje |
| Księga wieczysta | Link-out do przeglądarki EKW MS, bez numeru KW | Żadne źródło (ULDK/EGiB) nie zwraca numeru KW |
| Hałas | ŚWIADOMIE nie zintegrowane | Brak jednej krajowej usługi — GDDKiA/PKP/miasta osobno, statyczna notatka zamiast fałszywego "brak danych" |

**Plan zagospodarowania (MPZP/KIAPP)** — `services/zoning.py`:
- `KIMPZP_LAYERS = "raster,wektor-str,wektor-lzb,wektor-pow,wektor-lin,wektor-pkt"`
  (NIE `"plany"` — to nazwa ogólnej warstwy-grupy, która nie renderuje się
  niezawodnie; każda gmina publikuje TYLKO raster ALBO wektor).
  **Lekcja**: gdy WMS zwraca 200 i poprawny PNG, ale wizualnie nic nie
  pokazuje mimo że dane na pewno gdzieś są — sprawdź `GetCapabilities`
  (`?SERVICE=WMS&REQUEST=GetCapabilities`) pod kątem tego, czy używana
  nazwa warstwy to faktycznie renderowalna warstwa z treścią, nie tylko
  nazwa grupy/kontenera. `KIAPP_LAYERS = "app"` nie był tak sprawdzony.
- Oba źródła (KIAPP, KIMPZP) odpytywane RÓWNOLEGLE (`asyncio.gather`),
  KIAPP wygrywa gdy ma REALNY wynik (`status != "error"`, nie tylko
  `is not None` — błąd KIAPP potrafił wcześniej maskować udany wynik
  KIMPZP). Strategia: szybki `GetMap` jako sonda (`_mpzp_has_plan_drawn`,
  z retry przez `_get_with_retry`) przed wolnym/zawodnym
  `GetFeatureInfo` (`TIMEOUT_MPZP_DETAIL=20s`, wrapped w
  `asyncio.wait_for` — niektóre gminy wiszą NIESKOŃCZENIE na
  `GetFeatureInfo`).
- **Plan Ogólny / OUZ**: od 1.09.2026 brak MPZP nie oznacza już automatycznie
  możliwości uzyskania warunków zabudowy — trzeba leżeć w OUZ z planu
  ogólnego gminy. Appka NIE ma strukturalnego dostępu do schematu atrybutów
  KIAPP (niezweryfikowalne z tego środowiska) — `_mentions_any()` robi
  tylko wyszukiwanie słów kluczowych w tekście `GetFeatureInfo`
  (`mentions_plan_ogolny`/`mentions_ouz`), fałszywe negatywy akceptowalne,
  fałszywe pozytywy nie. Gdy `found: "no"` (nic nigdzie), appka dołącza
  `note` z wyjaśnieniem tej zasady. **Status planu ogólnego jako
  strukturalny, osobny sygnał** — odłożone: do 30.09.2026 projekty aktów
  są publikowane WYŁĄCZNIE w BIP każdej gminy osobno (nie w KIAPP), a
  scraping ~2477 stron BIP to dokładnie ten rodzaj rozwiązania, którego
  ten projekt unika. Wrócić do tematu po 30.09/30.11.2026.
- **`ConnectTimeout` dla niektórych, cięższych działek (potwierdzone na
  Korbielów 3917/5, gm. Jeleśnia)** — źle się to diagnozowało, więc pełna
  historia rozstrzygających testów: (1) wymuszenie IPv4 dla tego hosta
  (hipoteza: brak "Happy Eyeballs" w httpx) — **wypróbowane i wycofane**,
  identyczny błąd po wdrożeniu; (2) `asyncio.Semaphore`/`to_thread` (patrz
  wyżej) — **nie pomogło**, próba MPZP startowała natychmiast (bez
  kolejkowania) i mimo to timeout; (3) druga działka testowa (Zawoja) na
  TYM SAMYM kontenerze/IP, który przed chwilą zawiódł dla Korbielowa —
  **zadziałała**, co wyklucza reputację/blokadę adresu IP Render. Wniosek:
  prawdopodobnie realna niestabilność KONKRETNEGO backendu gminy Jeleśnia
  za krajowym agregatorem KIMPZP (ten agregator proxy'uje do ~380
  niezależnych systemów gminnych/powiatowych — część jest niesprawna,
  podobnie jak przy WFS EGiB, sekcja 4). **Jedyna pozostała droga**:
  bezpośredni WMS dostawcy gminy (Geo-System/epodgik.pl, potwierdzone
  przez Klaudię w DevTools na `polska.e-mapa.net` — zwraca ustrukturyzowane
  pola TERYT/Nazwa planu/Uchwała/Status w ~90ms) zamiast krajowego
  agregatora — wymaga rejestru gmina→dostawca→URL podobnego do
  `WFS_POWIAT_REGISTRY` (sekcja 4). Osobny, większy, NIE rozpoczęty
  projekt — nie zgadywać/hardkodować jednego adresu na próbę.

**Jakość powietrza (GIOŚ)** — `services/air_quality.py`:
- `/v1/` w URL jest OBOWIĄZKOWE — stary, nie-wersjonowany URL zwraca HTTP
  410 Gone (wycofany 30.06.2025).
- Brak endpointu „najbliższa stacja" — pobiera całą listę (`station/findAll`,
  paginowane po `totalPages`, cache pod stałym kluczem `"all"`, TTL 30 dni)
  i sam sortuje po odległości.
- ~42% stacji jest manualnych (bez danych na bieżąco) — próbuje do 5
  najbliższych kandydatów, `_find_pollutant_sensors()` (liczba mnoga)
  zwraca WSZYSTKIE czujniki stacji w kolejności preferencji (PM2.5→PM10),
  próbowane po kolei NA TEJ SAMEJ stacji przed przejściem do następnej
  (był błąd: porzucało stację na pierwszym niepowodzeniu PM2.5). Najnowszy
  wiersz często ma `"Wartość": null` — skanuje w przód do pierwszej
  niepustej wartości.
- TTL odczytów 1h (jedyny wyjątek od „cachuj agresywnie" reszty appki —
  dane faktycznie aktualizują się co godzinę). Surowy odczyt bez własnej
  oceny ryzyka zdrowotnego (zawsze tier `"ok"` w werdykcie). Wymagana
  widoczna atrybucja „Źródło danych: GIOŚ — EKOINFONET".

**Werdykt i lista kroków** — `services/verdict.py`/`services/due_diligence.py`:
- Deterministyczna punktacja (start 100): 40 osuwisko, 35 strefa zalewowa,
  20 brak drogi (5 fallback), 15 ryzyko podtopień / brak mediów (5 przy
  1-2 typach), 10 obszar chroniony / brak planu / kopalnia. Sekcja, która
  nie odpowiedziała, NIGDY nie obniża wyniku — osobny poziom `"unknown"`
  (obok `risk`/`warning`/`ok`), własny wiersz z plakietką „BRAK DANYCH",
  trafia też do `incomplete_sections`. Wynik → `dobra` (≥80) /
  `do_sprawdzenia` (50-79) / `wysokie_ryzyko` (<50).
- `rows` — pełna lista WSZYSTKICH sprawdzonych sygnałów (nie tylko
  problemów, jak wcześniej), z `counts` (`{risk, warning, ok, unknown}`).
  Frontend: zwarta lista jako spis treści (etykieta + pill, klik
  przewija do karty szczegółów), nie druga kopia treści kart.
- `due_diligence.py` — 25 kroków w 7 kategoriach, standardowa wiedza
  branżowa (nie kopiowana treść), auto-odhaczane z `covered` (sekcje ze
  statusem `"ok"`).

**Overpass** (`http_utils.py`): wszystkie mirrory odpytywane RÓWNOLEGLE
(`asyncio.wait FIRST_COMPLETED`, nie sekwencyjnie — sekwencyjne próby
oznaczały, że zablokowany/wolny mirror musiał wyczerpać swój PEŁNY timeout,
zanim drugi w ogóle był próbowany), plus 1 retry całego wyścigu, gdy OBA
zawiodą naraz (`_overpass_query_once` = wyścig, `_overpass_query` = wyścig
+ retry). `TIMEOUT_OVERPASS` musi przewyższać każdą dyrektywę `[timeout:N]`
wpisaną w treść zapytań (`services/nearby_features.py`) — kiedyś była tu
niespójność (klient 14s, zapytanie deklarowało serwerowi 25s), pilnowana
teraz testem parsującym faktyczne dyrektywy z pliku.

**Błędy z pustym komunikatem**: kilka wyjątków httpx (`ConnectTimeout`,
`ReadTimeout` bez argumentów) ma pusty `str()` — `http_utils.describe_exc(exc)`
(`str(exc) or type(exc).__name__`) używane wszędzie, gdzie wyjątek trafia
do komunikatu błędu, żeby "Usługa niedostępna: " nigdy nie zostawało bez
niczego po dwukropku.

**Wzorzec UI: rozwijane podkategorie warstw mapy** (`static/app.js`,
`addLayerGroupRow(container, label, subcategories)`) — używany przez GESUT
i Plany zagospodarowania. Własne `<div class="layer-group-row">` (checkbox
+ `<details>`) doklejane do kontrolki Leaflet, NIE wstrzykiwane do wnętrza
`L.control.layers` (ta się resetuje przy każdym kliknięciu i nie skaluje
się na więcej niż jedną grupę). Nadrzędny checkbox syncuje się z dziećmi
(`indeterminate` gdy część zaznaczona) przez WSPÓLNE instancje warstw
(`L.layerGroup` budowany z tych samych obiektów co podkategorie, nie osobna
instancja). Dwie pułapki CSS: `label{display:flex}` bez `:not([open])`
nadpisuje domyślne ukrywanie zamkniętego `<details>`; `.leaflet-control-layers`
potrzebuje `max-height`+`overflow-y:auto`, inaczej rozwinięcie obu grup
naraz ucina dolne checkboxy bez błędu.

**Budowa/przegląd kodu — poprawione błędy warte znajomości**:
`cache.py::get_or_fetch()` robił SYNCHRONICZNE SQLite bezpośrednio w
korutynie (blokowało event loop, serializując współbieżne requesty) —
odczyt/zapis teraz przez `asyncio.to_thread()`. `_get_with_retry()` nie
łapał 5xx (tylko `TimeoutException`/`TransportError`) — przeciążony WFS
zwracający 503 failował bez retry.

**Naprawione 2026-09-05**: szukanie po samej miejscowości zwracało działki
z sąsiedniej, większej gminy zamiast z wyszukiwanej (zgłoszone na żywo:
"Raciechowice" 1500m² → same wyniki z Dobczyc). Przyczyna: domyślny
`radius_m` w `enumerate_parcel_points_in_area()` został podbity 2→15km
2026-09-03 dla obsługi "Powiat X", ale gałąź powiatowa (`is_powiat_query`)
i tak dostała WŁASNY, jawny promień (10km/gminę) — zwykła gałąź (pojedyncza
miejscowość, `_gather_nearby_parcels`) nigdy nie przekazywała swojego
`radius_m`, więc po cichu dziedziczyła ten podbity default. 15km wokół
małej gminy sięga w większych sąsiadów obsługiwanych przez TEN SAM serwer
WFS powiatu; `max_features=500` (twardy limit, bez sortowania po
odległości) potrafił się wyczerpać wynikami sąsiada, zanim padła choć
jedna działka z docelowej miejscowości. Naprawa: przywrócony default 2km.
**Gotcha na przyszłość**: jeśli znów potrzebne będzie szukanie "całego
obszaru" większym promieniem, dawaj temu wywołaniu WŁASNY jawny `radius_m`
(tak jak `is_powiat_query` i `scan_wfs_for_parcel_number`) — nie podbijaj
współdzielonego defaultu funkcji.

### 3.2 „Szukaj działki" — wyszukiwanie po miejscowości + rozmiarze

Najbardziej złożona część appki. Pipeline: (1) geokodowanie miejscowości →
punkt (bierze MEDIANĘ wszystkich dopasowanych punktów, nie pierwszy —
sama nazwa wsi pasuje do dziesiątek adresów, pierwszy z brzegu dawał
niestabilne wyniki), (2) ustalenie powiatu (`find_parcel_by_xy`), (3)
wyszukanie WFS (sekcja 4), (4) rozwiązanie każdego kandydata przez
`GetParcelByXY` + prawdziwa powierzchnia geodezyjna i wymiary
(`minimum_rotated_rectangle`), (5) filtrowanie/sortowanie.

**Kryteria** (`search_parcels_universal`, dowolna kombinacja): powierzchnia
±10%, szerokość+długość razem ±10% każda (niezależne od kolejności), tryb
„maksimum" (`dims_as_maximum=true` — twardy sufit, nie tolerancja), **bez
limitu liczby wyników** (`max_results=None`). Jakość dopasowania: filtr
prostokątności (`min_rectangularity=0.65`, odrzuca L-kształty/trójkąty),
RMS zamiast średniej dla błędu wymiarów, krzyżowa weryfikacja iloczynem
wymiarów vs prawdziwa powierzchnia.

**Szukanie po całym powiecie** (np. "Powiat suski") — darmowy geokoder
GUGiK nie zna nazw powiatów, fallback na `geocode_powiat_gmina_prefixes()`
(pole `pow_nazwa`). Osobna gałąź (`is_powiat_query`) w
`_gather_nearby_parcels`: `enumerate_parcel_points_in_area` wywoływane
OSOBNO dla KAŻDEJ gminy w powiecie (współbieżnie, `asyncio.gather`,
10km promień per gmina — jeden okrąg wokół geograficznego środka powiatu
pokrywał tylko wąski wycinek), `max_features` skalowany
`max(50, 500 // liczba_gmin)`, żeby suma zostawała w bezpiecznym budżecie.

**Miniatura kształtu**: `geo_utils._polygon_outline_normalized()` — SVG
z geometrii już obliczonej (nic nowego z sieci), uproszczonej
(`shapely.simplify`) i znormalizowanej (north-up, dłuższy bok=64).

---

## 4. WFS EGiB — największa, najtrudniejsza część projektu

### Co jest zepsute (nie próbuj naprawiać)
Zbiorcza usługa WFS GUGiK
(`mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza`) ma
**trwałą awarię bazy danych** (`msPostGISLayerOpen(): Database connection
failed`, potwierdzone wielokrotnie) — awaria infrastruktury GUGiK, nie coś
do naprawienia po naszej stronie.

### Rozwiązanie: bezpośredni routing per powiat
Każdy z 380 powiatów prowadzi WŁASNY, niezależny serwer WFS. Zweryfikowana
lista (`geoinformatyka.com.pl/raporty/analiza_uslug_wfs.html`, oparta na
rejestrze GUGiK EZiU) sparsowana do `wfs_powiat_registry.json` — 380
wpisów: TERYT powiatu → `{url, version, layer}`.

### Praktyczne szczegóły
- **Kolejność osi EPSG:2180 różni się per serwer** (część zwraca
  northing/easting, część odwrotnie) — kod WYKRYWA to automatycznie,
  licząc odległość do znanego punktu kotwiczącego. Nie zastępuj tego jedną
  zahardkodowaną konwencją.
- WFS 2.0 używa `typenames` (mnoga), WFS 1.1.0 `typename` (pojedyncza) —
  rejestr ma pole `version`.
- Poszczególne serwery bywają wolne/niedostępne — normalne dla 380
  niezależnych serwerów rządowych, nie traktuj jako błąd do naprawy;
  `_get_with_retry` daje jedną powtórkę.
- Rejestr może mieć luki (powiat limanowski 1207 był ręcznie dodany) —
  jeśli trafisz na „ten powiat nie jest jeszcze w naszym rejestrze",
  sprawdź czy serwer działa teraz (szukaj nazwy powiatu + "webewid.pl"
  albo "geoportal2.pl" — dwaj najczęstsi dostawcy) i dodaj wpis.

### ⚠️ Sprawdzone ślepe zaułki — nie próbuj ponownie
- `EZiUDP` (`integracja.gugik.gov.pl/eziudp`) — legacy formularz PHP,
  wymaga sesji przeglądarki/JS, nie da się zeskryptować przez `curl`.
- `walidator.gugik.gov.pl` — tylko do walidacji POJEDYNCZEGO, już znanego
  URL-a, nie do odkrywania nowych.

---

## 5. Wyszukiwarka Działek — specyfikacja

Statyczna appka, jeden plik `index.html` (HTML+CSS+JS), brak backendu.

### Struktura
```
index.html                 — cały HTML+CSS+JS (REGIONS, PORTALS, logika)
service-worker.js           — network-first, ten sam wzorzec co analiza-dzialki
manifest.json, icons/
.github/workflows/ci.yml    — składnia JS + service-worker.js + manifest.json
```

### Funkcje
- 26+9 miejscowości górskich w 9 regionach + całe województwa.
- Formularz filtrów (rodzaj/region/cena/powierzchnia) → 5 linków naraz
  (Otodom, OLX, Domiporta, Nieruchomości-online, GetHome), każdy ze
  zweryfikowanym formatem URL. Konfiguracja deklaratywna: jedna tablica
  `PORTALS` (`{id, label, buildUrl(filters)}}`) — dodanie 6. portalu =
  jeden nowy obiekt, nic więcej.
- OLX sortuje `created_at:desc` w URL-u; **Otodom NIE ma parametru
  sortowania** (sprawdzone wielokrotnie) — trzeba ustawić ręcznie po
  otwarciu strony.
- Zapisywanie wyszukiwań w `localStorage` (lokalne skróty, nie alerty
  e-mail).
- Trovit — link-out, ale **NIE agreguje Otodom/OLX** (metawyszukiwarka
  mniejszych portali), disclaimer pod przyciskiem mówi to wprost.

### ⚠️ Sprawdzone ślepe zaułki
- Scraping Otodom/OLX — zabronione regulaminowo (ToS) i praktycznie
  (`X-Frame-Options: DENY/SAMEORIGIN`).
- Oficjalne API OLX Group (`developer.olxgroup.com`) — to API do
  WYSTAWIANIA ofert (biura nieruchomości/CRM), nie do wyszukiwania cudzych.

### CI: `failure` z 0 jobami i bez możliwości ponowienia → błąd składni YAML
Realny przypadek: `run: node -e '...'` w jednej linii z sekwencją
dwukropek+spacja wewnątrz nieblokowego skalara YAML — zabronione w
specyfikacji, GitHub odrzuca cały plik jako nieprawidłowy (stąd 0 jobów,
stąd niemożność ponowienia — nigdy nie było poprawnego runa). **Jedyne
miejsce, gdzie błąd był widoczny**: strona pojedynczego runu → sekcja
„Annotations" (NIE lista runów, NIE żadne API `list_workflow_runs`/
`get_workflow`). Waliduj YAML lokalnie faktycznym parserem
(`python3 -c "import yaml; yaml.safe_load(open('plik.yml'))"`), nie tylko
przez wyciąganie treści komend `run:`.

---

## 6. Twarde, sprawdzone fakty o mapy.geoportal.gov.pl i polska.e-mapa.net

- `mapy.geoportal.gov.pl/imap/?identifyParcel=TERYT` (stary viewer,
  `/imap/`) — **DZIAŁA** (potwierdzony JS `checkParametersExist()`).
- `mapy.geoportal.gov.pl/imapnext/...` (nowy viewer) — **NIE obsługuje**
  `identifyParcel` (parametr nieobecny w aktualnym `main.js`).
- `polska.e-mapa.net?identifyParcel=TERYT` — **DZIAŁA** (Geo-System, osobna
  platforma od GUGiK; potwierdzone zrzutem z ich funkcji „skopiuj link").
- Wyszukiwarka działek na `polska.e-mapa.net` działa WYŁĄCZNIE przez AJAX
  POST (`pandora.ajax.post`, plik `AppSzukaj.js`) — nie da się zastąpić
  linkiem GET.

---

## 7. Wciąż otwarte luki

- **KIEG** (szczegółowa ewidencja) bywa niekompletna dla wielu działek —
  strukturalne ograniczenie usługi rządowej, nie do naprawienia w kodzie.
- **Rejestr Urbanistyczny** wystartował 1.07.2026, ogólnokrajowo prawie
  pusty w momencie integracji — wypełni się sam w miarę wgrywania danych
  przez gminy, bez zmian w kodzie.
- **Nowe brakujące powiaty w rejestrze WFS** — wzorzec postępowania w
  sekcji 4.
- Pozostałe kategorie danych zbadane, ale NIE zaimplementowane (brak
  potwierdzonego publicznego źródła — nie zgadywać URL-i): azbest,
  osiadanie terenu (Copernicus EGMS), ryzyko Seveso, zasięg UKE, realne
  ceny transakcyjne RCN, spadki terenu przez NMT GUGiK
  (`services.gugik.gov.pl/nmt/` — istnienie potwierdzone, składnia
  zapytania nie).

---

## 8. Metodologia pracy

Ta appka była budowana z naciskiem na **weryfikację na żywo, nie
zakładanie**:

1. **Testuj bezpośrednio przez `curl`** zanim zaimplementujesz coś opartego
   na zewnętrznym API — nie zgaduj formatu parametrów. (W tym środowisku
   sieć rządowa jest zablokowana — użyj `WebSearch` do researchu, cytuj
   źródła, jasno oznacz co jest niepotwierdzone.)
2. **Po każdej zmianie kodu** — sprawdź składnię (`py_compile`/`node -c`),
   uruchom testy, przetestuj lokalnie.
3. **Po wgraniu na GitHub** — `diff` między lokalnym plikiem a pobranym z
   powrotem.
4. **Po deployu** — poczekaj (~60-90s) i sprawdź na żywo na prawdziwym
   adresie, nie tylko lokalnie.
5. **Jeśli appka "nie widzi" zmian mimo poprawnego push** — sprawdź po
   kolei: (a) czy Dockerfile kopiuje wszystkie nowe pliki, (b) czy
   podbito `?v=N`/`CACHE_NAME`, (c) czy to zbuforowana przeglądarka
   użytkowniczki (zamknięcie+ponowne otwarcie appki).
6. **Nie zakładaj, że coś jest trwale zepsute po jednej próbie** — serwery
   rządowe (zwłaszcza ~380 niezależnych WFS powiatowych) miewają
   przejściowe awarie. Odróżniaj to od trwałych (sprawdzaj wielokrotnie, w
   odstępach).
7. **`HANDOFF.md` aktualizuj razem z kodem, w tym samym commicie** — krótko
   i faktycznie (patrz notatka na górze pliku).
8. **`TEST_PARCELS.md`** (root repo) — lista realnych działek testowych z
   numerem TERYT i uzasadnieniem. Gdy Klaudia podaje nową działkę testową
   — dopisz ją tam, nie tutaj.

---

## 9. Co jest ważne dla Klaudii (styl współpracy)

- Oczekuje, że **sam/sama znajdziesz, zdiagnozujesz i wdrożysz** poprawki —
  minimalnie angażując ją w kroki pośrednie.
- **Nie dodawaj UI, które nie działa w pełni** — kilkukrotnie odrzucone
  jako "na pokaz", jeśli nie dało się w pełni zweryfikować.
- Ceni **szczerość o ograniczeniach** — jeśli coś nie działa/nie da się
  zweryfikować, powiedz to wprost, zaproponuj opcje, nie udawaj że jest
  lepiej niż jest.
- Deployment rób **samodzielnie przez API/git**, gdy masz dostęp — nie proś
  o ręczne wgrywanie przez interfejs GitHub, chyba że nie masz innej opcji.
- **Dokumentację (ten plik) trzymaj krótką** — to była świadoma decyzja
  2026-09-05 po tym, jak plik urósł do ~2400 linii kroniki rozwoju.
  Zapisuj wnioski i pułapki, nie proces ich znajdowania.
