# Działki testowe

Lista prawdziwych, znanych działek do ręcznego testowania appki — każda ma
jakąś potwierdzoną z zewnątrz cechę (niezależne źródło danych, znany błąd,
ciekawy przypadek brzegowy), więc nadaje się do sprawdzenia „czy appka
nadal to poprawnie pokazuje" po zmianach.

**Zasada**: kiedy Klaudia podaje nową działkę testową, dopisz ją tutaj
(numer TERYT, lokalizacja, dlaczego jest przydatna, data dodania). Kiedy
Klaudia pyta o „działkę testową", to jest plik, do którego zaglądasz.

## Lista

| TERYT | Lokalizacja | Dlaczego przydatna | Dodano |
|---|---|---|---|
| `121508_2.0002.16014/3` | Zawoja, gm. Zawoja, pow. suski, woj. małopolskie (1279 m²) | Realna działka z darmowej oceny Działkopedii (zrzuty ekranu + PDF przesłane przez Klaudię, 4.09.2026) — ma potwierdzone z zewnętrznego źródła: brak osuwisk (SOPO), strefa zalewowa Q10 w odległości 159 m, działka poza nią (ISOK), 3 obszary chronione w promieniu 2 km (GDOŚ), **MPZP: działka objęta planem miejscowym** (potwierdzone zrzutem z żywej apki — widoczny na KIMPZP, ale serwer gminy nie zwrócił szczegółów na czas, status "partial" w naszym systemie — POPRAWKA 2026-09-04: wcześniejszy wpis mylnie mówił "brak planu miejscowego", to była błędna interpretacja PDF-u), plan ogólny gminy Zawoja: status "projekt publiczny" (nieuchwalony — osobny akt od MPZP, Działkopedia flaguje to jako osobny punkt UWAGA, którego nasza apka jeszcze nie sprawdza), droga w odległości 71 m, mediana cen z transakcji RCN 60 zł/m² (361 transakcji w obrębie). Dobry test porównawczy dla sekcji: osuwiska, powódź, obszary chronione, plan zagospodarowania (MPZP vs plan ogólny), droga. | 2026-09-04 |
| `241704_2.0002.3917/5` | Korbielów, gm. Jeleśnia, pow. żywiecki, woj. śląskie | Zgłoszona przez Klaudię na żywo 2026-09-04 przy okazji dwóch osobnych usterek: (1) `/api/resolve` bez budżetu czasu (naprawione tego samego dnia, patrz sekcja 3), (2) `services/zoning.py` zwraca `Usługa MPZP (KIMPZP) niedostępna: ConnectTimeout` mimo że warstwa WMS planu WIDOCZNIE i natychmiast renderuje się na mapie po kliknięciu warstwy „MPZP (starszy)"/„Rejestr Urbanistyczny" w Leaflet. **Ten drugi problem wystąpił PONOWNIE 2026-09-04 mimo wcześniejszej naprawy (retry)** — po analizie logów Render na żywo **prawdziwa przyczyna została potwierdzona**: to NIE problem sieciowy (próba wymuszenia IPv4 nie pomogła i została wycofana), tylko przeciążenie backendu na Render (plan darmowy) przy 12 równoległych zapytaniach — w tej samej analizie RÓWNIEŻ sekcje Overpass dostają timeout, a lżejsza działka (Zawoja) działa bez problemu. **To zatem dobra działka regresyjna specyficznie do testowania obciążenia/współbieżności backendu** (nie tylko samego MPZP) — patrz HANDOFF.md sekcja 3.1, „Prawdziwa przyczyna: przeciążenie zasobów na Render" i „Plan naprawy: zmniejszenie obciążenia współbieżnego" (jeszcze NIE wdrożony — czeka na decyzję Klaudii). | 2026-09-04 |
