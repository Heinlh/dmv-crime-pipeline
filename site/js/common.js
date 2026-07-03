// Shared taxonomy metadata, formatters, and data loading used on every
// page. CATEGORIES is the single source of truth for category display:
// key (the warehouse offense_category value), plain-English label, hex
// color, description, and included examples. Raw offense_category
// values never reach the UI unlabeled.

const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };
const JURISDICTION_COLORS = { dc: "#2a9dcf", moco: "#cbd5e1" };

// Colors keep the requested hue per category (dark red, orange-red,
// purple, blue, teal, amber, gray) with lightness adjusted where needed
// so every mark clears 3:1 contrast on the dark surface. The set is
// validated for colorblind safety (all-pairs CVD deltaE, floor band
// covered by the labels/legend/tooltips that always accompany color).
const CATEGORIES = {
  homicide: {
    label: "Homicide / Fatal Violence",
    color: "#cd3a3a",
    description: "Deadly offenses against a person.",
    examples: "homicide, manslaughter, fatal shootings",
  },
  violent: {
    label: "Violent Crime",
    color: "#bf4f00",
    description: "Non-fatal offenses against a person.",
    examples: "assault, robbery, weapons offenses, nonfatal shootings",
  },
  sexual: {
    label: "Sexual Offenses",
    color: "#aa6ad1",
    description: "Sexual offenses against a person.",
    examples: "sexual assault, rape, sex offenses",
  },
  property: {
    label: "Property Crime",
    color: "#0072B2",
    description: "Offenses against property.",
    examples: "theft, burglary, fraud, shoplifting, stolen property",
  },
  vehicle: {
    label: "Vehicle-Related Crime",
    color: "#009E73",
    description: "Offenses involving a vehicle.",
    examples: "motor vehicle theft, theft from auto, carjacking, vehicle break-ins",
  },
  disorder: {
    label: "Drug / Alcohol / Disorder",
    color: "#c98500",
    description: "Drug, alcohol, and public-order offenses.",
    examples: "drug offenses, alcohol violations, disorderly conduct, nuisance/public order",
  },
  other: {
    label: "Other / Unknown",
    color: "#8A8A8A",
    description: "Incidents that don't fit the categories above.",
    examples: "uncategorized, administrative, unclear, miscellaneous incidents",
  },
};

// Derived lookups so page code reads by role.
const CATEGORY_ORDER = Object.keys(CATEGORIES);
const CATEGORY_LABELS = Object.fromEntries(CATEGORY_ORDER.map(k => [k, CATEGORIES[k].label]));
const CATEGORY_COLORS = Object.fromEntries(CATEGORY_ORDER.map(k => [k, CATEGORIES[k].color]));
const CATEGORY_DESCRIPTIONS = Object.fromEntries(
  CATEGORY_ORDER.map(k => [k, `${CATEGORIES[k].description} Includes ${CATEGORIES[k].examples}.`]));

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
