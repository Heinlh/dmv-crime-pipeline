// Trends page: one period + granularity control row scoping every chart.
// Data source is data/trends.json: pre-aggregated daily counts by
// jurisdiction and category over the full history, so any period since
// 2016 works without incident-level data in the browser.

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// Mirrors the CSS tokens --text-2 and --grid in site/css/style.css
// (Chart.js needs literal values).
const CHART_INK = "#a6b0c9";
const CHART_GRID = "#1d1b30";

Chart.defaults.color = CHART_INK;
Chart.defaults.borderColor = CHART_GRID;
Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';

// --------------------------------------------------------- state

let rows = [];          // [{date: "YYYY-MM-DD", jurisdiction, category, count}]
let heatmapPayload = null;
let minDate = "2016-07-01";
let maxDate = null;
const state = { from: null, to: null, granularity: "month", jurisdiction: "", category: "" };
const charts = {};      // canvas id -> Chart instance

// Dayparts for the "when are incidents reported" heatmap. Night wraps
// past midnight; each hour is attributed to its own calendar day.
const DAYPARTS = [
  { label: "Morning", hint: "6 AM-12 PM", hours: [6, 7, 8, 9, 10, 11] },
  { label: "Afternoon", hint: "12-5 PM", hours: [12, 13, 14, 15, 16] },
  { label: "Evening", hint: "5-10 PM", hours: [17, 18, 19, 20, 21] },
  { label: "Night", hint: "10 PM-6 AM", hours: [22, 23, 0, 1, 2, 3, 4, 5] },
];
// Display rows Monday..Sunday; the export's weekday uses 0 = Sunday.
const DAY_ROWS = [1, 2, 3, 4, 5, 6, 0];

const ALL_JURISDICTIONS = Object.keys(JURISDICTION_LABELS);

// --------------------------------------------------- date helpers

const dayMs = 24 * 60 * 60 * 1000;
const toIso = d => d.toISOString().slice(0, 10);

function parseDay(iso) { return new Date(iso + "T00:00:00Z"); }

function bucketKey(iso, granularity) {
  if (granularity === "day") return iso;
  if (granularity === "month") return iso.slice(0, 7);
  // week: ISO Monday of the date's week
  const d = parseDay(iso);
  const shift = (d.getUTCDay() + 6) % 7;
  return toIso(new Date(d.getTime() - shift * dayMs));
}

function bucketLabel(key, granularity) {
  if (granularity === "month") {
    return new Date(key + "-01T00:00:00").toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }
  const label = fmtDate(key);
  return granularity === "week" ? `wk of ${label}` : label;
}

function enumerateBuckets(fromIso, toIso_, granularity) {
  const keys = [];
  if (granularity === "month") {
    let [y, m] = fromIso.slice(0, 7).split("-").map(Number);
    const end = toIso_.slice(0, 7);
    while (true) {
      const key = `${y}-${String(m).padStart(2, "0")}`;
      keys.push(key);
      if (key === end) break;
      m++; if (m > 12) { m = 1; y++; }
    }
    return keys;
  }
  const step = granularity === "week" ? 7 * dayMs : dayMs;
  let t = parseDay(bucketKey(fromIso, granularity)).getTime();
  const end = parseDay(toIso_).getTime();
  while (t <= end) { keys.push(toIso(new Date(t))); t += step; }
  return keys;
}

// ------------------------------------------------- period selection

function setPeriod(fromIso, toIso_, preset) {
  state.from = fromIso < minDate ? minDate : fromIso;
  state.to = toIso_ > maxDate ? maxDate : toIso_;
  document.getElementById("f-from").value = state.from.slice(0, 7);
  document.getElementById("f-to").value = state.to.slice(0, 7);
  document.querySelectorAll("#preset-buttons button").forEach(b =>
    b.classList.toggle("active", b.dataset.preset === preset));
}

