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
`tests/test_pure_logic.py` — 40 testów dla logiki bez zależności sieciowych:
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
  (ewidencja, budynki, osuwiska, media, hydrologia, plany, pozwolenia,
  wycena, linki do map, `nearest_road`).
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
nie zwróciła. **⚠️ NIE zweryfikowane na żywo** (ULDK zablokowany w
sandboksie) — jeśli to też nie zadziała, prawdopodobnie ULDK po prostu
nie ma tej konkretnej działki zaindeksowanej (luka pokrycia po stronie
usługi, nie błąd kodu) — sprawdź to jako pierwsze, zanim szukasz dalej
w tym miejscu. 2 nowe testy pytest (`find_parcel_by_id_direct` z fałszywym
klientem HTTP — parsowanie udanej i nieudanej odpowiedzi).

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
