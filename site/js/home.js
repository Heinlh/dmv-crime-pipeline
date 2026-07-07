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
      { label: "Last 24 Hours", value: fmtNumber(s.new_incidents_24h), count: true },
      { label: "Last 7 Days", value: fmtNumber(s.last_7d_count), count: true,
        sub: s.pct_change_7d === null ? "" : `${fmtPct(s.pct_change_7d)} vs. previous 7 days`, subClass: deltaClass },
      { label: "Most Common (7d)", value: topCategoryLabel },
      { label: "Records Since 2016", value: fmtNumber(s.total_records), count: true,
        sub: s.data_start_date ? `warehouse starts ${fmtDate(s.data_start_date)}` : "" },
    ];
    cards.innerHTML = kpis.map(k => `
      <div class="card hud">
        <div class="label">${esc(k.label)}</div>
        <div class="value" data-countup="${k.count ? esc(k.value) : ""}">${k.count ? "0" : esc(k.value)}</div>
        <div class="sub ${k.subClass || ""}">${esc(k.sub || "")}</div>
      </div>
    `).join("");
    cards.querySelectorAll("[data-countup]").forEach(el => {
      if (el.dataset.countup) countUp(el, el.dataset.countup);
    });
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

const map = L.map("map", { zoomControl: false }).setView([38.95, -77.05], 10);
// zoom on the top-right so the search overlay owns the top-left corner
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  maxZoom: 19,
}).addTo(map);

const clusterGroup = L.markerClusterGroup({ maxClusterRadius: 46 });
map.addLayer(clusterGroup);

// case-number lookup term driving the map search; when set, it replaces
// the dropdown filters entirely (a case number identifies one incident)
let mapSearchTerm = "";
let shownLatLngs = [];       // latlngs of the currently shown markers (for fit)
let soleResultMarker = null; // the marker when exactly one incident is shown
let incidentWindowDays = 90; // refreshed from incidents.json on load

// Hotspot polygons live in their own pane below the marker overlay so
// dots and clusters always draw (and click) above the shading.
map.createPane("hotspots").style.zIndex = 390;
const hotspotGroup = L.layerGroup().addTo(map);

let allIncidents = [];
let hexData = null;

// --- hotspot hex layer ---

function renderHotspots() {
  hotspotGroup.clearLayers();
  const windowDays = document.getElementById("f-hotspots").value;
  if (!windowDays || !hexData || !hexData.windows[windowDays]) return;
  const cells = hexData.windows[windowDays];
  if (!cells.length) return;
  const max = cells[0][1]; // exporter sorts by count desc
  for (const [hex, count, topCat] of cells) {
    const boundary = hexData.boundaries[hex];
    if (!boundary) continue;
    // single-hue cyan ramp; sqrt-ish exponent keeps mid-range cells
    // visible without letting the top cell wash out the basemap
    const intensity = Math.pow(count / max, 0.6);
    const polygon = L.polygon(boundary, {
      pane: "hotspots",
      stroke: true,
      color: "rgba(77, 227, 255, 0.35)",
      weight: 1,
      fillColor: "#4de3ff",
      fillOpacity: 0.06 + 0.30 * intensity,
    });
    polygon.bindTooltip(
      `${count.toLocaleString()} incident${count === 1 ? "" : "s"} in the last ${windowDays} days` +
      ` &middot; mostly ${esc(categoryLabel(topCat))}`,
      { direction: "top", opacity: 1, sticky: true });
    hotspotGroup.addLayer(polygon);
  }
}

async function initHotspots() {
  try {
    hexData = await fetchJson("data/hexes.json");
    renderHotspots();
  } catch (err) {
    // hotspots are an enhancement; the map stands without them
    console.warn("hotspots unavailable:", err.message);
  }
}

// --- day-by-day playback (time scrubber) ---
// When engaged, the map shows a single day at a time over the last 30
// days instead of the cumulative date-range filter. Autoplay advances
// one day per second and is disabled entirely under reduced motion
// (the slider still steps manually).

