// Trends page: one period + granularity control row scoping every chart.
// Data source is data/trends.json: pre-aggregated daily counts by
// jurisdiction and category over the full history, so any period since
// 2016 works without incident-level data in the browser.

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

const CHART_INK = "#9fb0c3";
const CHART_GRID = "#1a2534";

Chart.defaults.color = CHART_INK;
Chart.defaults.borderColor = CHART_GRID;
Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';

// --------------------------------------------------------- state

let rows = [];          // [{date: "YYYY-MM-DD", jurisdiction, category, count}]
let minDate = "2016-07-01";
let maxDate = null;
const state = { from: null, to: null, granularity: "month" };
const charts = {};      // canvas id -> Chart instance

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
  return rows.filter(r => r.date >= state.from && r.date <= state.to);
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
  const cards = [
    { label: "Total Incidents", value: fmtNumber(total) },
    { label: "Washington DC", value: fmtNumber(byJur.dc || 0),
      sub: total ? `${((byJur.dc || 0) / total * 100).toFixed(0)}% of total` : "" },
    { label: "Montgomery County", value: fmtNumber(byJur.moco || 0),
      sub: total ? `${((byJur.moco || 0) / total * 100).toFixed(0)}% of total` : "" },
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
  const jurisdictions = ["dc", "moco"];

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
      plugins: { legend: { labels: { boxWidth: 18, boxHeight: 2 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 12, maxRotation: 0 }, grid: { display: false } },
        y: { beginAtZero: true, grid: { color: CHART_GRID } },
      },
    },
  });

  // caption: the peak bucket
  let peakIdx = -1, peakTotal = -1;
  buckets.forEach((b, i) => {
    const t = (datasets[0].data[i] || 0) + (datasets[1].data[i] || 0);
    if (t > peakTotal) { peakTotal = t; peakIdx = i; }
  });
  document.getElementById("caption-volume").textContent = peakIdx >= 0
    ? `Volume peaked ${state.granularity === "day" ? "on" : "in"} ${bucketLabel(buckets[peakIdx], state.granularity)} ` +
      `with ${peakTotal.toLocaleString()} reported incidents across both jurisdictions.`
    : "No incidents in the selected period.";

  // table view twin
  const tbl = document.getElementById("volume-table");
  tbl.innerHTML = `<table class="data-table">
    <thead><tr><th>Period</th><th class="num">DC</th><th class="num">MoCo</th><th class="num">Total</th></tr></thead>
    <tbody>${buckets.map((b, i) => `
      <tr><td>${esc(bucketLabel(b, state.granularity))}</td>
      <td class="num">${(datasets[0].data[i] || 0).toLocaleString()}</td>
      <td class="num">${(datasets[1].data[i] || 0).toLocaleString()}</td>
      <td class="num">${((datasets[0].data[i] || 0) + (datasets[1].data[i] || 0)).toLocaleString()}</td></tr>`).join("")}
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
    ? totalsBy(rows.filter(r => r.date >= prevFrom && r.date <= prevTo), r => r.category)
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
              return ["dc", "moco"].map(j =>
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
      `(${(totals[top] || 0).toLocaleString()} incidents). Hover a bar for the DC / Montgomery County split.`
    : "No incidents in the selected period.";

  const tbl = document.getElementById("category-table");
  tbl.innerHTML = `<table class="data-table">
    <thead><tr><th>Category</th><th class="num">DC</th><th class="num">MoCo</th><th class="num">Total</th></tr></thead>
    <tbody>${cats.map(c => `
      <tr><td>${esc(CATEGORY_LABELS[c])}</td>
      <td class="num">${(byJurCat[`dc|${c}`] || 0).toLocaleString()}</td>
      <td class="num">${(byJurCat[`moco|${c}`] || 0).toLocaleString()}</td>
      <td class="num">${(totals[c] || 0).toLocaleString()}</td></tr>`).join("")}
    </tbody></table>`;
}

function renderHeatmap(payload) {
  const grid = document.getElementById("heatmap");
  const counts = new Map(payload.rows.map(([w, h, c]) => [`${w}|${h}`, c]));
  const max = Math.max(1, ...payload.rows.map(r => r[2]));

  let html = `<div></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="col-label">${h}</div>`;

  let peak = { dow: 0, hour: 0, count: -1 };
  for (let dow = 0; dow < 7; dow++) {
    html += `<div class="row-label">${WEEKDAYS[dow]}</div>`;
    for (let h = 0; h < 24; h++) {
      const count = counts.get(`${dow}|${h}`) || 0;
      if (count > peak.count) peak = { dow, hour: h, count };
      const intensity = count / max;
      const bg = `rgba(94, 200, 242, ${0.05 + intensity * 0.85})`;
      html += `<div class="heatmap-cell" title="${WEEKDAYS[dow]} ${h}:00 - ${count} incidents" style="background:${bg}"></div>`;
    }
  }
  grid.innerHTML = html;

  document.getElementById("caption-heatmap").textContent =
    `Fixed to the last ${payload.window_days} days. Darker cells mean more reported incidents; ` +
    `the busiest slot is around ${peak.hour}:00 on ${WEEKDAYS_FULL[peak.dow]}s.`;
}

function renderAll() {
  const slice = sliceRows();
  renderPeriodCards(slice);
  renderVolume(slice);
  renderCategory(slice);
}

// ------------------------------------------------------------ wiring

function wireControls() {
  document.querySelectorAll("#preset-buttons button").forEach(b =>
    b.addEventListener("click", () => applyPreset(b.dataset.preset)));
  document.querySelectorAll("#gran-buttons button").forEach(b =>
    b.addEventListener("click", () => { setGranularity(b.dataset.gran); renderAll(); }));
  document.getElementById("f-from").addEventListener("change", onCustomRange);
  document.getElementById("f-to").addEventListener("change", onCustomRange);

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
    minDate = rows[0].date;
    maxDate = rows[rows.length - 1].date;
    for (const el of ["f-from", "f-to"]) document.getElementById(el).max = maxDate.slice(0, 7);

    wireControls();
    applyPreset("all");
    renderHeatmap(heatmap);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML("beforeend",
      `<p class="loading">Could not load trend data (${esc(err.message)}). Run the pipeline to generate site/data/.</p>`);
  }
}

init();
