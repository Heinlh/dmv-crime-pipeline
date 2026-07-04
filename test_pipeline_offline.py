"""Offline smoke test: fabricate API-shaped records for both sources,
land them, load DuckDB, run the transform and the site export, and
verify the fact table plus the exported JSON. Includes a duplicate
record to prove dedupe and idempotency work, and a 2016 record to prove
the full-history trends export includes old data while the incident
window export excludes it.

Runs against a temp raw/warehouse/site-data dir (not data/ and site/data)
so it's safe to run even after a real pipeline run in this checkout."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

import config

_tmp = Path(tempfile.mkdtemp(prefix="dmv-crime-pipeline-test-"))
config.RAW_DIR = _tmp / "raw"
config.WAREHOUSE_PATH = _tmp / "crime.duckdb"
config.SITE_DATA_DIR = _tmp / "site_data"

from config import SITE_DATA_DIR, WAREHOUSE_PATH
from export import export_site_data
from extractors.base import land_raw
from load import load_duckdb

_now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
_iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.000")
RECENT_A = _now - timedelta(days=6)
RECENT_A_FIX = RECENT_A + timedelta(minutes=15)  # later correction, wins dedupe
RECENT_B = _now - timedelta(days=5)

moco_records = [
    {"incident_id": "201234567", "offence_code": "2308", "case_number": "24001111",
     "start_date": _iso(RECENT_A), "end_date": _iso(RECENT_A + timedelta(minutes=30)),
     "nibrs_code": "23F", "victims": "1", "crimename1": "Crime Against Property",
     "crimename2": "Theft From Motor Vehicle", "crimename3": "LARCENY - FROM AUTO",
     "district": "ROCKVILLE", "location": "100 BLK MONROE ST",
     "city": "ROCKVILLE", "state": "MD", "zip_code": "20850",
     "latitude": "39.0840", "longitude": "-77.1528"},
    {"incident_id": "201234568", "case_number": "24001112",
     "start_date": _iso(RECENT_B), "crimename1": "Crime Against Person",
     "crimename2": "Aggravated Assault", "crimename3": "ASSAULT - AGGRAVATED",
     "district": "SILVER SPRING", "location": "8600 BLK GEORGIA AVE",
     "city": "SILVER SPRING", "state": "MD", "zip_code": "20910",
     "victims": "2", "latitude": "38.9959", "longitude": "-77.0276"},
    # duplicate of the first incident (a later correction) to test dedupe
    {"incident_id": "201234567", "case_number": "24001111",
     "start_date": _iso(RECENT_A_FIX), "crimename2": "Theft From Motor Vehicle",
     "district": "ROCKVILLE", "location": "100 BLK MONROE ST",
     "city": "ROCKVILLE", "state": "MD", "zip_code": "20850",
     "victims": "1", "latitude": "39.0840", "longitude": "-77.1528"},
    # ancient record: must appear in trends.json (full history) but NOT
    # in incidents.json (recent window only)
    {"incident_id": "160000001", "case_number": "16000001",
     "start_date": "2016-08-15T21:00:00.000", "crimename2": "Robbery",
     "district": "WHEATON", "location": "11200 BLK GEORGIA AVE",
     "city": "WHEATON", "state": "MD", "zip_code": "20902",
     "victims": "1", "latitude": "39.0398", "longitude": "-77.0552"},
    # homicide taxonomy check
    {"incident_id": "201234570", "case_number": "24001113",
     "start_date": _iso(RECENT_B), "crimename2": "Murder and Nonnegligent Manslaughter",
     "district": "GERMANTOWN", "location": "19900 BLK FREDERICK RD",
     "city": "GERMANTOWN", "state": "MD", "zip_code": "20874",
     "victims": "1", "latitude": "39.1732", "longitude": "-77.2717"},
]

ms = lambda dt: str(int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000))
dc_records = [
    {"CCN": "26098765", "REPORT_DAT": ms(RECENT_B), "START_DATE": ms(RECENT_B - timedelta(hours=5)),
     "END_DATE": ms(RECENT_B - timedelta(hours=4)), "OFFENSE": "ROBBERY", "METHOD": "GUN",
     "BLOCK": "1400 - 1499 BLOCK OF U STREET NW", "WARD": "1", "DISTRICT": "3",
     "SHIFT": "MIDNIGHT", "LATITUDE": "38.9170", "LONGITUDE": "-77.0339", "OBJECTID": "1"},
    {"CCN": "26098766", "REPORT_DAT": ms(RECENT_B), "START_DATE": ms(RECENT_B - timedelta(hours=14)),
     "OFFENSE": "THEFT F/AUTO", "METHOD": "OTHERS",
     "BLOCK": "3200 - 3299 BLOCK OF M STREET NW", "WARD": "2", "DISTRICT": "2",
     "SHIFT": "EVENING", "LATITUDE": "0", "LONGITUDE": "0", "OBJECTID": "2"},  # failed geocode -> NULL
    # sexual-offense taxonomy check
    {"CCN": "26098767", "REPORT_DAT": ms(RECENT_B), "START_DATE": ms(RECENT_B - timedelta(hours=8)),
     "OFFENSE": "SEX ABUSE", "METHOD": "OTHERS",
     "BLOCK": "1200 - 1299 BLOCK OF H STREET NE", "WARD": "6", "DISTRICT": "1",
     "SHIFT": "EVENING", "LATITUDE": "38.9002", "LONGITUDE": "-76.9895", "OBJECTID": "3"},
]

# PG County: the two records deliberately use different column-name
# generations (clearance_code_inc_type/street_address vs offense/location)
# to prove the coalescing transform handles both.
pgc_records = [
    {"incident_case_id": "PGC0001", "date": _iso(RECENT_B),
     "clearance_code_inc_type": "THEFT FROM AUTO",
     "street_address": "7700 BLK LANDOVER RD", "city": "LANDOVER",
     "zip_code": "20785", "pgpd_sector": "H",
     "latitude": "38.9340", "longitude": "-76.8721"},
    {"incident_case_id": "PGC0002", "date": _iso(RECENT_B),
     "offense": "HOMICIDE", "location": "6300 BLK MARLBORO PIKE",
     "city": "DISTRICT HEIGHTS", "pgpd_beat": "K1",
     "latitude": "38.8570", "longitude": "-76.8880"},
]

land_raw(moco_records, "moco")
land_raw(dc_records, "dc")
land_raw(pgc_records, "pgc")
load_duckdb.run()
load_duckdb.run()  # second run proves idempotency (no duplicate keys)
export_site_data.run()

con = duckdb.connect(str(WAREHOUSE_PATH))
print("\n--- fct_incidents ---")
print(con.execute("""
    SELECT incident_key, jurisdiction, occurred_at, offense_raw,
           offense_category, severity_weight, area_name, latitude, victims, method
    FROM marts.fct_incidents ORDER BY incident_key
