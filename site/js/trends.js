const JURISDICTION_COLORS = { dc: "#3d8bfd", moco: "#e67e22" };
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

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

  let peakDate = dates[0], peakTotal = -1;
  for (const d of dates) {
    const total = jurisdictions.reduce((sum, j) => sum + (byKey.get(`${j}|${d}`) || 0), 0);
    if (total > peakTotal) { peakTotal = total; peakDate = d; }
  }
  document.getElementById("caption-daily").textContent =
    `Incidents peaked on ${peakDate} with ${peakTotal} reports across both jurisdictions.`;

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

  const maxIdx = totals.indexOf(Math.max(...totals));
  const minIdx = totals.indexOf(Math.min(...totals));
  const pctMore = totals[minIdx] > 0 ? Math.round((totals[maxIdx] / totals[minIdx] - 1) * 100) : null;
  document.getElementById("caption-jurisdiction").textContent = maxIdx === minIdx || pctMore === null
    ? "Both jurisdictions reported a similar number of incidents over the last 90 days."
    : `${JURISDICTION_LABELS[jurisdictions[maxIdx]]} reported about ${pctMore}% more incidents than ${JURISDICTION_LABELS[jurisdictions[minIdx]]} over the last 90 days.`;
}

function renderCategoryCards(rows) {
  const totals = new Map();
  for (const r of rows) {
    const cur = totals.get(r.offense_category) || { count: 0, prev_count: 0 };
    cur.count += r.count;
    cur.prev_count += r.prev_count;
    totals.set(r.offense_category, cur);
  }
  const sorted = [...totals.entries()].sort((a, b) => b[1].count - a[1].count);

  document.getElementById("category-cards").innerHTML = sorted.map(([cat, t]) => {
    const pct = t.prev_count ? (t.count - t.prev_count) / t.prev_count * 100 : null;
    return `
      <div class="card">
        <div class="label">${CATEGORY_LABELS[cat] || cat}</div>
        <div class="value">${t.count.toLocaleString()}</div>
        <div class="sub">${pct === null ? "no prior data" : fmtPct(pct) + " vs. previous 7 days"}</div>
      </div>
    `;
  }).join("");

  return sorted;
}

function renderCategoryChart(rows, sortedTotals) {
  const categories = sortedTotals.map(([cat]) => cat);
  const jurisdictions = [...new Set(rows.map(r => r.jurisdiction))];
  const byKey = new Map(rows.map(r => [`${r.jurisdiction}|${r.offense_category}`, r.count]));

  const datasets = jurisdictions.map(j => ({
    label: JURISDICTION_LABELS[j] || j,
    data: categories.map(c => byKey.get(`${j}|${c}`) || 0),
    backgroundColor: JURISDICTION_COLORS[j] || "#666",
  }));

  new Chart(document.getElementById("chart-category"), {
    type: "bar",
    data: { labels: categories.map(c => CATEGORY_LABELS[c] || c), datasets },
    options: { responsive: true, maintainAspectRatio: false },
  });

  const [topCat, topTotals] = sortedTotals[0];
  const pct = topTotals.prev_count ? (topTotals.count - topTotals.prev_count) / topTotals.prev_count * 100 : null;
  document.getElementById("caption-category").textContent =
    `${CATEGORY_LABELS[topCat] || topCat} was the most common category this week (${topTotals.count} incidents` +
    (pct === null ? ")." : `, ${fmtPct(pct)} vs. the previous 7 days).`);
}

function renderHeatmap(rows) {
  const grid = document.getElementById("heatmap");
  const counts = new Map(rows.map(r => [`${r.weekday}|${r.hour}`, r.count]));
  const max = Math.max(1, ...rows.map(r => r.count));

  let html = `<div></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="col-label">${h}</div>`;

  let peak = { dow: 0, hour: 0, count: -1 };
  for (let dow = 0; dow < 7; dow++) {
    html += `<div class="row-label">${WEEKDAYS[dow]}</div>`;
    for (let h = 0; h < 24; h++) {
      const count = counts.get(`${dow}|${h}`) || 0;
      if (count > peak.count) peak = { dow, hour: h, count };
      const intensity = count / max;
      const bg = `rgba(61, 139, 253, ${0.08 + intensity * 0.85})`;
      html += `<div class="heatmap-cell" title="${WEEKDAYS[dow]} ${h}:00 - ${count} incidents" style="background:${bg}"></div>`;
    }
  }
  grid.innerHTML = html;

  document.getElementById("caption-heatmap").textContent =
    `Darker boxes show time periods with more reported incidents. Most incidents are reported around ` +
    `${peak.hour}:00 on ${WEEKDAYS_FULL[peak.dow]}s.`;
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
    const sortedTotals = renderCategoryCards(category);
    renderCategoryChart(category, sortedTotals);
    renderHeatmap(heatmap);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML("beforeend",
      `<p class="loading">Could not load trend data (${err.message}). Run the pipeline to generate site/data/.</p>`);
  }
}

init();
