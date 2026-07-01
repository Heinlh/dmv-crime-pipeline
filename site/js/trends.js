const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };
const JURISDICTION_COLORS = { dc: "#3d8bfd", moco: "#e67e22" };
const CATEGORY_COLORS = {
  violent: "#c0392b", property: "#d68910", vehicle: "#8e44ad",
  drug: "#16a085", society: "#2874a6", other: "#7f8c8d",
};
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status} loading ${path}`);
  return res.json();
}

function renderDailyChart(rows) {
  const dates = [...new Set(rows.map(r => r.date))].sort();
  const jurisdictions = [...new Set(rows.map(r => r.jurisdiction))];
  const byKey = new Map(rows.map(r => [`${r.jurisdiction}|${r.date}`, r.count]));

  const datasets = jurisdictions.map(j => ({
    label: JURISDICTION_LABELS[j] || j,
    data: dates.map(d => byKey.get(`${j}|${d}`) || 0),
    borderColor: JURISDICTION_COLORS[j] || "#666",
    backgroundColor: JURISDICTION_COLORS[j] || "#666",
    tension: 0.15,
    pointRadius: 0,
  }));

  new Chart(document.getElementById("chart-daily"), {
    type: "line",
    data: { labels: dates, datasets },
    options: { responsive: true, maintainAspectRatio: false, scales: { x: { ticks: { maxTicksLimit: 12 } } } },
  });

  return { dates, jurisdictions, byKey };
}

function renderJurisdictionChart(jurisdictions, byKey, dates) {
  const totals = jurisdictions.map(j => dates.reduce((sum, d) => sum + (byKey.get(`${j}|${d}`) || 0), 0));
  new Chart(document.getElementById("chart-jurisdiction"), {
    type: "bar",
    data: {
      labels: jurisdictions.map(j => JURISDICTION_LABELS[j] || j),
      datasets: [{ label: "Incidents (last 90 days)", data: totals, backgroundColor: jurisdictions.map(j => JURISDICTION_COLORS[j] || "#666") }],
    },
    options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } } },
  });
}

function renderCategoryChart(rows) {
  const categories = [...new Set(rows.map(r => r.offense_category))];
  const jurisdictions = [...new Set(rows.map(r => r.jurisdiction))];
  const byKey = new Map(rows.map(r => [`${r.jurisdiction}|${r.offense_category}`, r.count]));

  const datasets = jurisdictions.map(j => ({
    label: JURISDICTION_LABELS[j] || j,
    data: categories.map(c => byKey.get(`${j}|${c}`) || 0),
    backgroundColor: JURISDICTION_COLORS[j] || "#666",
  }));

  new Chart(document.getElementById("chart-category"), {
    type: "bar",
    data: { labels: categories, datasets },
    options: { responsive: true, maintainAspectRatio: false },
  });
}

function renderHeatmap(rows) {
  const grid = document.getElementById("heatmap");
  const counts = new Map(rows.map(r => [`${r.weekday}|${r.hour}`, r.count]));
  const max = Math.max(1, ...rows.map(r => r.count));

  let html = `<div></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="col-label">${h}</div>`;

  for (let dow = 0; dow < 7; dow++) {
    html += `<div class="row-label">${WEEKDAYS[dow]}</div>`;
    for (let h = 0; h < 24; h++) {
      const count = counts.get(`${dow}|${h}`) || 0;
      const intensity = count / max;
      const bg = `rgba(61, 139, 253, ${0.08 + intensity * 0.85})`;
      html += `<div class="heatmap-cell" title="${WEEKDAYS[dow]} ${h}:00 - ${count} incidents" style="background:${bg}"></div>`;
    }
  }
  grid.innerHTML = html;
}

async function init() {
  try {
    const [daily, category, heatmap] = await Promise.all([
      fetchJson("data/trends_daily.json"),
      fetchJson("data/trends_category.json"),
      fetchJson("data/trends_heatmap.json"),
    ]);
    const { dates, jurisdictions, byKey } = renderDailyChart(daily);
    renderJurisdictionChart(jurisdictions, byKey, dates);
    renderCategoryChart(category);
    renderHeatmap(heatmap);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML("beforeend",
      `<p class="loading">Could not load trend data (${err.message}). Run the pipeline to generate site/data/.</p>`);
  }
}

init();
