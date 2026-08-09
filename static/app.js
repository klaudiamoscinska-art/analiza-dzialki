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

  let parcelLayer = null;

  const input = document.getElementById("parcelInput");
  const clearBtn = document.getElementById("clearBtn");
  const checkBtn = document.getElementById("checkBtn");
  const spinner = document.getElementById("spinner");
  const btnLabel = document.getElementById("btnLabel");
  const results = document.getElementById("results");
  const errorBox = document.getElementById("errorBox");

  clearBtn.addEventListener("click", () => {
    input.value = "";
    input.focus();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runAnalysis();
  });

  checkBtn.addEventListener("click", runAnalysis);

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
      showError("Wpisz numer działki (identyfikator TERYT).");
      return;
    }

    clearError();
    setLoading(true);
    results.innerHTML = "";

    try {
      const resp = await fetch(`/api/analyze?parcel_id=${encodeURIComponent(parcelId)}`);
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || "Nie udało się przeanalizować działki.");
      }

      renderMap(data);
      renderResults(data);
    } catch (err) {
      showError(err.message || "Wystąpił nieoczekiwany błąd.");
      results.innerHTML = "";
    } finally {
      setLoading(false);
    }
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

  function renderResults(data) {
    const p = data.parcel;
    let html = "";

    html += `<div class="teryt-echo">${escapeHTML(p.teryt_id)} · ${escapeHTML(
      p.commune || ""
    )}, pow. ${escapeHTML(p.county || "")}, woj. ${escapeHTML(p.voivodeship || "")}${
      p.multiple_found ? " · uwaga: znaleziono więcej niż jedną działkę, pokazano pierwszą" : ""
    }</div>`;

    // KROK 1 — landslide risk
    const ls = data.landslide;
    if (ls.status === "ok") {
      if (ls.has_landslide) {
        html += cardHTML({
          title: "Zagrożenie osuwiskowe",
          text: "WYKRYTO OSUWISKO / TEREN ZAGROŻONY",
          tone: "danger",
        });
      } else {
        html += cardHTML({
          title: "Zagrożenie osuwiskowe",
          text: "BRAK ZAGROŻEŃ OSUWISKOWYCH",
          tone: "ok",
        });
      }
    } else {
      html += cardHTML({
        title: "Zagrożenie osuwiskowe (SOPO PIG-PIB)",
        text: ls.message || "Usługa niedostępna.",
      });
    }

    // KROK 2a — utilities (GESUT)
    html += cardHTML({
      title: "Uzbrojenie terenu (GESUT)",
      text:
        data.utilities.status === "ok"
          ? data.utilities.summary
          : data.utilities.message,
    });

    // KROK 2b — cadastre / land classification
    html += cardHTML({
      title: "Ewidencja gruntów (EGiB)",
      text:
        data.cadastre.status === "ok" ? data.cadastre.summary : data.cadastre.message,
    });

    // KROK 2c — zoning (MPZP)
    html += cardHTML({
      title: "Przeznaczenie (MPZP)",
      text: data.zoning.status === "ok" ? data.zoning.summary : data.zoning.message,
    });

    // KROK 4 — GUS-style value estimate
    const g = data.gus_estimate;
    if (g.status === "ok") {
      html += `
        <div class="card value-card">
          <p class="eyebrow">Wycena statystyczna</p>
          <p class="amount">${fmtPLN(g.estimated_value_pln)}</p>
          <p class="breakdown">
            ${fmtArea(g.area_m2)} × ${g.price_per_m2} zł/m²
            (śr. woj. ${escapeHTML(g.voivodeship_name || "")})
          </p>
          <p class="disclaimer">
            Szacunkowa wartość statystyczna gruntu — wyliczona na podstawie
            powierzchni działki i orientacyjnej średniej ceny gruntów dla
            województwa. Nie jest to wycena rzeczoznawcy majątkowego ani
            operat szacunkowy i nie może być podstawą decyzji finansowych
            lub prawnych.
          </p>
        </div>`;
    } else {
      html += cardHTML({
        title: "Wycena statystyczna",
        text: g.message || "Nie udało się wyliczyć wyceny.",
      });
    }

    results.innerHTML = html;
  }
})();