const playback = {
  active: false,
  days: [],   // ISO dates, oldest first
  index: 29,
  timer: null,
};

function playbackDates() {
  // anchor the window to the newest incident so the scrubber always
  // covers days that actually have data
  let maxTs = 0;
  for (const inc of allIncidents) if (inc._ts > maxTs) maxTs = inc._ts;
  const end = maxTs ? new Date(maxTs) : new Date();
  const days = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(end.getTime() - i * 86400000);
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

function setPlaybackIndex(i) {
  playback.index = Math.max(0, Math.min(29, i));
  document.getElementById("pb-slider").value = playback.index;
  const day = playback.days[playback.index];
  document.getElementById("pb-date").textContent = day ? fmtDate(day) : "";
  applyFilters();
}

function stopAutoplay() {
  if (playback.timer) { clearInterval(playback.timer); playback.timer = null; }
  const play = document.getElementById("pb-play");
  play.innerHTML = "&#9654;";
  play.setAttribute("aria-pressed", "false");
}

function togglePlayback(on) {
  playback.active = on;
  const toggle = document.getElementById("pb-toggle");
  const slider = document.getElementById("pb-slider");
  const play = document.getElementById("pb-play");
  toggle.setAttribute("aria-pressed", String(on));
  toggle.classList.toggle("on", on);
  slider.disabled = !on;
  play.hidden = !on || REDUCED_MOTION;
  if (on) {
    playback.days = playbackDates();
    setPlaybackIndex(playback.index);
  } else {
    stopAutoplay();
    document.getElementById("pb-date").textContent = "all days in range";
    applyFilters();
  }
}

function startAutoplay() {
  const play = document.getElementById("pb-play");
  if (playback.timer) { stopAutoplay(); return; }
  if (playback.index >= 29) setPlaybackIndex(0);
  play.innerHTML = "&#10074;&#10074;";
  play.setAttribute("aria-pressed", "true");
  playback.timer = setInterval(() => {
    if (playback.index >= 29) { stopAutoplay(); return; }
    setPlaybackIndex(playback.index + 1);
  }, 1000);
}

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
  const playbackDay = playback.active ? playback.days[playback.index] : null;

  // Case lookup overrides the dropdown filters entirely: a case number
  // identifies one incident, so date range and category must not be
  // able to hide it (that interaction is exactly what made free-text
  // search confusing here).
  const caseQuery = normalizeCase(mapSearchTerm);

  clusterGroup.clearLayers();
  let shown = 0;
  const countsByCategory = {};
  shownLatLngs = [];
  soleResultMarker = null;

  for (const inc of allIncidents) {
    if (inc.latitude === null || inc.longitude === null) continue;
    if (caseQuery) {
      if (!matchesCase(inc, caseQuery)) continue;
    } else {
      if (jurisdiction && inc.jurisdiction !== jurisdiction) continue;
      if (category && inc.offense_category !== category) continue;
      if (inc.severity_weight < minSeverity) continue;
      if (playbackDay) {
        if ((inc.occurred_at || "").slice(0, 10) !== playbackDay) continue;
      } else if (Number.isNaN(inc._ts) || inc._ts < cutoff) continue;
    }

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
    shownLatLngs.push([inc.latitude, inc.longitude]);
    soleResultMarker = shown === 0 ? marker : null;
    countsByCategory[inc.offense_category] = (countsByCategory[inc.offense_category] || 0) + 1;
    shown++;
  }

  const scope = caseQuery ? ` for case "${mapSearchTerm}"`
    : playbackDay ? ` on ${fmtDate(playbackDay)}` : "";
  document.getElementById("result-count").textContent =
    `${shown.toLocaleString()} incident${shown === 1 ? "" : "s"} shown${scope}`;
  renderLegend(countsByCategory);

  writeHashState({
    j: jurisdiction,
    days: String(rangeDays) === "30" ? "" : String(rangeDays),
    cat: category,
    sev: minSeverity === 1 ? "" : String(minSeverity),
    q: mapSearchTerm,
    hex: document.getElementById("f-hotspots").value === "30" ? "" : document.getElementById("f-hotspots").value || "off",
    day: playbackDay || "",
  });
}

