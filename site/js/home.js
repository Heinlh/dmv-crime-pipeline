// --- freshness banner + summary + KPI cards ---

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

    banner.textContent = `Data as of last published agency data: ${fmtDateTime(s.last_updated)}. ` +
      `${s.pct_missing_coords}% of incidents have no published location and aren't shown on the map.`;

    const topCategoryLabel = CATEGORY_LABELS[s.top_category_7d] || s.top_category_7d || "n/a";
    const direction = s.pct_change_7d === null
      ? "about the same as"
      : renderTrendDirection(s.pct_change_7d);
    sentence.textContent = `In the last 7 days, DC and Montgomery County reported ${s.last_7d_count.toLocaleString()} ` +
      `incidents, ${direction} the previous 7 days. The most common category was ${topCategoryLabel}.`;
    sentence.classList.remove("loading");

    const kpis = [
      { label: "New Today", value: s.today_count.toLocaleString() },
      { label: "Last 7 Days", value: s.last_7d_count.toLocaleString(), sub: s.pct_change_7d === null ? "" : `${fmtPct(s.pct_change_7d)} vs. previous 7 days` },
      { label: "Most Common Category", value: topCategoryLabel },
      { label: "Sources Active", value: s.sources_active.length, sub: s.sources_active.map(j => JURISDICTION_LABELS[j] || j).join(", ") },
      { label: "Last Updated", value: fmtDateTime(s.last_updated) },
    ];
    cards.innerHTML = kpis.map(k => `
      <div class="card">
        <div class="label">${k.label}</div>
        <div class="value">${k.value}</div>
        <div class="sub">${k.sub || ""}</div>
      </div>
    `).join("");
  } catch (err) {
    banner.textContent = `Could not load pipeline status (${err.message}). Run the pipeline to generate site/data/.`;
    sentence.textContent = "";
  }
}

// --- legend ---

function renderLegend() {
  const container = document.getElementById("legend-swatches");
  container.innerHTML = Object.keys(CATEGORY_LABELS).map(cat => `
    <div class="legend-item" title="${CATEGORY_DESCRIPTIONS[cat]}">
      <span class="legend-swatch" style="background:${CATEGORY_COLORS[cat]}"></span>
      ${CATEGORY_LABELS[cat]}
    </div>
  `).join("");
}

// --- populate the category filter from the shared taxonomy ---

function populateCategoryFilter() {
  const select = document.getElementById("f-category");
  for (const [value, label] of Object.entries(CATEGORY_LABELS)) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
}

// --- map ---

const map = L.map("map").setView([38.95, -77.05], 10);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const clusterGroup = L.markerClusterGroup();
map.addLayer(clusterGroup);

let allFeatures = [];

function severityRadius(weight) {
  return 4 + Math.min(weight, 10) * 0.8;
}

function popupHtml(props) {
  const when = props.occurred_at ? fmtDateTime(props.occurred_at) : "Unknown";
  const categoryLabel = CATEGORY_LABELS[props.offense_category] || props.offense_category;
  return `
    <strong>${props.offense_raw || "Unknown offense"}</strong>
    <span class="badge ${props.offense_category}">${categoryLabel}</span><br>
    ${when}<br>
    ${JURISDICTION_LABELS[props.jurisdiction] || props.jurisdiction}
    ${props.area_name ? " &middot; " + props.area_name : ""}<br>
    ${props.block_address || ""}<br>
    ${props.case_number ? "Case #" + props.case_number + "<br>" : ""}
    Severity: ${props.severity_weight}/10
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

  for (const feature of allFeatures) {
    const props = feature.properties;
    if (jurisdiction && props.jurisdiction !== jurisdiction) continue;
    if (category && props.offense_category !== category) continue;
    if (props.severity_weight < minSeverity) continue;
    const occurred = new Date(props.occurred_at).getTime();
    if (Number.isNaN(occurred) || occurred < cutoff) continue;

    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: severityRadius(props.severity_weight),
      color: CATEGORY_COLORS[props.offense_category] || "#7f8c8d",
      fillColor: CATEGORY_COLORS[props.offense_category] || "#7f8c8d",
      fillOpacity: 0.85,
      weight: 1,
    });
    marker.bindPopup(popupHtml(props));
    clusterGroup.addLayer(marker);
    shown++;
  }

  document.getElementById("result-count").textContent = `${shown.toLocaleString()} incidents shown`;
}

async function initMap() {
  try {
    const geojson = await fetchJson("data/incidents.geojson");
    allFeatures = geojson.features;
    applyFilters();
  } catch (err) {
    document.getElementById("result-count").textContent = `Could not load incidents (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

["f-jurisdiction", "f-range", "f-category", "f-severity"].forEach(id =>
  document.getElementById(id).addEventListener("change", applyFilters)
);

populateCategoryFilter();
renderLegend();
renderSummary();
initMap();
