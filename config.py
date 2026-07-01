"""Central configuration for the DMV crime pipeline.

All paths are relative to the project root so the pipeline runs the same
locally and inside GitHub Actions.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"
WAREHOUSE_PATH = PROJECT_ROOT / "data" / "warehouse" / "crime.duckdb"
STATE_PATH = PROJECT_ROOT / "state" / "watermarks.json"
SITE_DATA_DIR = PROJECT_ROOT / "site" / "data"

# How far back to reach on the very first run (no watermark yet).
# Keep this modest for development; widen it for the real backfill.
INITIAL_LOOKBACK_DAYS = 90

# Montgomery County: Socrata SODA API, dataset icn6-v9z3.
# Set the SOCRATA_APP_TOKEN environment variable to raise rate limits
# (free token from https://data.montgomerycountymd.gov/profile/edit/developer_settings).
MOCO = {
    "source_name": "moco",
    "base_url": "https://data.montgomerycountymd.gov/resource/icn6-v9z3.json",
    "watermark_field": "start_date",  # floating timestamp in SODA
    "page_size": 5000,
}

# DC: MPD FEEDS FeatureServer on maps2.dcgis.dc.gov.
# Layer ids are per-year, so the extractor picks the layer from this map.
# When 2027 opens, add its layer id here and nothing else changes.
DC = {
    "source_name": "dc",
    "base_url": "https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/MPD/FeatureServer",
    "year_layers": {
        2026: 41,
        2025: 7,
    },
    "watermark_field": "REPORT_DAT",  # esri epoch-millis timestamp
    "page_size": 1000,  # layer MaxRecordCount is 1000
}
