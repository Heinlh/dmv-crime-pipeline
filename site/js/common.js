// Shared taxonomy metadata, formatters, and data loading used on every
// page. CATEGORIES is the single source of truth for category display:
// key (the warehouse offense_category value), plain-English label, hex
// color, description, and included examples. Raw offense_category
// values never reach the UI unlabeled.

const JURISDICTION_LABELS = {
  dc: "Washington DC",
  moco: "Montgomery County",
  pgc: "Prince George's County",
  fairfax: "Fairfax County",
  pwc: "Prince William County",
};
// Line-chart series only: neon cyan / magenta / amber / green / pink.
// All pairs clear 3:1 contrast on the chart surface; the tightest CVD
// pairs sit in the floor band, which is acceptable ONLY because every
// chart carries a legend, index-mode tooltips naming each series, and a
// table view. Never used for categories.
const JURISDICTION_COLORS = { dc: "#4de3ff", moco: "#d17bff", pgc: "#ffb454", fairfax: "#3ddc97", pwc: "#ff8fab" };

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

// ------------------------------------------------ shareable URL state
// Filter state lives in the URL hash as query-string pairs
// (#days=30&cat=violent) so any view can be copied and shared. Pages
// opt in by calling readHashState() on load and writeHashState() on
// every filter change; replaceState keeps Back button behavior sane.

function readHashState() {
  const out = {};
  const hash = location.hash.replace(/^#/, "");
  if (!hash) return out;
  for (const [k, v] of new URLSearchParams(hash)) out[k] = v;
  return out;
}

function writeHashState(obj) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) {
    if (v !== "" && v !== null && v !== undefined) params.set(k, v);
  }
  const next = params.toString();
  history.replaceState(null, "", next ? `#${next}` : location.pathname + location.search);
}

// ------------------------------------------------ motion preference

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Count-up animation for KPI values. Respects reduced motion (sets the
// final text immediately) and always ends on the exact formatted value.
function countUp(el, finalText) {
  const numeric = parseFloat(String(finalText).replace(/[^0-9.]/g, ""));
  if (REDUCED_MOTION || !Number.isFinite(numeric) || numeric === 0) {
    el.textContent = finalText;
    return;
  }
  const duration = 700;
  const start = performance.now();
  function frame(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    if (t < 1) {
      el.textContent = Math.round(numeric * eased).toLocaleString();
      requestAnimationFrame(frame);
    } else {
      el.textContent = finalText;
    }
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------- command palette
// Ctrl+K / Cmd+K opens a keyboard-first switcher: jump to any page, or
// free-text search that lands on the Events page. Vanilla JS, built
// lazily on first open, closed with Esc or backdrop click.

const PALETTE_COMMANDS = [
  { label: "Map", hint: "live incident map", href: "index.html" },
  { label: "Trends", hint: "history back to 2016", href: "trends.html" },
  { label: "Events", hint: "searchable incident log", href: "events.html" },
  { label: "Daily Brief", hint: "latest data day", href: "daily.html" },
  { label: "Alerts", hint: "daily brief by email", href: "alerts.html" },
  { label: "About", hint: "sources and caveats", href: "about.html" },
  { label: "Contact", hint: "the person behind the dashboard", href: "contact.html" },
  { label: "Privacy", hint: "what this site does and does not collect", href: "privacy.html" },
  { label: "Map: homicide only", hint: "filter the map", href: "index.html#cat=homicide&days=90" },
  { label: "Map: violent crime only", hint: "filter the map", href: "index.html#cat=violent" },
  { label: "Map: vehicle crime only", hint: "filter the map", href: "index.html#cat=vehicle" },
  { label: "Trends: last 12 months", hint: "one-year view", href: "trends.html#preset=1y" },
];

let _paletteEl = null;

function buildPalette() {
  const wrap = document.createElement("div");
  wrap.className = "cmdk";
  wrap.hidden = true;
  wrap.innerHTML = `
    <div class="cmdk-backdrop"></div>
    <div class="cmdk-panel" role="dialog" aria-modal="true" aria-label="Command palette">
      <input class="cmdk-input" type="text" placeholder="Jump to a page or search events..."
             aria-label="Command palette search" autocomplete="off" spellcheck="false">
      <ul class="cmdk-list" role="listbox"></ul>
      <div class="cmdk-foot"><kbd>&uarr;&darr;</kbd> navigate <kbd>&#9166;</kbd> go <kbd>esc</kbd> close</div>
    </div>`;
  document.body.appendChild(wrap);

  const input = wrap.querySelector(".cmdk-input");
  const list = wrap.querySelector(".cmdk-list");
  let active = 0;

  function currentItems() {
    const q = input.value.trim().toLowerCase();
    let items = PALETTE_COMMANDS.filter(c =>
      !q || c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q));
    if (q) {
      items = items.concat([{
        label: `Search events for "${input.value.trim()}"`,
        hint: "full-text search, last 90 days",
        href: `events.html#q=${encodeURIComponent(input.value.trim())}`,
      }]);
    }
    return items.slice(0, 9);
  }

  function render() {
    const items = currentItems();
    if (active >= items.length) active = Math.max(0, items.length - 1);
    list.innerHTML = items.map((c, i) => `
      <li class="cmdk-item ${i === active ? "active" : ""}" role="option"
          aria-selected="${i === active}" data-href="${esc(c.href)}">
        <span class="cmdk-label">${esc(c.label)}</span>
        <span class="cmdk-hint">${esc(c.hint)}</span>
      </li>`).join("");
  }

  function go(href) {
    closePalette();
    // same-page hash navigation needs a manual reload to re-read state
    const [page] = href.split("#");
    const here = location.pathname.split("/").pop() || "index.html";
    location.href = href;
    if (page === here && href.includes("#")) location.reload();
  }

  input.addEventListener("input", () => { active = 0; render(); });
  input.addEventListener("keydown", (e) => {
    const items = currentItems();
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === "Enter" && items[active]) { e.preventDefault(); go(items[active].href); }
  });
  list.addEventListener("click", (e) => {
    const item = e.target.closest(".cmdk-item");
    if (item) go(item.dataset.href);
  });
  wrap.querySelector(".cmdk-backdrop").addEventListener("click", closePalette);

  wrap._render = render;
  wrap._input = input;
  return wrap;
}

function openPalette() {
  if (!_paletteEl) _paletteEl = buildPalette();
  _paletteEl.hidden = false;
  _paletteEl._input.value = "";
  _paletteEl._render();
  _paletteEl._input.focus();
}

function closePalette() {
  if (_paletteEl) _paletteEl.hidden = true;
}

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (_paletteEl && !_paletteEl.hidden) closePalette(); else openPalette();
  } else if (e.key === "Escape") {
    closePalette();
  }
});

// --------------------------------------------------- service worker
// Progressive web app: network-first worker gives an offline fallback
// with the last-seen data. Registration is best-effort; the site works
// identically without it.

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}