// The map search is a CASE NUMBER lookup only (free-text place and
// offense matching lives on the Events page; on the map it fought the
// dropdown filters and produced confusing result sets). Case numbers
// are compared with the leading '#', spaces, and dashes stripped.
function normalizeCase(value) {
  return String(value ?? "").toUpperCase().replace(/[#\s-]/g, "");
}

function matchesCase(inc, normalizedQuery) {
  const caseNo = normalizeCase(inc.case_number);
  return caseNo !== "" && caseNo.includes(normalizedQuery);
}

// Pan/zoom the map to frame the currently shown markers. Capped zoom so
// a single result does not slam to street level; a no-op when empty.
function fitToResults() {
  if (!shownLatLngs.length) return;
  if (shownLatLngs.length === 1) {
    map.setView(shownLatLngs[0], 15, { animate: true });
  } else {
    map.fitBounds(shownLatLngs, { padding: [40, 40], maxZoom: 15, animate: true });
  }
}

// -------------------------------------------------- map search overlay
// A case-number lookup over the loaded incidents (no third-party
// geocoder, so nothing a visitor types ever leaves the browser). Typing
// part of a case number suggests matching cases; selecting one shows
// exactly that incident, frames it, and opens its summary card. The
// lookup ignores the dropdown filters, since a case number identifies
// one incident. Free-text place/offense search lives on the Events page.

const mapSearch = (() => {
  const wrap = document.getElementById("map-search");
  const input = document.getElementById("map-search-input");
  const list = document.getElementById("map-search-list");
  const clearBtn = document.getElementById("map-search-clear");
  let items = [];   // currently rendered suggestions, in display order
  let active = -1;

  // keep clicks and scrolls inside the box from panning the map
  if (window.L && L.DomEvent) {
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
  }

  function compute(normalizedQuery) {
    const matched = [];
    for (const inc of allIncidents) {
      if (!matchesCase(inc, normalizedQuery)) continue;
      matched.push({
        value: String(inc.case_number),
        label: `#${inc.case_number}`,
        hint: `${incidentTitle(inc)} · ${jurisdictionLabel(inc.jurisdiction)}`,
        dot: CATEGORY_COLORS[inc.offense_category] || CATEGORY_COLORS.other,
      });
      if (matched.length >= 8) break;
    }
    return matched;
  }

  function render() {
    const rawQ = input.value.trim();
    const normalizedQuery = normalizeCase(rawQ);
    clearBtn.hidden = !rawQ;
    if (normalizedQuery.length < 3) {
      // case numbers are long; don't dump suggestions on a keystroke or two
      list.innerHTML = `<li class="map-search-empty">Type at least 3 characters of a case number (e.g. #20261880131).</li>`;
      list.hidden = !rawQ && document.activeElement !== input;
      input.setAttribute("aria-expanded", "true");
      items = [];
      return;
    }
    items = compute(normalizedQuery);
    if (!items.length) {
      list.innerHTML = `<li class="map-search-empty">No case number matching "${esc(rawQ)}" in the last ${incidentWindowDays} days of mapped incidents.</li>`;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      return;
    }
    list.innerHTML = items.map((it, i) => `
      <li class="map-search-item ${i === active ? "active" : ""}" role="option"
        aria-selected="${i === active}" data-i="${i}">
        <span class="dot" style="background:${esc(it.dot)}"></span>
        <span class="msi-label mono">${esc(it.label)}</span>
        <span class="msi-meta">${esc(it.hint)}</span></li>`).join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    list.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
  }

  function choose(it) {
    if (!it) return;
    mapSearchTerm = it.value;
    input.value = `#${it.value}`.replace(/^##/, "#");
    clearBtn.hidden = false;
    close();
    input.blur();
    applyFilters();
    if (soleResultMarker) {
      // One exact hit. In lookup mode only the matches are on the map,
      // so a sole marker is never clustered with anything: a plain
      // un-animated setView then openPopup is reliable, and unlike
      // markercluster's zoomToShowLayer it leaves no pending animation
      // callback to dangle if the user filters again mid-zoom.
      const marker = soleResultMarker;
      map.setView(marker.getLatLng(), 16, { animate: false });
      setTimeout(() => { if (marker._map) marker.openPopup(); }, 80);
    } else {
      fitToResults();
    }
  }

  function clear() {
    input.value = "";
    mapSearchTerm = "";
    clearBtn.hidden = true;
    close();
    map.closePopup();
    applyFilters();
  }

  input.addEventListener("focus", render);
  input.addEventListener("input", () => { active = -1; render(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && items[active]) choose(items[active]);
      else if (items.length === 1) choose(items[0]);
      else if (input.value.trim()) choose({ value: input.value.trim().replace(/^#/, "") });
    } else if (e.key === "Escape") {
      if (!list.hidden) close(); else clear();
    }
  });
  list.addEventListener("mousedown", (e) => {
    // mousedown (not click) so it fires before the input blur closes the list
    const li = e.target.closest(".map-search-item");
    if (li) { e.preventDefault(); choose(items[parseInt(li.dataset.i, 10)]); }
  });
  clearBtn.addEventListener("click", clear);
  document.addEventListener("click", (e) => {
    if (!wrap.contains(e.target)) close();
  });

  return {
    init() {
      if (mapSearchTerm) { input.value = `#${mapSearchTerm}`; clearBtn.hidden = false; }
    },
  };
})();

async function initMap() {
  try {
    const { incidents, windowDays } = await fetchIncidents();
    allIncidents = incidents;
    if (windowDays) incidentWindowDays = windowDays;
    if (pendingPlaybackDay) restorePlaybackDay(pendingPlaybackDay);
    mapSearch.init();
    applyFilters();
    if (mapSearchTerm) fitToResults();
  } catch (err) {
    document.getElementById("result-count").textContent =
      `Could not load incidents (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

// --- restore state from a shared URL, then wire everything up ---

let pendingPlaybackDay = null;

function restorePlaybackDay(day) {
  playback.days = playbackDates();
  const idx = playback.days.indexOf(day);
  if (idx === -1) return;
  playback.index = idx;
  togglePlayback(true);
}

function applyHashState() {
  const h = readHashState();
  const setIf = (id, value, valid) => {
    if (value !== undefined && (!valid || valid.includes(value))) {
      document.getElementById(id).value = value;
    }
  };
  setIf("f-jurisdiction", h.j, Object.keys(JURISDICTION_LABELS));
  setIf("f-range", h.days, ["1", "7", "30", "90"]);
  setIf("f-category", h.cat, CATEGORY_ORDER);
  setIf("f-severity", h.sev, ["1", "4", "7"]);
  if (h.hex === "off") document.getElementById("f-hotspots").value = "";
  else setIf("f-hotspots", h.hex, ["7", "30"]);
  if (h.day && /^\d{4}-\d{2}-\d{2}$/.test(h.day)) pendingPlaybackDay = h.day;
  if (h.q) mapSearchTerm = h.q;  // applied once incidents load in initMap
}

["f-jurisdiction", "f-range", "f-category", "f-severity"].forEach(id =>
  document.getElementById(id).addEventListener("change", applyFilters)
);
document.getElementById("f-hotspots").addEventListener("change", () => {
  renderHotspots();
  applyFilters(); // refresh the shared-URL hash
});
document.getElementById("pb-toggle").addEventListener("click", () =>
  togglePlayback(!playback.active));
document.getElementById("pb-slider").addEventListener("input", (e) => {
  stopAutoplay();
  setPlaybackIndex(parseInt(e.target.value, 10));
});
document.getElementById("pb-play").addEventListener("click", startAutoplay);

populateJurisdictionFilter("f-jurisdiction");
populateCategoryFilter();
applyHashState();
renderLegend(null);
renderSummary();
initMap();
initHotspots();