function applyPreset(preset) {
  const today = maxDate;
  const t = parseDay(today);
  if (preset === "90d") {
    setPeriod(toIso(new Date(t.getTime() - 90 * dayMs)), today, preset);
    setGranularity("day");
  } else if (preset === "1y") {
    setPeriod(toIso(new Date(Date.UTC(t.getUTCFullYear() - 1, t.getUTCMonth(), t.getUTCDate()))), today, preset);
    setGranularity("week");
  } else if (preset === "ytd") {
    setPeriod(`${t.getUTCFullYear()}-01-01`, today, preset);
    setGranularity("week");
  } else {
    setPeriod(minDate, today, preset);
    setGranularity("month");
  }
  renderAll();
}

function setGranularity(granularity) {
  state.granularity = granularity;
  document.querySelectorAll("#gran-buttons button").forEach(b =>
    b.classList.toggle("active", b.dataset.gran === granularity));
}

function onCustomRange() {
  let fromMonth = document.getElementById("f-from").value;
  let toMonth = document.getElementById("f-to").value;
  if (!fromMonth || !toMonth) return;
  if (fromMonth > toMonth) [fromMonth, toMonth] = [toMonth, fromMonth];
  const [ty, tm] = toMonth.split("-").map(Number);
  const lastDay = toIso(new Date(Date.UTC(ty, tm, 0)));  // last day of "to" month
  setPeriod(`${fromMonth}-01`, lastDay, "custom");
  const spanDays = (parseDay(state.to) - parseDay(state.from)) / dayMs;
  setGranularity(spanDays <= 120 ? "day" : spanDays <= 550 ? "week" : "month");
  renderAll();
}

// ------------------------------------------------------ aggregation

function sliceRows() {
  return rows.filter(r =>
    r.date >= state.from && r.date <= state.to &&
    (!state.jurisdiction || r.jurisdiction === state.jurisdiction) &&
    (!state.category || r.category === state.category));
}

// Sum counts into {bucket -> {group -> count}} for a grouping function.
function aggregate(slice, groupFn) {
  const out = new Map();
  for (const r of slice) {
    const bucket = bucketKey(r.date, state.granularity);
    let groups = out.get(bucket);
    if (!groups) { groups = {}; out.set(bucket, groups); }
    const g = groupFn(r);
    groups[g] = (groups[g] || 0) + r.count;
  }
  return out;
}

function totalsBy(slice, keyFn) {
  const out = {};
  for (const r of slice) out[keyFn(r)] = (out[keyFn(r)] || 0) + r.count;
  return out;
}

// ---------------------------------------------------------- charts

function renderChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function renderPeriodCards(slice) {
  const total = slice.reduce((s, r) => s + r.count, 0);
  const byJur = totalsBy(slice, r => r.jurisdiction);
  const spanDays = Math.max(1, Math.round((parseDay(state.to) - parseDay(state.from)) / dayMs) + 1);
  const jurs = state.jurisdiction ? [state.jurisdiction] : ALL_JURISDICTIONS;
  const cards = [
    { label: "Total Incidents", value: fmtNumber(total) },
    ...jurs.map(j => ({
      label: jurisdictionLabel(j), value: fmtNumber(byJur[j] || 0),
      sub: total ? `${((byJur[j] || 0) / total * 100).toFixed(0)}% of total` : "",
    })),
    { label: "Daily Average", value: fmtNumber(Math.round(total / spanDays)) },
  ];
  document.getElementById("period-cards").innerHTML = cards.map(k => `
    <div class="card">
      <div class="label">${esc(k.label)}</div>
      <div class="value">${esc(k.value)}</div>
      <div class="sub">${esc(k.sub || "")}</div>
    </div>
  `).join("");

  document.getElementById("period-readout").textContent =
    `${fmtDate(state.from)} to ${fmtDate(state.to)}`;
}