""").df().to_string(index=False))
print("\n--- daily_counts ---")
print(con.execute("SELECT * FROM marts.daily_counts ORDER BY occurred_date").df().to_string(index=False))

n = con.execute("SELECT COUNT(*) FROM marts.fct_incidents").fetchone()[0]
assert n == 9, f"expected 9 unique incidents, got {n}"
dup = con.execute("SELECT occurred_at FROM marts.fct_incidents WHERE incident_key='moco-201234567'").fetchone()[0]
assert str(dup).startswith(RECENT_A_FIX.strftime("%Y-%m-%d %H:%M")), "dedupe should keep the latest version"
geo = con.execute("SELECT latitude FROM marts.fct_incidents WHERE incident_key='dc-26098766'").fetchone()[0]
assert geo is None, "0,0 geocode should be nulled"

# unified taxonomy mapping across both sources
categories = dict(con.execute(
    "SELECT incident_key, offense_category FROM marts.fct_incidents").fetchall())
expected_categories = {
    "moco-201234570": "homicide",   # Murder and Nonnegligent Manslaughter
    "moco-201234568": "violent",    # Aggravated Assault
    "moco-201234567": "vehicle",    # Theft From Motor Vehicle
    "moco-160000001": "violent",    # Robbery
    "dc-26098765": "violent",       # ROBBERY
    "dc-26098766": "vehicle",       # THEFT F/AUTO
    "dc-26098767": "sexual",        # SEX ABUSE
    "pgc-PGC0001": "vehicle",       # THEFT FROM AUTO (new-gen columns)
    "pgc-PGC0002": "homicide",      # HOMICIDE (old-gen columns, coalesced)
}
for key, expected in expected_categories.items():
    assert categories[key] == expected, f"{key}: expected {expected}, got {categories[key]}"

# --- exported site data ---
summary = json.loads((SITE_DATA_DIR / "summary.json").read_text())
incidents = json.loads((SITE_DATA_DIR / "incidents.json").read_text())
trends = json.loads((SITE_DATA_DIR / "trends.json").read_text())
heatmap = json.loads((SITE_DATA_DIR / "heatmap.json").read_text())

assert summary["total_records"] == 9
assert summary["data_start_date"] == "2016-08-15", summary["data_start_date"]
assert heatmap["rows"], "heatmap should have rows"
assert heatmap["columns"] == ["weekday", "hour", "jurisdiction", "offense_category", "count"], heatmap["columns"]

digest = json.loads((SITE_DATA_DIR / "digest.json").read_text())
assert digest["latest_day"], "digest must identify the latest data day"
assert digest["bullets"] and any("incidents were reported" in b for b in digest["bullets"]), digest["bullets"]
assert {r["jurisdiction"] for r in digest["by_jurisdiction"]} <= {"dc", "moco", "pgc"}
assert digest["notable"], "digest should list notable incidents"

# --- the email newsletter builds from the same digest ---
from export.send_digest_email import build_html, build_narrative  # noqa: E402

narrative = build_narrative(digest)
assert narrative and all(p.strip() for p in narrative), "narrative must not be empty"
assert any("The record for" in p for p in narrative), narrative
email_html = build_html(digest, "https://example.test")
assert "CRIME WATCH" in email_html
assert "{{ unsubscribe_url }}" in email_html, "unsubscribe link must be present"
assert "https://example.test/daily.html" in email_html
assert "AGENCY LABEL" in email_html, "agency labels must appear on notable incidents"

keys = {row[incidents["columns"].index("incident_key")] for row in incidents["rows"]}
assert "moco-160000001" not in keys, "2016 record must be outside the incident window"
assert {"moco-201234567", "moco-201234568", "dc-26098765", "dc-26098766"} <= keys, keys
geo_idx = incidents["columns"].index("latitude")
dc2 = next(r for r in incidents["rows"] if r[incidents["columns"].index("incident_key")] == "dc-26098766")
assert dc2[geo_idx] is None, "nulled geocode must survive export"

trend_dates = {r[0] for r in trends["rows"]}
assert "2016-08-15" in trend_dates, "full-history trends must include the 2016 record"
assert all(len(r) == 4 for r in trends["rows"]), "trends rows are [date, jurisdiction, category, count]"

print("\nAll assertions passed: dedupe, idempotency, geocode nulling, taxonomy mapping, site export.")
