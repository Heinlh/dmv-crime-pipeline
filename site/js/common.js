const JURISDICTION_LABELS = { dc: "Washington DC", moco: "Montgomery County" };

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

const CATEGORY_COLORS = {
  violent: "#c0392b",
  property: "#d68910",
  vehicle: "#8e44ad",
  drug: "#16a085",
  society: "#2874a6",
  other: "#7f8c8d",
};

function fmtPct(value) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function fmtDateTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status} loading ${path}`);
  return res.json();
}
