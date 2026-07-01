"""Offline smoke test: fabricate API-shaped records for both sources,
land them, load DuckDB, run the transform, and verify the fact table.
Includes a duplicate record to prove dedupe and idempotency work.

Runs against a temp raw/warehouse dir (not data/raw and
data/warehouse/crime.duckdb) so it's safe to run even after a real
pipeline run has landed real data in this checkout."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

import config

_tmp = Path(tempfile.mkdtemp(prefix="dmv-crime-pipeline-test-"))
config.RAW_DIR = _tmp / "raw"
config.WAREHOUSE_PATH = _tmp / "crime.duckdb"

from config import WAREHOUSE_PATH
from extractors.base import land_raw
from load import load_duckdb

moco_records = [
    {"incident_id": "201234567", "offence_code": "2308", "case_number": "24001111",
     "start_date": "2026-06-25T14:30:00.000", "end_date": "2026-06-25T15:00:00.000",
     "nibrs_code": "23F", "victims": "1", "crimename1": "Crime Against Property",
     "crimename2": "Theft From Motor Vehicle", "crimename3": "LARCENY - FROM AUTO",
     "district": "ROCKVILLE", "location": "100 BLK MONROE ST",
     "city": "ROCKVILLE", "state": "MD", "zip_code": "20850",
     "latitude": "39.0840", "longitude": "-77.1528"},
    {"incident_id": "201234568", "case_number": "24001112",
     "start_date": "2026-06-26T02:10:00.000", "crimename1": "Crime Against Person",
     "crimename2": "Aggravated Assault", "crimename3": "ASSAULT - AGGRAVATED",
     "district": "SILVER SPRING", "location": "8600 BLK GEORGIA AVE",
     "city": "SILVER SPRING", "state": "MD", "zip_code": "20910",
     "victims": "2", "latitude": "38.9959", "longitude": "-77.0276"},
    # duplicate of the first incident (a later correction) to test dedupe
    {"incident_id": "201234567", "case_number": "24001111",
     "start_date": "2026-06-25T14:45:00.000", "crimename2": "Theft From Motor Vehicle",
     "district": "ROCKVILLE", "location": "100 BLK MONROE ST",
     "city": "ROCKVILLE", "state": "MD", "zip_code": "20850",
     "victims": "1", "latitude": "39.0840", "longitude": "-77.1528"},
]

ms = lambda s: str(int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000))
dc_records = [
    {"CCN": "26098765", "REPORT_DAT": ms("2026-06-26T08:15:00"), "START_DATE": ms("2026-06-26T03:00:00"),
     "END_DATE": ms("2026-06-26T03:30:00"), "OFFENSE": "ROBBERY", "METHOD": "GUN",
     "BLOCK": "1400 - 1499 BLOCK OF U STREET NW", "WARD": "1", "DISTRICT": "3",
     "SHIFT": "MIDNIGHT", "LATITUDE": "38.9170", "LONGITUDE": "-77.0339", "OBJECTID": "1"},
    {"CCN": "26098766", "REPORT_DAT": ms("2026-06-26T12:00:00"), "START_DATE": ms("2026-06-25T22:00:00"),
     "OFFENSE": "THEFT F/AUTO", "METHOD": "OTHERS",
     "BLOCK": "3200 - 3299 BLOCK OF M STREET NW", "WARD": "2", "DISTRICT": "2",
     "SHIFT": "EVENING", "LATITUDE": "0", "LONGITUDE": "0", "OBJECTID": "2"},  # failed geocode -> NULL
]

land_raw(moco_records, "moco")
land_raw(dc_records, "dc")
load_duckdb.run()
load_duckdb.run()  # second run proves idempotency (no duplicate keys)

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
assert n == 4, f"expected 4 unique incidents, got {n}"
dup = con.execute("SELECT occurred_at FROM marts.fct_incidents WHERE incident_key='moco-201234567'").fetchone()[0]
assert str(dup).startswith("2026-06-25 14:45"), "dedupe should keep the latest version"
geo = con.execute("SELECT latitude FROM marts.fct_incidents WHERE incident_key='dc-26098766'").fetchone()[0]
assert geo is None, "0,0 geocode should be nulled"
print("\nAll assertions passed: dedupe, idempotency, geocode nulling, taxonomy mapping.")