function renderVolume(slice) {
  const buckets = enumerateBuckets(state.from, state.to, state.granularity);
  const byBucket = aggregate(slice, r => r.jurisdiction);
  const jurisdictions = state.jurisdiction ? [state.jurisdiction] : ALL_JURISDICTIONS;

  const datasets = jurisdictions.map(j => ({
    label: jurisdictionLabel(j),
    data: buckets.map(b => (byBucket.get(b) || {})[j] || 0),
    borderColor: JURISDICTION_COLORS[j],
    backgroundColor: JURISDICTION_COLORS[j],
    borderWidth: 2,
    tension: 0.2,
    pointRadius: 0,
    pointHoverRadius: 4,
  }));

  renderChart("chart-volume", {
    type: "line",
    data: { labels: buckets.map(b => bucketLabel(b, state.granularity)), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: jurisdictions.length > 1, labels: { boxWidth: 18, boxHeight: 2 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 12, maxRotation: 0 }, grid: { display: false } },
        y: { beginAtZero: true, grid: { color: CHART_GRID } },
      },
    },
  });

  // caption: the peak bucket. Hover behavior on 3+ series stays "one
  // tooltip, every series" via interaction mode index.
  let peakIdx = -1, peakTotal = -1;
  buckets.forEach((b, i) => {
    const t = datasets.reduce((sum, d) => sum + (d.data[i] || 0), 0);
    if (t > peakTotal) { peakTotal = t; peakIdx = i; }
  });
  const scopeText = state.jurisdiction ? `in ${jurisdictionLabel(state.jurisdiction)}` : "across all jurisdictions";
  document.getElementById("caption-volume").textContent = peakIdx >= 0 && peakTotal > 0
    ? `Volume peaked ${state.granularity === "day" ? "on" : "in"} ${bucketLabel(buckets[peakIdx], state.granularity)} ` +
      `with ${peakTotal.toLocaleString()} reported incidents ${scopeText}.`
    : "No incidents match the selected filters.";

  // table view twin
  const tbl = document.getElementById("volume-table");
  tbl.innerHTML = `<table class="data-table">
    <thead><tr><th>Period</th>${jurisdictions.map(j =>
      `<th class="num">${esc(jurisdictionLabel(j))}</th>`).join("")}<th class="num">Total</th></tr></thead>
    <tbody>${buckets.map((b, i) => {
      const vals = datasets.map(d => d.data[i] || 0);
      const total = vals.reduce((sum, v) => sum + v, 0);
      return `<tr><td>${esc(bucketLabel(b, state.granularity))}</td>
        ${vals.map(v => `<td class="num">${v.toLocaleString()}</td>`).join("")}
        <td class="num">${total.toLocaleString()}</td></tr>`;
    }).join("")}
    </tbody></table>`;
}

