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
```
main.py                      — cały backend FastAPI
requirements.txt
Dockerfile                   — MUSI kopiować main.py, wfs_powiat_registry.json, static/
wfs_powiat_registry.json     — rejestr 380 bezpośrednich serwerów WFS per powiat
static/
  index.html                 — cały HTML+CSS, zakładki, formularze
  app.js                     — cała logika frontendu (jeden plik)
  manifest.json
  service-worker.js          — CACHE_NAME="analiza-dzialki-v3", network-first
  icons/                     — ⚠️ BRAKUJE w repo obecnie (patrz sekcja 7, znane luki)
```

### Cache-busting — KRYTYCZNE, rób to przy KAŻDEJ zmianie app.js
`index.html` ładuje skrypt jako `<script src="/static/app.js?v=N"></script>`.
**Aktualny numer: v=10.** Przy każdej zmianie `app.js` podbij `N` o 1 i
zaktualizuj `index.html`. Bez tego przeglądarka użytkowniczki może pokazywać
starą, zbuforowaną wersję — to się realnie zdarzyło kilkukrotnie i było
mylące (appka "nie widziała" zmian, mimo że kod na GitHub był poprawny).

Backend ma też middleware wymuszający `Cache-Control: no-store` na
wszystkich `/api/*` — to osobna warstwa ochrony przed cache'owaniem
odpowiedzi API przez przeglądarkę (inny problem niż cache samego JS).

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
| Media/GESUT | KIUT, metoda pikselowa przez `GetMap` (GetFeatureInfo strukturalnie zepsute — zwraca ten sam komunikat zawsze) | Działa, ale to przybliżenie wizualne, nie dokładne atrybuty |
| Plany zagospodarowania | **Dwa źródła próbowane po kolei**: (1) nowy `KrajowaIntegracjaAktowPlanowaniaPrzestrzennego` (Rejestr Urbanistyczny, wystartował 1 lipca 2026, **obecnie ogólnokrajowo prawie pusty** — gminy dopiero wgrywają dane), (2) stary `KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego` jako fallback | Oba używają strategii: `GetMap` (szybkie, niezawodne) jako sonda, dopiero potem `GetFeatureInfo` z limitem czasu (część serwerów gmin wisi w nieskończoność na `GetFeatureInfo`) |
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
- **Szerokość i długość razem** (±20% każda, dopasowanie niezależne od
  kolejności — nie trzeba wiedzieć, które z dwóch podanych liczb to
  „szerokość")
- **Jeden wymiar** (tylko w połączeniu z powierzchnią — sam jeden wymiar to
  za mało informacji, appka to blokuje z jasnym komunikatem)
- **Tryb „maksimum"** (`dims_as_maximum=true`, checkbox w UI) — oba wymiary
  wymagane, traktowane jako **twardy sufit**, nie przybliżenie. Sortowanie
  wtedy inne: od najpełniej wykorzystujących dostępną przestrzeń (blisko
  limitu), nie od najbliższych jakiemuś celowi.

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

### Funkcje
- **26+9=35 konkretnych miejscowości górskich**, pogrupowanych w 9
  regionów (Tatry, Podhale, Beskid Żywiecki/Mały, Beskidy, Beskid
  Makowski/Wyspowy przez powiat myślenicki, Beskid Wyspowy, Bieszczady,
  Sudety) + całe województwa jako opcja szersza
- **Formularz filtrów**: rodzaj (działka/dom), region, cena od/do,
  powierzchnia od/do → generuje **5 linków naraz** (Otodom, OLX, Domiporta,
  Nieruchomości-online, GetHome) — każdy z osobnym, zweryfikowanym formatem
  adresu URL tego portalu
- **OLX zawsze sortuje od najnowszych** (`search[order]=created_at:desc`
  — potwierdzone, że to realnie działa). **Otodom NIE ma parametru
  sortowania w adresie URL** mimo sprawdzenia wielu wariantów — to trzeba
  ustawić ręcznie po otwarciu strony
- **Zapisywanie wyszukiwań** w `localStorage` (nazwij, zapisz, wczytaj
  jednym kliknięciem, usuń) — to lokalne skróty w tej appce, nie
  prawdziwe alerty e-mail
- Link-out do ogólnej wyszukiwarki **Trovit** — ale **NIE agreguje
  Otodom/OLX** (sprawdzone: to metawyszukiwarka mniejszych/średnich
  portali), appka to teraz jasno komunikuje

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

- **Folder `static/icons/` brakuje w repo `analiza-dzialki`** (potwierdzone
  ponownie 2026-09-01 przy audycie kodu) — `manifest.json`
  (`icon-192.png`, `icon-512.png`, `icon-512-maskable.png`), `index.html`
  (`apple-touch-icon.png`, `icon-192.png`, `favicon.png`) i
  `service-worker.js` (`APP_SHELL` z `icon-192.png`, `icon-512.png` w
  `cache.addAll()`) odwołują się do plików, których w repo nie ma.
  **To realne ryzyko, nie tylko kosmetyka**: `cache.addAll()` w
  `service-worker.js` odrzuca całą instalację service workera, jeśli
  choć jeden z URL-i 404-uje — czyli PWA/„dodaj do ekranu głównego" może
  aktualnie w ogóle się nie instalować poprawnie. Priorytet: wysoki.
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
- **Brak logowania w `main.py`** (potwierdzone 2026-09-01) — zero
  `logging`/`print`, a `except Exception: pass` w `_uldk_get_by_xy_raw`
  cicho połyka błędy. Utrudnia to odróżnianie przejściowych awarii
  serwerów powiatowych od trwałych (patrz metodologia, sekcja 8, punkt 7).
- **Timeouty rozrzucone i niespójne (10–45s)** między wywołaniami do ULDK/
  WFS/Overpass/ISOK, retry tylko w `_uldk_get_by_xy_raw`. Wolny serwer
  powiatowy może zablokować request na pełne 45s bez fallbacku.
- **`main.py` to jeden plik, ~1720 linii, 45 funkcji top-level**, bez
  podziału na moduły — `search_parcels_universal` ma ~161 linii obsługując
  4 tryby naraz. Kandydat do refaktoru na moduły (np. `services/wfs.py`,
  `services/uldk.py`, `services/geo.py`), jeśli/kiedy zacznie przeszkadzać
  w utrzymaniu.
- **Brak testów automatycznych i CI/CD w obu repo** — `nodemods/`+jsdom
  wspomniane w metodologii (sekcja 8, punkt 3) w praktyce nie istnieje w
  repo `analiza-dzialki`. Całe testowanie jest manualne, zgodnie z
  metodologią w sekcji 8.

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
