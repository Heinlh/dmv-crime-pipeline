const CATEGORY_COLORS = {
  violent: "#c0392b",
  property: "#d68910",
  vehicle: "#8e44ad",
  drug: "#16a085",
  society: "#2874a6",
  other: "#7f8c8d",
};

const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };

const map = L.map("map").setView([38.95, -77.05], 10);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const clusterGroup = L.markerClusterGroup();
map.addLayer(clusterGroup);

let allFeatures = [];

function popupHtml(props) {
  const when = props.occurred_at ? new Date(props.occurred_at).toLocaleString() : "Unknown";
  return `
    <strong>${props.offense_raw || "Unknown offense"}</strong>
    <span class="badge ${props.offense_category}">${props.offense_category}</span><br>
    ${when}<br>
    ${JURISDICTION_LABELS[props.jurisdiction] || props.jurisdiction}
    ${props.area_name ? " &middot; " + props.area_name : ""}<br>
    ${props.block_address || ""}<br>
    ${props.case_number ? "Case #" + props.case_number + "<br>" : ""}
    Severity: ${props.severity_weight}
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
      radius: 6,
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

async function init() {
  try {
    const res = await fetch("data/incidents.geojson");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const geojson = await res.json();
    allFeatures = geojson.features;
    applyFilters();
  } catch (err) {
    document.getElementById("result-count").textContent = `Could not load incidents (${err.message}). Run the pipeline to generate site/data/.`;
  }
}

["f-jurisdiction", "f-range", "f-category", "f-severity"].forEach(id =>
  document.getElementById(id).addEventListener("change", applyFilters)
);

init();