function renderCategory(slice) {
  const totals = totalsBy(slice, r => r.category);
  const byJurCat = {};
  for (const r of slice) {
    byJurCat[`${r.jurisdiction}|${r.category}`] =
      (byJurCat[`${r.jurisdiction}|${r.category}`] || 0) + r.count;
  }
  const cats = CATEGORY_ORDER.filter(c => totals[c]).sort((a, b) => (totals[b] || 0) - (totals[a] || 0));

  // delta cards vs the preceding period of equal length; only when the
  // warehouse fully covers that preceding window, otherwise the delta
  // compares against a mostly-empty period and reads as a huge fake jump
  const spanMs = parseDay(state.to) - parseDay(state.from) + dayMs;
  const prevFrom = toIso(new Date(parseDay(state.from).getTime() - spanMs));
  const prevTo = toIso(new Date(parseDay(state.from).getTime() - dayMs));
  const prevCovered = prevFrom >= minDate;
  const prevTotals = prevCovered
    ? totalsBy(rows.filter(r =>
        r.date >= prevFrom && r.date <= prevTo &&
        (!state.jurisdiction || r.jurisdiction === state.jurisdiction) &&
        (!state.category || r.category === state.category)), r => r.category)
    : {};

  document.getElementById("category-cards").innerHTML = cats.map(cat => {
    const cur = totals[cat] || 0, prev = prevTotals[cat] || 0;
    const pct = prevCovered && prev ? (cur - prev) / prev * 100 : null;
    return `
      <div class="card">
        <div class="label">${esc(CATEGORY_LABELS[cat])}</div>
        <div class="value">${cur.toLocaleString()}</div>
        <div class="sub ${pct > 0 ? "up" : pct < 0 ? "down" : ""}">${pct === null ? "no comparable prior period" : esc(fmtPct(pct)) + " vs. preceding period"}</div>
      </div>
    `;
  }).join("");

  renderChart("chart-category", {
    type: "bar",
    data: {
      labels: cats.map(c => CATEGORY_LABELS[c]),
      datasets: [{
        data: cats.map(c => totals[c] || 0),
        backgroundColor: cats.map(c => CATEGORY_COLORS[c]),
        borderRadius: 4,
        borderSkipped: "start",
        maxBarThickness: 24,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.parsed.x.toLocaleString()} incidents`,
            afterLabel: ctx => {
              const cat = cats[ctx.dataIndex];
              return ALL_JURISDICTIONS.map(j =>
                `${jurisdictionLabel(j)}: ${(byJurCat[`${j}|${cat}`] || 0).toLocaleString()}`).join("\n");
            },
          },
        },
      },
      scales: {
        x: { beginAtZero: true, grid: { color: CHART_GRID } },
        y: { grid: { display: false } },
      },
    },
  });

  const top = cats[0];
  document.getElementById("caption-category").textContent = top
    ? `${CATEGORY_LABELS[top]} was the most common category in this period ` +
      `(${(totals[top] || 0).toLocaleString()} incidents). Hover a bar for the per-jurisdiction split.`
    : "No incidents in the selected period.";

  const tableJurs = state.jurisdiction ? [state.jurisdiction] : ALL_JURISDICTIONS;
  const tbl = document.getElementById("category-table");
  tbl.innerHTML = `<table class="data-table">
    <thead><tr><th>Category</th>${tableJurs.map(j =>
      `<th class="num">${esc(jurisdictionLabel(j))}</th>`).join("")}<th class="num">Total</th></tr></thead>
    <tbody>${cats.map(c => `
      <tr><td>${esc(CATEGORY_LABELS[c])}</td>
      ${tableJurs.map(j => `<td class="num">${(byJurCat[`${j}|${c}`] || 0).toLocaleString()}</td>`).join("")}
      <td class="num">${(totals[c] || 0).toLocaleString()}</td></tr>`).join("")}
    </tbody></table>`;
}

function renderHeatmap() {
  if (!heatmapPayload) return;
  const grid = document.getElementById("heatmap");

  // Sum weekday x hour counts (filtered by jurisdiction/category) into
  // a 7-day x 4-daypart grid.
  const hourToDaypart = {};
  DAYPARTS.forEach((dp, i) => dp.hours.forEach(h => { hourToDaypart[h] = i; }));
  const cells = Array.from({ length: 7 }, () => [0, 0, 0, 0]);
  for (const [weekday, hour, jurisdiction, category, count] of heatmapPayload.rows) {
    if (state.jurisdiction && jurisdiction !== state.jurisdiction) continue;
    if (state.category && category !== state.category) continue;
    cells[weekday][hourToDaypart[hour]] += count;
  }
  const max = Math.max(1, ...cells.flat());

  let html = `<div></div>` + DAYPARTS.map(dp =>
    `<div class="col-label">${dp.label}<span class="hint">${dp.hint}</span></div>`).join("");

  let peak = { dow: 1, part: 0, count: -1 };
  let quiet = { dow: 1, part: 0, count: Infinity };
  for (const dow of DAY_ROWS) {
    html += `<div class="row-label">${WEEKDAYS[dow]}</div>`;
    DAYPARTS.forEach((dp, part) => {
      const count = cells[dow][part];
      if (count > peak.count) peak = { dow, part, count };
      if (count < quiet.count) quiet = { dow, part, count };
      const intensity = count / max;
      const bg = `rgba(77, 227, 255, ${0.05 + intensity * 0.85})`;
      const ink = intensity > 0.55 ? "#0a0a14" : "var(--text)";
      const readout = `${WEEKDAYS_FULL[dow]} ${dp.label.toLowerCase()} (${dp.hint}): ${count.toLocaleString()} reported incidents`;
      html += `<div class="heatmap-cell" role="img" tabindex="0" aria-label="${readout}"
        title="${readout}" style="background:${bg};color:${ink}">${count.toLocaleString()}</div>`;
    });
  }
  grid.innerHTML = html;

  const caption = document.getElementById("caption-heatmap");
  if (peak.count <= 0) {
    caption.textContent = "No reported incidents match these filters in the last 90 days.";
    return;
  }
  const peakPart = DAYPARTS[peak.part], quietPart = DAYPARTS[quiet.part];
  caption.textContent =
    `Reported incidents were most common on ${WEEKDAYS_FULL[peak.dow]} ${peakPart.label.toLowerCase()}s ` +
    `(${peakPart.hint}, ${peak.count.toLocaleString()} incidents). The quietest period was ` +
    `${WEEKDAYS_FULL[quiet.dow]} ${quietPart.label.toLowerCase()}s (${quietPart.hint}, ${quiet.count.toLocaleString()}).`;
}

function renderAll() {
  const slice = sliceRows();
  renderPeriodCards(slice);
  renderVolume(slice);
  renderCategory(slice);
  renderHeatmap();
}

// ------------------------------------------------------------ wiring

function wireControls() {
  document.querySelectorAll("#preset-buttons button").forEach(b =>
    b.addEventListener("click", () => applyPreset(b.dataset.preset)));
  document.querySelectorAll("#gran-buttons button").forEach(b =>
    b.addEventListener("click", () => { setGranularity(b.dataset.gran); renderAll(); }));
  document.getElementById("f-from").addEventListener("change", onCustomRange);
  document.getElementById("f-to").addEventListener("change", onCustomRange);

  populateJurisdictionFilter("f-jurisdiction");
  const categorySelect = document.getElementById("f-category");
  for (const cat of CATEGORY_ORDER) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = CATEGORY_LABELS[cat];
    categorySelect.appendChild(opt);
  }
  document.getElementById("f-jurisdiction").addEventListener("change", e => {
    state.jurisdiction = e.target.value;
    renderAll();
  });
  categorySelect.addEventListener("change", e => {
    state.category = e.target.value;
    renderAll();
  });

  for (const [btnId, tblId, chartSel] of [
    ["toggle-volume-table", "volume-table", "#chart-volume"],
    ["toggle-category-table", "category-table", "#chart-category"],
  ]) {
    const btn = document.getElementById(btnId);
    btn.addEventListener("click", () => {
      const tbl = document.getElementById(tblId);
      const show = tbl.hidden;
      tbl.hidden = !show;
      document.querySelector(chartSel).closest(".chart-wrap").style.display = show ? "none" : "";
      btn.setAttribute("aria-pressed", String(show));
      btn.textContent = show ? "CHART" : "TABLE";
    });
  }
}

async function init() {
  try {
    const [trends, heatmap] = await Promise.all([
      fetchJson("data/trends.json"),
      fetchJson("data/heatmap.json"),
    ]);
    rows = trends.rows.map(([date, jurisdiction, category, count]) =>
      ({ date, jurisdiction, category, count }));
    if (!rows.length) throw new Error("trends.json is empty");
    heatmapPayload = heatmap;
    minDate = rows[0].date;
    maxDate = rows[rows.length - 1].date;
    for (const el of ["f-from", "f-to"]) document.getElementById(el).max = maxDate.slice(0, 7);

    wireControls();
    applyPreset("all");
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML("beforeend",
      `<p class="loading">Could not load trend data (${esc(err.message)}). Run the pipeline to generate site/data/.</p>`);
  }
}

init();
