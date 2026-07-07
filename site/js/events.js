// Events page: client-side search and filtering over the incident-level
// window (data/incidents.json, last ~90 days). Results render as summary
// cards, paginated with a Load More button.

const PAGE_SIZE = 60;

let allIncidents = [];
let filtered = [];
let shownCount = 0;

// --------------------------------------------------------- filtering

function matchesSearch(inc, terms) {
  if (!terms.length) return true;
  const haystack = [
    incidentTitle(inc), inc.offense_raw, inc.block_address, inc.area_name,
    inc.city, inc.case_number, categoryLabel(inc.offense_category),
    jurisdictionLabel(inc.jurisdiction),
  ].join(" ").toLowerCase();
  return terms.every(t => haystack.includes(t));
}

function applyFilters() {
  const terms = document.getElementById("f-search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const jurisdiction = document.getElementById("f-jurisdiction").value;
  const category = document.getElementById("f-category").value;
  const rangeDays = parseInt(document.getElementById("f-range").value, 10);
  const sort = document.getElementById("f-sort").value;
  const cutoff = Date.now() - rangeDays * 24 * 60 * 60 * 1000;

  filtered = allIncidents.filter(inc =>
    (!jurisdiction || inc.jurisdiction === jurisdiction) &&
    (!category || inc.offense_category === category) &&
    !(Number.isNaN(inc._ts) || inc._ts < cutoff) &&
    matchesSearch(inc, terms)
  );

  if (sort === "oldest") filtered.sort((a, b) => a._ts - b._ts);
  else if (sort === "severity") filtered.sort((a, b) => b.severity_weight - a.severity_weight || b._ts - a._ts);
  else filtered.sort((a, b) => b._ts - a._ts);

  shownCount = 0;
  document.getElementById("event-grid").innerHTML = "";
  renderNextPage();

  writeHashState({
    q: document.getElementById("f-search").value.trim(),
    j: jurisdiction,
    cat: category,
    days: String(rangeDays) === "90" ? "" : String(rangeDays),
    sort: sort === "newest" ? "" : sort,
  });
}

// --------------------------------------------------------- rendering

function cardHtml(inc) {
  const cat = inc.offense_category;
  return `
    <article class="event-card ${esc(cat)}">
      <div class="head">
        <span class="badge ${esc(cat)}">${esc(categoryLabel(cat))}</span>
        <span class="when">${esc(fmtDateTime(inc.occurred_at))}</span>
      </div>
      <div class="title">${esc(incidentTitle(inc))}</div>
      <div class="meta">
        ${esc(inc.block_address || "Location withheld")}<br>
        ${esc(jurisdictionLabel(inc.jurisdiction))}${inc.area_name ? " &middot; " + esc(inc.area_name) : ""}<br>
        <span class="case">AGENCY LABEL: ${esc(inc.offense_raw || "n/a")}${inc.case_number ? ` &middot; CASE #${esc(inc.case_number)}` : ""}</span>
      </div>
    </article>
  `;
}

function renderNextPage() {
  const grid = document.getElementById("event-grid");
  const page = filtered.slice(shownCount, shownCount + PAGE_SIZE);
  grid.insertAdjacentHTML("beforeend", page.map(cardHtml).join(""));
  shownCount += page.length;

  document.getElementById("result-count").textContent =
    filtered.length
      ? `${shownCount.toLocaleString()} of ${filtered.length.toLocaleString()} incidents`
      : "";
  document.getElementById("empty-state").hidden = filtered.length > 0;
  document.getElementById("load-more").hidden = shownCount >= filtered.length;
}

// ------------------------------------------------------------ wiring

function populateCategoryFilter() {
  const select = document.getElementById("f-category");
  for (const cat of CATEGORY_ORDER) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = CATEGORY_LABELS[cat];
    select.appendChild(opt);
  }
}

let searchTimer = null;
function wireControls() {
  document.getElementById("f-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(applyFilters, 150);
  });
  ["f-jurisdiction", "f-category", "f-range", "f-sort"].forEach(id =>
    document.getElementById(id).addEventListener("change", applyFilters));
  document.getElementById("load-more").addEventListener("click", renderNextPage);
}

function applyHashState() {
  const h = readHashState();
  if (h.q) document.getElementById("f-search").value = h.q;
  if (h.j && JURISDICTION_LABELS[h.j]) document.getElementById("f-jurisdiction").value = h.j;
  if (h.cat && CATEGORY_ORDER.includes(h.cat)) document.getElementById("f-category").value = h.cat;
  if (["7", "30", "90"].includes(h.days)) document.getElementById("f-range").value = h.days;
  if (["oldest", "severity"].includes(h.sort)) document.getElementById("f-sort").value = h.sort;
}

async function init() {
  populateJurisdictionFilter("f-jurisdiction");
  populateCategoryFilter();
  wireControls();
  applyHashState();
  try {
    const { incidents, windowDays } = await fetchIncidents();
    allIncidents = incidents;
    if (windowDays) {
      document.getElementById("events-subtitle").firstChild.textContent =
        `Search and filter individual incidents from the last ${windowDays} days. Older activity is summarized on the `;
    }
    applyFilters();
  } catch (err) {
    document.getElementById("result-count").textContent =
      `Could not load incidents (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

init();
