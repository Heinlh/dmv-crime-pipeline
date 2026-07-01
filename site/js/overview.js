const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };

async function loadStatus() {
  const container = document.getElementById("status-cards");
  try {
    const res = await fetch("data/summary.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();

    const sourceLabel = s.sources_active.map(j => JURISDICTION_LABELS[j] || j).join(", ");
    const byJurisdiction = Object.entries(s.records_by_jurisdiction)
      .map(([j, n]) => `${JURISDICTION_LABELS[j] || j}: ${n.toLocaleString()}`)
      .join(" | ");

    const cards = [
      { label: "Last Updated", value: new Date(s.last_updated).toLocaleString(), sub: "" },
      { label: "Sources Active", value: s.sources_active.length, sub: sourceLabel },
      { label: "Records Processed", value: s.total_records.toLocaleString(), sub: byJurisdiction },
      { label: "New in Last 24h", value: s.new_incidents_24h.toLocaleString(), sub: "" },
      { label: "New in Last 48h", value: s.new_incidents_48h.toLocaleString(), sub: "" },
      { label: "Pipeline Status", value: s.pipeline_status.toUpperCase(), sub: "", statusClass: `status-${s.pipeline_status === "ok" ? "ok" : "fail"}` },
    ];

    container.innerHTML = cards.map(c => `
      <div class="card">
        <div class="label">${c.label}</div>
        <div class="value ${c.statusClass || ""}">${c.value}</div>
        <div class="sub">${c.sub}</div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="loading">Could not load pipeline status (${err.message}). Run the pipeline to generate site/data/.</p>`;
  }
}

loadStatus();
