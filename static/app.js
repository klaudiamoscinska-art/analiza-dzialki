(function () {
  "use strict";

  // Rejestracja service workera — wymagana, żeby przeglądarka zaproponowała
  // "Dodaj do ekranu głównego" i żeby appka otwierała się bez belki adresu.
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {
        // Brak service workera nie blokuje działania appki — po prostu
        // nie będzie można jej zainstalować na ekranie głównym.
      });
    });
  }

  const map = L.map("map", { zoomControl: true, attributionControl: false }).setView(
    [52.0, 19.3],
    6
  );
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
  }).addTo(map);

  // Toggleable overlays. NOTE: SOPO/hydrogeologia (cbdgmapa.pgi.gov.pl) are
  // deliberately NOT offered as map tiles here — that host sits behind an
  // Incapsula WAF that intermittently blocks plain tile requests too, so
  // those two sources stay panel-only (see main.py docstring for details).
  const egibLayer = L.tileLayer.wms(
    "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow",
    {
      layers: "dzialki,numery_dzialek,budynki",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      maxZoom: 22,
      attribution: "GUGiK EGiB",
    }
  );
  const mpzpLayer = L.tileLayer.wms(
    "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
    {
      layers: "plany",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      maxZoom: 22,
      opacity: 0.55,
      attribution: "GUGiK MPZP",
    }
  );
  const appLayer = L.tileLayer.wms(
    "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaAktowPlanowaniaPrzestrzennego",
    {
      layers: "app",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      maxZoom: 22,
      opacity: 0.55,
      attribution: "GUGiK Rejestr Urbanistyczny",
    }
  );
  egibLayer.addTo(map);

  L.control
    .layers(
      null,
      {
        "Działki i budynki (EGiB)": egibLayer,
        "Plan miejscowy — MPZP (starszy)": mpzpLayer,
        "Plan miejscowy — Rejestr Urbanistyczny": appLayer,
      },
      { position: "topright", collapsed: false }
    )
    .addTo(map);

  let parcelLayer = null;
  let currentCandidates = [];

  const input = document.getElementById("parcelInput");
  const clearBtn = document.getElementById("clearBtn");
  const checkBtn = document.getElementById("checkBtn");
  const spinner = document.getElementById("spinner");
  const btnLabel = document.getElementById("btnLabel");
  const results = document.getElementById("results");
  const errorBox = document.getElementById("errorBox");

  const addressInput = document.getElementById("addressInput");
  const addressClearBtn = document.getElementById("addressClearBtn");
  const addressCheckBtn = document.getElementById("addressCheckBtn");
  const addressSpinner = document.getElementById("addressSpinner");
  const addressBtnLabel = document.getElementById("addressBtnLabel");

  clearBtn.addEventListener("click", () => {
    input.value = "";
    input.focus();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runAnalysis();
  });

  checkBtn.addEventListener("click", runAnalysis);

  addressClearBtn.addEventListener("click", () => {
    addressInput.value = "";
    addressInput.focus();
  });

  addressInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runAddressSearch();
  });

  addressCheckBtn.addEventListener("click", runAddressSearch);

  function setAddressLoading(isLoading) {
    addressCheckBtn.disabled = isLoading;
    addressSpinner.classList.toggle("on", isLoading);
    addressBtnLabel.textContent = isLoading ? "Sprawdzanie…" : "Sprawdź adres";
  }

  // ---------- Zakładki ----------
  const tabBtnAnaliza = document.getElementById("tabBtnAnaliza");
  const tabBtnSzukaj = document.getElementById("tabBtnSzukaj");
  const tabAnaliza = document.getElementById("tabAnaliza");
  const tabSzukaj = document.getElementById("tabSzukaj");

  function showTab(name) {
    const analizaActive = name === "analiza";
    tabBtnAnaliza.classList.toggle("active", analizaActive);
    tabBtnSzukaj.classList.toggle("active", !analizaActive);
    tabAnaliza.classList.toggle("active", analizaActive);
    tabSzukaj.classList.toggle("active", !analizaActive);
  }

  tabBtnAnaliza.addEventListener("click", () => showTab("analiza"));
  tabBtnSzukaj.addEventListener("click", () => showTab("szukaj"));

  // ---------- Szukaj działki po rozmiarze ----------
  const sizeSearchPlace = document.getElementById("sizeSearchPlace");
  const sizeSearchPlaceClearBtn = document.getElementById("sizeSearchPlaceClearBtn");
  const sizeSearchArea = document.getElementById("sizeSearchArea");
  const sizeSearchBtn = document.getElementById("sizeSearchBtn");
  const sizeSearchSpinner = document.getElementById("sizeSearchSpinner");
  const sizeSearchBtnLabel = document.getElementById("sizeSearchBtnLabel");
  const sizeSearchErrorBox = document.getElementById("sizeSearchErrorBox");
  const sizeSearchResultsEl = document.getElementById("sizeSearchResults");

  sizeSearchPlaceClearBtn.addEventListener("click", () => {
    sizeSearchPlace.value = "";
    sizeSearchPlace.focus();
  });

  function setSizeSearchLoading(isLoading) {
    sizeSearchBtn.disabled = isLoading;
    sizeSearchSpinner.classList.toggle("on", isLoading);
    sizeSearchBtnLabel.textContent = isLoading ? "Szukam…" : "Szukaj działek";
  }

  function showSizeSearchError(message) {
    sizeSearchErrorBox.textContent = message;
    sizeSearchErrorBox.style.display = "block";
  }

  function clearSizeSearchError() {
    sizeSearchErrorBox.style.display = "none";
    sizeSearchErrorBox.textContent = "";
  }

  async function runSizeSearch() {
    const place = sizeSearchPlace.value.trim();
    const area = sizeSearchArea.value.trim();

    if (!place) {
      showSizeSearchError("Wpisz nazwę miejscowości.");
      return;
    }
    if (!area || Number(area) <= 0) {
      showSizeSearchError("Podaj szukaną powierzchnię działki w m² (liczba większa od zera).");
      return;
    }

    clearSizeSearchError();
    setSizeSearchLoading(true);
    sizeSearchResultsEl.innerHTML = "";

    try {
      const resp = await fetch(
        `/api/search-by-size?place=${encodeURIComponent(place)}&area_m2=${encodeURIComponent(area)}`
      );
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || "Nie udało się przeszukać tej okolicy.");
      }

      renderSizeSearchResults(data, Number(area));
    } catch (err) {
      showSizeSearchError(err.message || "Wystąpił nieoczekiwany błąd.");
      sizeSearchResultsEl.innerHTML = "";
    } finally {
      setSizeSearchLoading(false);
    }
  }

  function renderSizeSearchResults(data, targetArea) {
    if (!data.matches || data.matches.length === 0) {
      sizeSearchResultsEl.innerHTML = `<div class="empty-hint">Nie znaleziono żadnej działki o zbliżonym rozmiarze (±10% od ${targetArea} m²) w promieniu ok. 2 km od "${escapeHTML(
        data.search_center || ""
      )}". Sprawdzono ${data.candidates_checked || 0} działek w okolicy.</div>`;
      return;
    }

    const rows = data.matches
      .map((m) => {
        const isNegative = m.diff_pct < 0;
        const diffLabel = `${isNegative ? "" : "+"}${m.diff_pct}%`;
        return `
        <button class="size-result-row" data-teryt="${escapeHTML(m.teryt_id)}">
          <span>
            <span class="size-result-main">~${fmtArea(m.area_m2)}</span>
            <span class="size-result-sub">${escapeHTML(m.commune)}, ${escapeHTML(m.county)}</span>
          </span>
          <span class="size-result-diff${isNegative ? " negative" : ""}">${diffLabel}</span>
        </button>`;
      })
      .join("");

    sizeSearchResultsEl.innerHTML = `
      <p class="disclaimer" style="margin:0 0 10px;">Sprawdzono ${data.candidates_checked} działek w okolicy „${escapeHTML(
      data.search_center || ""
    )}" — poniżej ${data.matches.length} najbardziej zbliżonych do ${targetArea} m², od najbliższej.</p>
      ${rows}`;

    sizeSearchResultsEl.querySelectorAll(".size-result-row").forEach((btn) => {
      btn.addEventListener("click", async () => {
        showTab("analiza");
        currentCandidates = [];
        await analyzeTerytId(btn.getAttribute("data-teryt"));
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  }

  sizeSearchArea.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSizeSearch();
  });

  sizeSearchBtn.addEventListener("click", runSizeSearch);

  function setLoading(isLoading) {
    checkBtn.disabled = isLoading;
    spinner.classList.toggle("on", isLoading);
    btnLabel.textContent = isLoading ? "Sprawdzanie…" : "Sprawdź działkę";
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = "block";
  }

  function clearError() {
    errorBox.style.display = "none";
    errorBox.textContent = "";
  }

  function fmtPLN(value) {
    try {
      return new Intl.NumberFormat("pl-PL", {
        style: "currency",
        currency: "PLN",
        maximumFractionDigits: 0,
      }).format(value);
    } catch (e) {
      return value + " zł";
    }
  }

  function fmtArea(m2) {
    return new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 0 }).format(m2) + " m²";
  }

  async function runAnalysis() {
    const parcelId = input.value.trim();
    if (!parcelId) {
      showError("Wpisz nazwę miejscowości i numer działki (albo pełny identyfikator TERYT).");
      return;
    }

    clearError();
    setLoading(true);
    results.innerHTML = "";
    currentCandidates = [];

    try {
      const resolveResp = await fetch(`/api/resolve?query=${encodeURIComponent(parcelId)}`);
      const resolveData = await resolveResp.json();

      if (!resolveResp.ok) {
        throw new Error(resolveData.detail || "Nie udało się znaleźć działki.");
      }

      if (resolveData.resolved) {
        await analyzeTerytId(resolveData.teryt_id);
      } else {
        currentCandidates = resolveData.candidates;
        renderCandidatePicker(resolveData.candidates);
        setLoading(false);
      }
    } catch (err) {
      showError(err.message || "Wystąpił nieoczekiwany błąd.");
      results.innerHTML = "";
      setLoading(false);
    }
  }

  async function runAddressSearch() {
    const address = addressInput.value.trim();
    if (!address) {
      showError("Wpisz adres (miejscowość, ulica i numer).");
      return;
    }

    clearError();
    setAddressLoading(true);
    results.innerHTML = "";
    currentCandidates = [];

    try {
      const resolveResp = await fetch(`/api/resolve-address?query=${encodeURIComponent(address)}`);
      const resolveData = await resolveResp.json();

      if (!resolveResp.ok) {
        throw new Error(resolveData.detail || "Nie udało się znaleźć działki pod tym adresem.");
      }

      if (resolveData.resolved) {
        await analyzeTerytId(resolveData.teryt_id, setAddressLoading);
      } else {
        currentCandidates = resolveData.candidates;
        renderCandidatePicker(resolveData.candidates);
        setAddressLoading(false);
      }
    } catch (err) {
      showError(err.message || "Wystąpił nieoczekiwany błąd.");
      results.innerHTML = "";
      setAddressLoading(false);
    }
  }

  async function analyzeTerytId(terytId, loadingSetter = setLoading) {
    loadingSetter(true);
    clearError();
    results.innerHTML = "";
    try {
      const resp = await fetch(`/api/analyze?parcel_id=${encodeURIComponent(terytId)}`);
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || "Nie udało się przeanalizować działki.");
      }

      renderMap(data);
      renderResults(data);
      if (currentCandidates.length > 1) {
        renderSwitcherBar(terytId);
      }
    } catch (err) {
      showError(err.message || "Wystąpił nieoczekiwany błąd.");
      results.innerHTML = "";
      if (currentCandidates.length > 1) {
        renderSwitcherBar(terytId);
      }
    } finally {
      // Zawsze resetuj oba przyciski — działka mogła być znaleziona przez
      // dowolną z dwóch wyszukiwarek (numer działki albo adres).
      setLoading(false);
      setAddressLoading(false);
    }
  }

  function renderSwitcherBar(activeTeryt) {
    const chips = currentCandidates
      .map((c) => {
        const isActive = c.teryt_id === activeTeryt;
        return `
        <button class="switcher-chip${isActive ? " active" : ""}" data-teryt="${escapeHTML(c.teryt_id)}">
          ${escapeHTML(c.commune)}
        </button>`;
      })
      .join("");
    const bar = document.createElement("div");
    bar.id = "switcherBar";
    bar.innerHTML = `
      <p class="eyebrow" style="margin:0 0 6px;">Kilka działek o tym numerze — przełącz się</p>
      <div class="switcher-row">${chips}</div>`;
    results.insertBefore(bar, results.firstChild);
    bar.querySelectorAll(".switcher-chip").forEach((btn) => {
      btn.addEventListener("click", () => analyzeTerytId(btn.getAttribute("data-teryt")));
    });
  }

  function renderCandidatePicker(candidates) {
    clearError();
    const rows = candidates
      .map(
        (c, i) => `
        <button class="candidate-row" data-teryt="${escapeHTML(c.teryt_id)}">
          <span class="candidate-main">${escapeHTML(c.commune)}, ${escapeHTML(c.county)}</span>
          <span class="candidate-sub">${escapeHTML(c.voivodeship)} · działka ${escapeHTML(c.parcel_no)}</span>
        </button>`
      )
      .join("");
    results.innerHTML = `
      <p class="eyebrow" style="margin-top:4px;">Kilka działek o tym numerze w okolicy — wybierz właściwą</p>
      <div id="candidateList">${rows}</div>`;

    results.querySelectorAll(".candidate-row").forEach((btn) => {
      btn.addEventListener("click", () => analyzeTerytId(btn.getAttribute("data-teryt")));
    });
  }

  function renderMap(data) {
    if (parcelLayer) {
      map.removeLayer(parcelLayer);
    }
    parcelLayer = L.geoJSON(data.geometry_geojson, {
      style: { color: "#0f5c4f", weight: 3, fillColor: "#0f5c4f", fillOpacity: 0.25 },
    }).addTo(map);
    map.fitBounds(parcelLayer.getBounds(), { padding: [24, 24], maxZoom: 19 });
  }

  function cardHTML({ title, text, tone }) {
    const cls = tone ? ` ${tone}` : " muted";
    return `<div class="card${cls}"><h3>${title}</h3><p>${escapeHTML(text)}</p></div>`;
  }

  function escapeHTML(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function tableHTML(rows) {
    if (!rows || !rows.length) return "";
    const body = rows
      .map(
        (r) =>
          `<tr><td class="label">${escapeHTML(r.label)}</td><td class="value">${escapeHTML(
            r.value || "—"
          )}</td></tr>`
      )
      .join("");
    return `<table class="data-table">${body}</table>`;
  }

  function renderResults(data) {
    const p = data.parcel;
    let html = "";

    html += `<div class="teryt-echo">${escapeHTML(p.teryt_id)} · ${escapeHTML(
      p.commune || ""
    )}, pow. ${escapeHTML(p.county || "")}, woj. ${escapeHTML(p.voivodeship || "")}${
      p.multiple_found ? " · uwaga: znaleziono więcej niż jedną działkę, pokazano pierwszą" : ""
    }</div>`;

    if (p.emapa_link) {
      html += `<a class="link-out-btn" href="${p.emapa_link}" target="_blank" rel="noopener noreferrer" style="display:block;margin-bottom:10px;">Zobacz na Polska.e-mapa.net →</a>`;
    }

    if (p.geoportal_link) {
      html += `<a class="link-out-btn" href="${p.geoportal_link}" target="_blank" rel="noopener noreferrer" style="display:block;margin-bottom:14px;">Zobacz na Polska mapa (geoportal.gov.pl) →</a>`;
    }

    // 1 — Ewidencja gruntów i budynków
    const cad = data.cadastre;
    const bld = data.buildings;
    let egibInner = "";
    if (cad.status === "ok" && cad.table.length) {
      egibInner += tableHTML(cad.table);
    } else if (cad.status !== "ok") {
      egibInner += `<p>${escapeHTML(cad.message)}</p>`;
    } else {
      egibInner += `<p>Brak danych w tej lokalizacji.</p>`;
    }
    egibInner += `<p class="disclaimer" style="margin-top:10px;padding-top:8px;">Budynki na działce</p>`;
    if (bld.status === "ok" && bld.buildings && bld.buildings.length) {
      egibInner += bld.buildings
        .map((b) => {
          const levels = [];
          if (b.levels_above_ground) levels.push(`${b.levels_above_ground} nadz.`);
          if (b.levels_below_ground) levels.push(`${b.levels_below_ground} podz.`);
          const levelsTxt = levels.length ? ` · kondygnacje: ${levels.join(" / ")}` : "";
          return `
        <div class="building-row">
          <span>${escapeHTML(b.label)}<span class="tag">${
            b.fully_within_parcel ? "w całości na działce" : "częściowo na działce"
          }${levelsTxt}</span></span>
          <span>~${fmtArea(b.area_m2)}</span>
        </div>`;
        })
        .join("");
      egibInner += `<p class="disclaimer">Źródło: ${escapeHTML(
        bld.source
      )}. Żadna darmowa usługa GUGiK nie udostępnia atrybutów budynku (identyfikator, kondygnacje) przez otwarte API — potwierdzone testami. Liczbę kondygnacji pokazujemy tylko, gdy jest oznaczona w OpenStreetMap.</p>`;
    } else if (bld.status === "ok") {
      egibInner += `<p class="disclaimer">Nie wykryto budynków na tej działce.</p>`;
    } else {
      egibInner += `<p class="disclaimer">${escapeHTML(bld.message)}</p>`;
    }
    html += `<div class="card muted"><h3>Ewidencja gruntów i budynków</h3>${egibInner}</div>`;

    // 2 — Zagrożenie osuwiskowe
    const ls = data.landslide;
    if (ls.status === "ok") {
      const cats =
        ls.matched_categories && ls.matched_categories.length
          ? `<p class="disclaimer" style="color:inherit;opacity:.85;">Wykryte kategorie: ${escapeHTML(
              ls.matched_categories.join(", ")
            )}</p>`
          : "";
      html += ls.has_landslide
        ? `<div class="card danger"><h3>Zagrożenie osuwiskowe</h3><p>WYKRYTO OSUWISKO / TEREN ZAGROŻONY</p>${cats}</div>`
        : cardHTML({ title: "Zagrożenie osuwiskowe", text: "BRAK ZAGROŻEŃ OSUWISKOWYCH", tone: "ok" });
    } else {
      html += cardHTML({ title: "Zagrożenie osuwiskowe (SOPO PIG-PIB)", text: ls.message });
    }

    // 3 — Media / uzbrojenie terenu (GESUT) — chip grid
    const ut = data.utilities;
    let utInner = "";
    if (ut.status === "ok") {
      utInner = `<p class="disclaimer" style="margin:0 0 8px;">Wykrywanie na podstawie obrazu mapy — czy w pobliżu działki narysowana jest linia danego typu.</p>`;
      utInner += `<div class="chip-grid">${ut.utilities
        .map(
          (u) => `
        <div class="chip${u.present ? " present" : ""}">
          <span>${escapeHTML(u.label)}</span><span class="dot"></span>
        </div>`
        )
        .join("")}</div>`;
    } else {
      utInner = `<p>${escapeHTML(ut.message)}</p>`;
    }
    html += `<div class="card muted"><h3>Media / uzbrojenie terenu (GESUT)</h3>${utInner}</div>`;

    // 4 — Hydrologia i zagrożenie powodziowe
    const hy = data.hydrology;
    let hyInner = "";
    const fz = hy.flood_zone;
    if (fz.status === "ok") {
      hyInner += fz.in_flood_zone
        ? `<p style="color:var(--danger);font-weight:700;">TEREN W STREFIE ZALEWOWEJ (ISOK)${
            fz.depth_range ? " — głębokość: " + escapeHTML(fz.depth_range) : ""
          }</p>`
        : `<p style="color:var(--ok);font-weight:700;">Brak strefy zalewowej ISOK w tym miejscu</p>`;
    } else {
      hyInner += `<p class="disclaimer">${escapeHTML(fz.message)}</p>`;
    }
    const wl = hy.waterlogging;
    if (wl.status === "ok") {
      hyInner += wl.at_risk
        ? `<p style="color:var(--danger);">Teren podatny na podtopienia (wody gruntowe, PIG-PIB)</p>`
        : `<p class="disclaimer">Brak wykrytego ryzyka podtopień (wody gruntowe)</p>`;
    }
    const ww = hy.waterways;
    if (ww.status === "ok" && ww.waterways.length) {
      hyInner += `<p class="disclaimer" style="margin-top:8px;">Cieki wodne w promieniu 400 m</p>`;
      hyInner += ww.waterways
        .map(
          (w) => `<div class="waterway-row"><span>${escapeHTML(w.name)} <span class="kind">(${escapeHTML(
            w.kind
          )})</span></span><span>${w.distance_m} m</span></div>`
        )
        .join("");
    } else if (ww.status === "ok") {
      hyInner += `<p class="disclaimer">Brak cieków wodnych w promieniu 400 m.</p>`;
    }
    html += `<div class="card muted"><h3>Hydrologia i zagrożenie powodziowe</h3>${hyInner}</div>`;

    // 4b — Odległość do najbliższej drogi gminnej
    const nr = data.nearest_road;
    let nrInner = "";
    if (nr.status === "ok" && nr.found === "yes") {
      const km = nr.distance_m >= 1000 ? `${(nr.distance_m / 1000).toFixed(2)} km` : `${nr.distance_m} m`;
      nrInner += `<p style="font-weight:700;font-size:16px;">${km}</p>`;
      nrInner += `<p class="disclaimer" style="margin-top:2px;">${escapeHTML(nr.road_name)}${
        nr.road_ref ? " (" + escapeHTML(nr.road_ref) + ")" : ""
      }</p>`;
      if (nr.is_fallback_powiatowa) {
        nrInner += `<p class="disclaimer">Brak drogi gminnej w promieniu wyszukiwania — pokazano najbliższą drogę wyższej kategorii (prawdopodobnie powiatową).</p>`;
      }
      nrInner += `<p class="disclaimer">${escapeHTML(nr.source)}</p>`;
    } else if (nr.status === "ok") {
      nrInner = `<p>${escapeHTML(nr.message)}</p>`;
    } else {
      nrInner = `<p>${escapeHTML(nr.message)}</p>`;
    }
    html += `<div class="card muted"><h3>Odległość do drogi gminnej</h3>${nrInner}</div>`;

    // 5 — Plany zagospodarowania (MPZP), tabular
    const zon = data.zoning;
    let zonInner = "";
    if (zon.status === "ok") {
      zonInner =
        zon.found === "yes"
          ? `${zon.source ? `<p class="disclaimer">Źródło: ${escapeHTML(zon.source)}</p>` : ""}${tableHTML(
              zon.table
            )}`
          : `<p>Brak planu miejscowego w tej lokalizacji (sprawdzono Rejestr Urbanistyczny i starszą usługę MPZP).</p>`;
    } else if (zon.status === "partial") {
      zonInner = `<p style="color:var(--danger);font-weight:700;">Działka jest objęta planem miejscowym</p><p class="disclaimer">${escapeHTML(
        zon.message
      )}</p>`;
    } else {
      zonInner = `<p>${escapeHTML(zon.message)}</p>`;
    }
    html += `<div class="card muted"><h3>Plany zagospodarowania</h3>${zonInner}</div>`;

    // 6 — Pozwolenia na budowę (GUNB/RWDZ)
    html += `
      <div class="card muted">
        <h3>Pozwolenia na budowę (GUNB / RWDZ)</h3>
        <p>Rejestr RWDZ nie udostępnia otwartego API (wyszukiwanie chronione CAPTCHA) — dane trzeba sprawdzić ręcznie.</p>
        <a class="link-out-btn" href="${data.permits.gunb_link}" target="_blank" rel="noopener noreferrer">Sprawdź w rejestrze GUNB →</a>
      </div>`;

    // 7 — Wycena statystyczna (land + buildings, split)
    const val = data.valuation;
    if (val.status === "ok") {
      html += `
        <div class="card value-card">
          <p class="eyebrow">Szacunkowa wartość działki (grunt)</p>
          <p class="amount">${fmtPLN(val.land.value_pln)}</p>
          <p class="breakdown">${fmtArea(val.land.area_m2)} × ${val.land.price_per_m2} zł/m² (śr. woj. ${escapeHTML(
        val.land.voivodeship_name || ""
      )})</p>
        </div>`;
      if (val.buildings) {
        html += `
          <div class="card value-card secondary">
            <p class="eyebrow">Szacunkowa wartość budynków</p>
            <p class="amount">${fmtPLN(val.buildings.value_pln)}</p>
            <p class="breakdown">${fmtArea(val.buildings.footprint_area_m2)} pow. zabudowy × ${
          val.buildings.assumed_cost_per_m2
        } zł/m² (${val.buildings.building_count} budynek/-ów)</p>
          </div>`;
      }
      html += `<p class="disclaimer">Wyceny mają charakter wyłącznie orientacyjny i statystyczny — nie są operatem szacunkowym rzeczoznawcy i nie mogą być podstawą decyzji finansowych lub prawnych. Wartość budynków to bardzo uproszczony szacunek na podstawie powierzchni zabudowy, bez uwzględnienia liczby pięter, stanu czy standardu wykończenia.</p>`;
    } else {
      html += cardHTML({ title: "Wycena statystyczna", text: val.message });
    }

    results.innerHTML = html;
  }
})();
