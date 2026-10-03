// Methodology page: renders the three statistical corrections from the
// payloads the pipeline writes (aoristic.json, nowcast.json, digest.json).
// Every figure shown here is measured on the daily run; nothing on this
// page is a hardcoded claim.

const CHART_INK = "#a6b0c9";
const CHART_GRID = "#1d1b30";
Chart.defaults.color = CHART_INK;
Chart.defaults.borderColor = CHART_GRID;
Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';

const HOUR_LABELS = Array.from({ length: 24 }, (_, h) => {
  const suffix = h < 12 ? "am" : "pm";
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  return `${hour12}${suffix}`;
});

function cards(containerId, items) {
  document.getElementById(containerId).innerHTML = items.map(k => `
    <div class="card hud">
      <div class="label">${esc(k.label)}</div>
      <div class="value">${esc(k.value)}</div>
      <div class="sub">${esc(k.sub || "")}</div>
    </div>
  `).join("");
}

function jurisdictionList(keys) {
  return (keys || []).map(jurisdictionLabel).join(", ") || "no sources";
}

// ------------------------------------------------------------ aoristic

function renderAoristic(a) {
  const caption = document.getElementById("caption-aoristic");
  if (!a || !a.by_hour.length || !a.distortion) {
    caption.textContent =
      "No source in the warehouse publishes offense end times yet, so there is nothing to compare.";
    return;
  }

  new Chart(document.getElementById("chart-aoristic"), {
    type: "line",
    data: {
      labels: HOUR_LABELS,
      datasets: [
        {
          label: "Start time only (the conventional chart)",
          data: a.by_hour.map(r => r.start),
          borderColor: "#ffb454",
          backgroundColor: "#ffb454",
          borderWidth: 2, tension: 0.25, pointRadius: 0, pointHoverRadius: 4,
        },
        {
          label: "Aoristic (mass spread across each window)",
          data: a.by_hour.map(r => r.aoristic),
          borderColor: "#4de3ff",
          backgroundColor: "rgba(77, 227, 255, 0.10)",
          borderWidth: 2, tension: 0.25, fill: true, pointRadius: 0, pointHoverRadius: 4,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 18, boxHeight: 2 } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: CHART_GRID },
             title: { display: true, text: "incidents" } },
      },
    },
  });

  const d = a.distortion;
  caption.textContent =
    `Plotting start times alone overstates ${HOUR_LABELS[d.most_overstated_hour]} by about ` +
    `${d.most_overstated_by.toLocaleString()} incidents (${d.most_overstated_pct}% of all mapped ` +
    `incidents) and understates ${HOUR_LABELS[d.most_understated_hour]} by about ` +
    `${d.most_understated_by.toLocaleString()} (${d.most_understated_pct}%). Overall, ` +
    `${d.mass_moved_pct}% of the probability mass sits in the wrong hour.`;

  cards("aoristic-stats", [
    { label: "Mass in the wrong hour", value: `${d.mass_moved_pct}%`,
      sub: "total variation between the two curves" },
    { label: "Incidents compared", value: fmtNumber(a.coverage.incidents),
      sub: `last ${a.window_days} days` },
    { label: "Have a real window", value: `${a.coverage.pct_with_window}%`,
      sub: `${fmtNumber(a.coverage.uniform_windows)} span a day or more` },
    { label: "Most overstated hour", value: HOUR_LABELS[d.most_overstated_hour],
      sub: "where start times pile up" },
  ]);

  document.getElementById("aoristic-scope").textContent =
    `Covers ${jurisdictionList(a.jurisdictions)}, the sources that publish offense end times. ` +
    `Incidents with no published end time keep their start hour, which is the best the data allows.`;

  const rows = a.by_category;
  document.getElementById("aoristic-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>Category</th><th class="num">Incidents</th>
      <th class="num">Mass moved</th><th>Reading</th></tr></thead>
      <tbody>${rows.map(r => `
        <tr>
          <td>${esc(categoryLabel(r.category))}</td>
          <td class="num">${fmtNumber(r.incidents)}</td>
          <td class="num">${(r.tvd * 100).toFixed(1)}%</td>
          <td>${r.tvd >= 0.15
                ? "Start time is badly misleading here"
                : r.tvd >= 0.05
                  ? "Start time is somewhat misleading"
                  : "Start time is close enough"}</td>
        </tr>`).join("")}
      </tbody>
    </table>` : "";
}

// ------------------------------------------------------------- nowcast

function renderNowcast(n) {
  const caption = document.getElementById("caption-nowcast");
  if (!n || !n.delay || !n.series.length) {
    caption.textContent =
      "No source in the warehouse publishes report dates yet, so the reporting delay cannot be measured.";
    return;
  }

  const labels = n.series.map(s => fmtDate(s.date));
  const corrected = n.series.map(s => (s.estimate === null ? null : s.estimate));
  const lower = n.series.map(s => (s.lower === null ? null : s.lower));
  const upper = n.series.map(s => (s.upper === null ? null : s.upper));

  new Chart(document.getElementById("chart-nowcast"), {
    type: "line",
    data: {
      labels,
      datasets: [
        // interval drawn as a band: lower line, then upper filled down to it
        { label: "Lower bound", data: lower, borderColor: "rgba(77,227,255,0.25)",
          borderWidth: 1, pointRadius: 0, fill: false, tension: 0.25 },
        { label: "95% interval", data: upper, borderColor: "rgba(77,227,255,0.25)",
          backgroundColor: "rgba(77,227,255,0.13)", borderWidth: 1, pointRadius: 0,
          fill: "-1", tension: 0.25 },
        { label: "Estimated eventual total", data: corrected, borderColor: "#4de3ff",
          backgroundColor: "#4de3ff", borderWidth: 2, borderDash: [5, 3],
          pointRadius: 0, pointHoverRadius: 4, tension: 0.25 },
        { label: "Reported so far (what a raw chart shows)",
          data: n.series.map(s => s.observed), borderColor: "#ffb454",
          backgroundColor: "#ffb454", borderWidth: 2, pointRadius: 0,
          pointHoverRadius: 4, tension: 0.25 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { boxWidth: 18, boxHeight: 2, filter: i => i.text !== "Lower bound" } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: CHART_GRID },
             title: { display: true, text: "incidents by date of offense" } },
      },
    },
  });

  const newest = n.series[n.series.length - 1];
  const gap = (newest && newest.estimate !== null)
    ? Math.max(0, Math.round(newest.estimate - newest.observed))
    : null;
  caption.textContent = gap === null
    ? `The newest day is under ${Math.round(100 * (newest ? newest.completeness : 0))}% complete, ` +
      `too incomplete to rescale honestly, so it is shown as observed only.`
    : `The most recent day currently shows ${fmtNumber(newest.observed)} incidents, but only ` +
      `${Math.round(newest.completeness * 100)}% of its reports have arrived. The eventual total is ` +
      `estimated at ${fmtNumber(Math.round(newest.estimate))}, roughly ${fmtNumber(gap)} still to come.`;

  cards("nowcast-stats", [
    { label: "Reported same day", value: `${n.delay.same_day_pct}%`,
      sub: "share filed on the day of offense" },
    { label: "Median delay", value: `${n.delay.median_days} d`,
      sub: "half of reports arrive within" },
    { label: "90th percentile", value: `${n.delay.p90_days} d`,
      sub: "nine in ten arrive within" },
    { label: "Fitted on", value: fmtNumber(n.delay.fitted_on),
      sub: `reports from days older than ${n.maturity_days} d` },
  ]);

  document.getElementById("nowcast-scope").textContent =
    `Covers ${jurisdictionList(n.jurisdictions)}, the sources that publish a report date. ` +
    `The delay distribution is fitted only on occurrence days at least ${n.maturity_days} days old, ` +
    `since younger days are themselves still filling in.` +
    (n.delay.negative_excluded
      ? ` ${fmtNumber(n.delay.negative_excluded)} records with a report date before the offense date were excluded as source errors.`
      : "");
}

// ------------------------------------------------------------- signals

function renderSignals(digest) {
  const caption = document.getElementById("caption-signals");
  const d = digest && digest.detection;
  if (!d) {
    caption.textContent = "Signal detection has not run yet.";
    return;
  }

  cards("signals-stats", [
    { label: "Series tested", value: fmtNumber(d.tested),
      sub: `${d.level} x category, latest day` },
    { label: "Signals reported", value: fmtNumber(d.flagged),
      sub: "after FDR correction" },
    { label: "Naive 5% would flag", value: fmtNumber(d.naive_would_flag),
      sub: "without the correction" },
    { label: "Target false-discovery rate", value: `${Math.round(d.fdr_q * 100)}%`,
      sub: "Benjamini-Hochberg q" },
  ]);

  const saved = d.naive_would_flag - d.flagged;
  caption.textContent =
    `On the latest data day, ${fmtNumber(d.tested)} series had enough volume to test. ` +
    `An uncorrected 5% threshold would have reported ${fmtNumber(d.naive_would_flag)} of them; ` +
    `after correction, ${fmtNumber(d.flagged)} survived` +
    (saved > 0 ? `, so ${fmtNumber(saved)} would-be alerts were suppressed as likely noise.` : ".");

  // Calibration: measured by running the detector against simulated
  // null data on this same run, not asserted.
  const c = d.calibration;
  document.getElementById("calibration-table").innerHTML = (c && c.trials) ? `
    <table class="data-table">
      <thead><tr><th>Threshold</th><th class="num">Alerts/day on pure noise</th>
      <th class="num">False-alarm rate</th><th>Interpretation</th></tr></thead>
      <tbody>
        <tr>
          <td>Naive 5% per series</td>
          <td class="num">${c.naive_false_alarms_per_day}</td>
          <td class="num">${(c.naive_false_alarm_rate * 100).toFixed(2)}%</td>
          <td>Every one is a false alarm, yet they arrive daily</td>
        </tr>
        <tr>
          <td>Benjamini-Hochberg (q = ${c.fdr_q})</td>
          <td class="num">${c.bh_false_alarms_per_day}</td>
          <td class="num">${(c.bh_false_alarm_rate * 100).toFixed(2)}%</td>
          <td>Firing is rare enough that a signal is worth reading</td>
        </tr>
      </tbody>
    </table>
    <p class="chart-caption">Measured over ${fmtNumber(c.trials)} simulated days across
      ${fmtNumber(c.series)} series (${fmtNumber(c.tests)} tests), drawing each series from its
      own fitted baseline so no real spike exists.</p>` : "";
}

// ---------------------------------------------------------------- init

async function init() {
  const [aoristicData, nowcastData, digest] = await Promise.all([
    fetchJson("data/aoristic.json").catch(() => null),
    fetchJson("data/nowcast.json").catch(() => null),
    fetchJson("data/digest.json").catch(() => null),
  ]);
  renderAoristic(aoristicData);
  renderNowcast(nowcastData);
  renderSignals(digest);
}

init();
