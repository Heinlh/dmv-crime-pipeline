// Home page: freshness banner, plain-language summary, KPI tiles, and
// the incident map (hover tooltip, click summary card, filter row).

// --- freshness banner + summary + KPI tiles ---

function renderTrendDirection(pct) {
  if (pct > 3) return `up ${pct.toFixed(1)}% from`;
  if (pct < -3) return `down ${Math.abs(pct).toFixed(1)}% from`;
  return "about the same as";
}

async function renderSummary() {
  const banner = document.getElementById("freshness-banner");
  const sentence = document.getElementById("summary-sentence");
  const cards = document.getElementById("kpi-cards");
  try {
    const s = await fetchJson("data/summary.json");

    banner.innerHTML = `<span class="ok">&#9679; OK</span> DATA AS OF ${esc(fmtDateTime(s.last_updated).toUpperCase())} ` +
      `&middot; ${esc(String(s.pct_missing_coords))}% OF INCIDENTS HAVE NO PUBLISHED LOCATION AND AREN'T MAPPED`;

    const topCategoryLabel = categoryLabel(s.top_category_7d);
    const direction = s.pct_change_7d === null
      ? "about the same as"
      : renderTrendDirection(s.pct_change_7d);
    const jurisdictionNames = (s.sources_active || []).map(jurisdictionLabel).join(", ");
    sentence.textContent = `In the last 7 days, ${s.last_7d_count.toLocaleString()} incidents were reported ` +
      `across ${jurisdictionNames || "the DMV"}, ${direction} the previous 7 days. ` +
      `The most common category was ${topCategoryLabel}.`;
    sentence.classList.remove("loading");

    const deltaClass = s.pct_change_7d > 0 ? "up" : s.pct_change_7d < 0 ? "down" : "";
    const kpis = [
      { label: "Last 24 Hours", value: fmtNumber(s.new_incidents_24h) },
      { label: "Last 7 Days", value: fmtNumber(s.last_7d_count),
        sub: s.pct_change_7d === null ? "" : `${fmtPct(s.pct_change_7d)} vs. previous 7 days`, subClass: deltaClass },
      { label: "Most Common (7d)", value: topCategoryLabel },
      { label: "Records Since 2016", value: fmtNumber(s.total_records),
        sub: s.data_start_date ? `warehouse starts ${fmtDate(s.data_start_date)}` : "" },
    ];
    cards.innerHTML = kpis.map(k => `
      <div class="card">
        <div class="label">${esc(k.label)}</div>
        <div class="value">${esc(k.value)}</div>
        <div class="sub ${k.subClass || ""}">${esc(k.sub || "")}</div>
      </div>
    `).join("");
  } catch (err) {
    banner.textContent = `COULD NOT LOAD PIPELINE STATUS (${err.message}). Run the pipeline to generate site/data/.`;
    sentence.textContent = "";
  }
}

// --- legend (with live counts for the current filter) ---

function renderLegend(countsByCategory) {
  const container = document.getElementById("legend-swatches");
  container.innerHTML = CATEGORY_ORDER.map(cat => `
    <div class="legend-item" title="${esc(CATEGORY_DESCRIPTIONS[cat])}">
      <span class="legend-swatch" style="background:${CATEGORY_COLORS[cat]}"></span>
      ${esc(CATEGORY_LABELS[cat])}
      <span class="count">${countsByCategory ? fmtNumber(countsByCategory[cat] || 0) : ""}</span>
    </div>
  `).join("");
}

// --- populate the category filter from the shared taxonomy ---

function populateCategoryFilter() {
  const select = document.getElementById("f-category");
  for (const cat of CATEGORY_ORDER) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = CATEGORY_LABELS[cat];
    select.appendChild(opt);
  }
}

// --- map ---

const map = L.map("map").setView([38.95, -77.05], 10);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  maxZoom: 19,
}).addTo(map);

const clusterGroup = L.markerClusterGroup({ maxClusterRadius: 46 });
map.addLayer(clusterGroup);

let allIncidents = [];

function popupHtml(inc) {
  const cat = inc.offense_category;
  return `
    <div class="incident-card">
      <span class="badge ${esc(cat)}">${esc(categoryLabel(cat))}</span>
      <div class="title">${esc(incidentTitle(inc))}</div>
      <div class="when">${esc(fmtDateTime(inc.occurred_at))}</div>
      <div class="row"><strong>${esc(inc.block_address || "Location withheld")}</strong></div>
      <div class="row">${esc(jurisdictionLabel(inc.jurisdiction))}${inc.area_name ? " &middot; " + esc(inc.area_name) : ""}</div>
      <div class="row agency">Agency label: ${esc(inc.offense_raw || "n/a")}${inc.case_number ? ` &middot; Case #${esc(inc.case_number)}` : ""}</div>
    </div>
  `;
}

function applyFilters() {
  const jurisdiction = document.getElementById("f-jurisdiction").value;
  const rangeDays = parseInt(document.getElementById("f-range").value, 10);
  const category = document.getElementById("f-category").value;
  const minSeverity = parseInt(document.getElementById("f-severity").value, 10);
  const cutoff = Date.now() - rangeDays * 24 * 60 * 60 * 1000;

  clusterGroup.clearLayers();
  let shown = 0;
  const countsByCategory = {};

  for (const inc of allIncidents) {
    if (inc.latitude === null || inc.longitude === null) continue;
    if (jurisdiction && inc.jurisdiction !== jurisdiction) continue;
    if (category && inc.offense_category !== category) continue;
    if (inc.severity_weight < minSeverity) continue;
    if (Number.isNaN(inc._ts) || inc._ts < cutoff) continue;

    const color = CATEGORY_COLORS[inc.offense_category] || CATEGORY_COLORS.other;
    // Uniform radius: category is the only encoding on the dot. The 2px
    // page-color stroke is the "surface ring" keeping overlaps legible.
    const marker = L.circleMarker([inc.latitude, inc.longitude], {
      radius: 7,
      fillColor: color,
      fillOpacity: 0.85,
      color: "#0a0a14",
      weight: 2,
    });
    marker.bindTooltip(incidentTitle(inc), { direction: "top", opacity: 1 });
    marker.bindPopup(popupHtml(inc), { maxWidth: 300 });
    clusterGroup.addLayer(marker);
    countsByCategory[inc.offense_category] = (countsByCategory[inc.offense_category] || 0) + 1;
    shown++;
  }

  document.getElementById("result-count").textContent = `${shown.toLocaleString()} incidents shown`;
  renderLegend(countsByCategory);
}

async function initMap() {
  try {
    const { incidents } = await fetchIncidents();
    allIncidents = incidents;
    applyFilters();
  } catch (err) {
    document.getElementById("result-count").textContent =
      `Could not load incidents (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

["f-jurisdiction", "f-range", "f-category", "f-severity"].forEach(id =>
  document.getElementById(id).addEventListener("change", applyFilters)
);

populateJurisdictionFilter("f-jurisdiction");
populateCategoryFilter();
renderLegend(null);
renderSummary();
initMap();
