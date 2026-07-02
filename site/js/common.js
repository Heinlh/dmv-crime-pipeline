// Shared taxonomy labels, colors, formatters, and data loading used on
// every page. Raw offense_category values never reach the UI unlabeled.

const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };
const JURISDICTION_COLORS = { dc: "#2a9dcf", moco: "#c98500" };

const CATEGORY_ORDER = ["violent", "property", "vehicle", "drug", "society", "other"];

const CATEGORY_LABELS = {
  violent: "Violent Crime",
  property: "Property Crime",
  vehicle: "Vehicle Crime",
  drug: "Drug/Alcohol",
  society: "Public Disorder",
  other: "Other",
};

const CATEGORY_DESCRIPTIONS = {
  violent: "Homicide, assault, robbery, and other offenses against a person.",
  property: "Burglary, theft, arson, vandalism, and other property offenses.",
  vehicle: "Motor vehicle theft and theft from a vehicle.",
  drug: "Drug and narcotics offenses.",
  society: "Weapons, DUI, prostitution, and other public-order offenses.",
  other: "Offenses that don't fit the categories above.",
};

// Validated for colorblind safety on the dark surface (all-pairs CVD
// deltaE >= 12 across the five chromatic slots; "other" is a deliberate
// neutral that identity never depends on -- labels and tooltips always
// carry the category name too).
const CATEGORY_COLORS = {
  violent: "#e66767",
  property: "#c98500",
  vehicle: "#8b7ff2",
  drug: "#1fa877",
  society: "#2a9dcf",
  other: "#8a93a3",
};

// ------------------------------------------------------- formatters

function fmtPct(value) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function fmtNumber(n) {
  return n === null || n === undefined ? "n/a" : n.toLocaleString();
}

function fmtDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "Unknown"
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function fmtDate(iso) {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

// Offense text, addresses, and case numbers come from external APIs:
// escape them before any innerHTML interpolation.
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ----------------------------------------------------- data loading

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status} loading ${path}`);
  return res.json();
}

// incidents.json is columnar ({columns, rows}) to keep the payload
// small; inflate it to an array of objects once, on load.
async function fetchIncidents() {
  const payload = await fetchJson("data/incidents.json");
  const cols = payload.columns;
  const incidents = payload.rows.map(row => {
    const obj = {};
    cols.forEach((c, i) => { obj[c] = row[i]; });
    obj._ts = obj.occurred_at ? new Date(obj.occurred_at).getTime() : NaN;
    return obj;
  });
  return { incidents, windowDays: payload.window_days };
}

function categoryLabel(cat) {
  return CATEGORY_LABELS[cat] || cat || "Unknown";
}

function jurisdictionLabel(j) {
  return JURISDICTION_LABELS[j] || j || "Unknown";
}
