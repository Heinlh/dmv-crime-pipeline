"""Central configuration for the DMV crime pipeline.

All paths are relative to the project root so the pipeline runs the same
locally and inside GitHub Actions.
"""

from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"
WAREHOUSE_PATH = PROJECT_ROOT / "data" / "warehouse" / "crime.duckdb"
STATE_PATH = PROJECT_ROOT / "state" / "watermarks.json"
SITE_DATA_DIR = PROJECT_ROOT / "site" / "data"

# How far back to reach when there is no watermark yet (first run, or a
# run whose data/state cache was lost). July 2016 is when the Montgomery
# County dataset begins, so this is the full available history for both
# sources. Extraction is incremental after the first successful run as
# long as state/watermarks.json (and the raw parquet zone) persist.
BACKFILL_START = datetime(2016, 7, 1, tzinfo=timezone.utc)

# Montgomery County: Socrata SODA API, dataset icn6-v9z3.
# Set the SOCRATA_APP_TOKEN environment variable to raise rate limits
# (free token from https://data.montgomerycountymd.gov/profile/edit/developer_settings).
MOCO = {
    "source_name": "moco",
    "base_url": "https://data.montgomerycountymd.gov/resource/icn6-v9z3.json",
    "watermark_field": "start_date",  # floating timestamp in SODA
    "page_size": 5000,
}

# Prince George's County: Socrata SODA API on data.princegeorgescountymd.gov.
# The county split its crime data in two: wb4e-w4nf covers Feb 2017 to
# 5 July 2023 (frozen), xjru-idbe covers July 2023 to present. The
# current dataset is updated WEEKLY by the county, so PG County data is
# always a few days behind the daily sources. The extractor only reads
# the frozen dataset when the watermark predates its cutoff (backfill).
PGC = {
    "source_name": "pgc",
    "base_url": "https://data.princegeorgescountymd.gov/resource/xjru-idbe.json",
    "historical_url": "https://data.princegeorgescountymd.gov/resource/wb4e-w4nf.json",
    "historical_cutoff": "2023-07-06T00:00:00",
    "watermark_field": "date",  # floating timestamp in SODA
    "page_size": 5000,
}

# DC: MPD FEEDS FeatureServer on maps2.dcgis.dc.gov.
# DC publishes one "Crime Incidents in YYYY" layer per calendar year. The
# extractor discovers the year -> layer id map from the FeatureServer's
# own layer listing at runtime, so new years appear without config edits.
# fallback_year_layers is only used if that discovery request fails.
DC = {
    "source_name": "dc",
    "base_url": "https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/MPD/FeatureServer",
    "fallback_year_layers": {
        2026: 41,
        2025: 7,
        2024: 6,
        2023: 5,
        2022: 4,
        2021: 3,
        2020: 2,
    },
    "watermark_field": "REPORT_DAT",  # esri epoch-millis timestamp
    "page_size": 1000,  # layer MaxRecordCount is 1000
}
