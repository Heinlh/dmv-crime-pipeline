// Daily Brief: renders data/digest.json (written on every pipeline run)
// as bullets, two small charts, and the day's most serious incidents.

const CHART_INK = "#a6b0c9";
const CHART_GRID = "#1d1b30";
Chart.defaults.color = CHART_INK;
Chart.defaults.borderColor = CHART_GRID;
Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';

function renderBullets(digest) {
  document.getElementById("brief-date").textContent =
    `Covering ${fmtDate(digest.latest_day)}, the most recent day with published reports ` +
    `(generated ${fmtDateTime(digest.generated_at)}).`;
  document.getElementById("bullets").innerHTML =
    digest.bullets.map(b => `<li>${esc(b)}</li>`).join("");
}

function renderDayCategories(digest) {
  const cats = digest.by_category.map(r => r.offense_category);
  new Chart(document.getElementById("chart-day-categories"), {
    type: "bar",
    data: {
      labels: cats.map(categoryLabel),
      datasets: [{
        data: digest.by_category.map(r => r.count),
        backgroundColor: cats.map(c => CATEGORY_COLORS[c] || CATEGORY_COLORS.other),
        borderRadius: 4,
        borderSkipped: "start",
        maxBarThickness: 24,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, grid: { color: CHART_GRID } },
        y: { grid: { display: false } },
      },
    },
  });
  const top = digest.by_category[0];
  document.getElementById("caption-day-categories").textContent = top
    ? `${categoryLabel(top.offense_category)} led the day with ${top.count.toLocaleString()} reported incidents.`
    : "No incidents reported.";
}

function renderLast14(digest) {
  new Chart(document.getElementById("chart-last14"), {
    type: "line",
    data: {
      labels: digest.last14.map(r => fmtDate(r.date)),
      datasets: [{
        data: digest.last14.map(r => r.count),
        borderColor: "#4de3ff",
        backgroundColor: "rgba(77, 227, 255, 0.1)",
        fill: true,
        borderWidth: 2,
        tension: 0.2,
        pointRadius: 0,
        pointHoverRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
        y: { beginAtZero: true, grid: { color: CHART_GRID } },
      },
    },
  });
  const counts = digest.last14.map(r => r.count);
  const latest = counts[counts.length - 1];
  const avg = counts.reduce((s, v) => s + v, 0) / Math.max(counts.length, 1);
  document.getElementById("caption-last14").textContent =
    `All jurisdictions combined, day by day. The latest day (${latest.toLocaleString()}) sits ` +
    `${latest > avg * 1.1 ? "above" : latest < avg * 0.9 ? "below" : "near"} the 14 day average of ${avg.toFixed(0)}.`;
}

function renderSignals(digest) {
  const box = document.getElementById("signals");
  const signals = digest.signals || [];
  if (!signals.length) {
    box.innerHTML = `<p class="signal-none">No slice of the data strayed far from its 8-week baseline. All quiet on the statistical front.</p>`;
    return;
  }
  box.innerHTML = signals.map(s => `
    <div class="signal ${s.direction === "spike" ? "spike" : "lull"}">
      <span class="dir">${s.direction === "spike" ? "&#9650; SPIKE" : "&#9660; LULL"}</span>
      <span class="what">${esc(categoryLabel(s.offense_category))} &middot; ${esc(jurisdictionLabel(s.jurisdiction))}</span>
      <span class="nums mono">${s.count} vs typical ${s.baseline.toFixed(0)} (&times;${s.ratio.toFixed(1)})</span>
    </div>
  `).join("");
}

function renderNotable(digest) {
  const grid = document.getElementById("notable");
  if (!digest.notable.length) {
    grid.innerHTML = `<p class="loading">No incidents on the latest data day.</p>`;
    return;
  }
  grid.innerHTML = digest.notable.map(inc => `
    <article class="event-card ${esc(inc.offense_category)}">
      <div class="head">
        <span class="badge ${esc(inc.offense_category)}">${esc(categoryLabel(inc.offense_category))}</span>
        <span class="when">${esc(fmtDateTime(inc.occurred_at))}</span>
      </div>
      <div class="title">${esc(incidentTitle(inc))}</div>
      <div class="meta">
        ${esc(inc.block_address || "Location withheld")}<br>
        ${esc(jurisdictionLabel(inc.jurisdiction))}${inc.area_name ? " &middot; " + esc(inc.area_name) : ""}<br>
        <span class="case">AGENCY LABEL: ${esc(inc.offense_raw || "n/a")}</span>
      </div>
    </article>
  `).join("");
}

async function init() {
  try {
    const digest = await fetchJson("data/digest.json");
    if (!digest.latest_day) throw new Error("no data in digest");
    renderBullets(digest);
    renderSignals(digest);
    renderDayCategories(digest);
    renderLast14(digest);
    renderNotable(digest);
  } catch (err) {
    document.getElementById("brief-date").textContent =
      `Could not load the daily brief (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

init();
