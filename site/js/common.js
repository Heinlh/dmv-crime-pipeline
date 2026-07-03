// Shared taxonomy metadata, formatters, and data loading used on every
// page. CATEGORIES is the single source of truth for category display:
// key (the warehouse offense_category value), plain-English label, hex
// color, description, and included examples. Raw offense_category
// values never reach the UI unlabeled.

const JURISDICTION_LABELS = {
  dc: "Washington DC",
  moco: "Montgomery County",
  pgc: "Prince George's County",
};
// Line-chart series only: neon cyan / magenta / amber. Checked for
// colorblind separation (all-pairs CVD deltaE 25+) and 3:1 contrast on
// the chart surface. Never used for categories.
const JURISDICTION_COLORS = { dc: "#4de3ff", moco: "#d17bff", pgc: "#ffb454" };

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

// Email alerts are delivered by Buttondown (a managed newsletter
// service): the signup form posts directly to them, so subscriber email
// addresses never touch this site or its repository. Set your Buttondown
// username here after creating the account; while empty, the Alerts page
// shows setup instructions instead of a form.
const BUTTONDOWN_USERNAME = "hhtet";

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

function populateJurisdictionFilter(selectId) {
  const select = document.getElementById(selectId);
  for (const [value, label] of Object.entries(JURISDICTION_LABELS)) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
}

// ------------------------------------------- factual incident titles
// Every incident title is composed ONLY from fields the agency
// published: a plain-English translation of the offense label, plus the
// reported method where one exists. No details are ever invented; the
// original agency label stays visible on each incident card.

const OFFENSE_LABEL_RULES = [
  [/HOMICIDE|MURDER|MANSLAUGHTER/, "Homicide"],
  [/SEX ABUSE|RAPE|SEX OFFENSE|SODOMY|FONDLING|PEEPING/, "Sexual assault or abuse"],
  [/CARJACK/, "Carjacking"],
  [/THEFT F\/AUTO|THEFT FROM AUTO|FROM MOTOR VEHICLE/, "Theft from a parked vehicle"],
  [/MOTOR VEHICLE THEFT|AUTO, STOLEN|STOLEN VEHICLE/, "Stolen vehicle"],
  [/ASSAULT W\/DANGEROUS WEAPON|AGGRAVATED ASSAULT|ASSAULT, WEAPON/, "Assault with a weapon"],
  [/ASSAULT|INTIMIDATION/, "Assault or threats"],
  [/ROBBERY/, "Robbery"],
  [/KIDNAP/, "Kidnapping"],
  [/BURGLARY|B & E|BREAKING/, "Burglary / break-in"],
  [/SHOPLIFT/, "Shoplifting"],
  [/STOLEN PROP/, "Stolen property"],
  [/THEFT|LARCENY/, "Theft"],
  [/FRAUD|SWINDLE|FALSE PRETENSE|FORGERY|COUNTERFEIT/, "Fraud"],
  [/VANDAL|DESTRUCTION/, "Vandalism / property damage"],
  [/ARSON/, "Arson"],
  [/WEAPON/, "Weapons offense"],
  [/DRUG|NARCOT/, "Drug offense"],
  [/DUI|DRIVING UNDER/, "Driving under the influence"],
  [/LIQUOR|ALCOHOL/, "Alcohol violation"],
  [/DISORDERLY/, "Disorderly conduct"],
  [/PROSTITUTION/, "Prostitution"],
  [/GAMBLING/, "Gambling offense"],
  [/ACCIDENT/, "Traffic accident"],
];

function friendlyOffense(raw) {
  if (!raw) return "Unknown offense";
  const upper = String(raw).toUpperCase();
  for (const [pattern, label] of OFFENSE_LABEL_RULES) {
    if (pattern.test(upper)) return label;
  }
  // no rule matched: present the agency's own label, tidied
  return String(raw).charAt(0).toUpperCase() + String(raw).slice(1).toLowerCase();
}

function incidentTitle(inc) {
  let title = friendlyOffense(inc.offense_raw);
  // DC publishes a method (gun/knife); fold it in when it says something
  if (inc.method && !/other/i.test(inc.method)) {
    title += ` (${String(inc.method).toLowerCase()})`;
  }
  return title;
}
