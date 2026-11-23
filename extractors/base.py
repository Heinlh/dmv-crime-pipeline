"""Shared plumbing for all extractors.

Responsibilities:
  1. Watermark state: remember the max timestamp seen per source so each
     run only pulls new or updated records (incremental extraction).
  2. Resilient HTTP: retries with backoff for flaky government endpoints.
  3. Raw landing zone: write API responses to parquet, partitioned by
     source and extraction date. Raw means raw: no renaming, no type
     coercion, no row filtering; all shaping happens downstream in the
     warehouse. Two things are enforced at this boundary because the raw
     zone is published on the public `data` branch: only the source's
     allowlisted fields are kept (config `fields`), and every row is
     stamped with when it was fetched (config.EXTRACTED_AT), so the
     transform can tell an agency's correction from the version it
     replaces.

Paths are read from `config` at call time rather than bound at import,
so tests and the CI workflow can redirect the raw zone and watermarks.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

import config
from config import BACKFILL_START
from extractors import privacy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------- state

def read_watermark(source_name: str) -> datetime:
    """Return the last high-water mark for a source, or the full-history
    backfill start if this is the first run (no watermark yet)."""
    if config.STATE_PATH.exists():
        state = json.loads(config.STATE_PATH.read_text())
        raw = state.get(source_name)
        if raw:
            return datetime.fromisoformat(raw)
    return BACKFILL_START


def write_watermark(source_name: str, value: datetime) -> None:
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(config.STATE_PATH.read_text()) if config.STATE_PATH.exists() else {}
    state[source_name] = value.isoformat()
    config.STATE_PATH.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------- http

def make_session(retries: int = 4, backoff_seconds: float = 2.0) -> requests.Session:
    """Session with manual retry wrapper via request_json below."""
    session = requests.Session()
    session.headers["User-Agent"] = "dmv-crime-pipeline (portfolio project)"
    session.request_retries = retries
    session.request_backoff = backoff_seconds
    return session


def request_json(session: requests.Session, url: str, params: dict) -> dict | list:
    """GET with exponential backoff. Raises after final attempt fails."""
    retries = getattr(session, "request_retries", 4)
    backoff = getattr(session, "request_backoff", 2.0)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            wait = backoff * (2 ** attempt)
            logging.warning("Request failed (attempt %d/%d): %s. Retrying in %.0fs",
                            attempt + 1, retries, error, wait)
            time.sleep(wait)
    raise RuntimeError(f"Request failed after {retries} attempts: {url}") from last_error


# ---------------------------------------------------------------- fields

def select_fields(source_name: str, live_fields, derived=()) -> list[str]:
    """The fields to request from a live endpoint: the source's allowlist
    plus any fetch_only inputs, limited to what the endpoint actually has.
    `derived` names fields the extractor produces itself (coordinates
    flattened from geometry, a coarsened address), which are never
    requested. Requesting only these keeps every query valid when a
    source lacks a candidate name, and keeps unlisted fields from ever
    being fetched. Logs allowlisted names the endpoint lacks, so a
    source-side rename shows up in the run log instead of as silent NULLs."""
    source = config.SOURCES[source_name]
    live = set(live_fields)
    wanted = [f for f in (*source["fields"], *source.get("fetch_only", [])) if f not in derived]
    missing = [f for f in wanted if f not in live]
    if missing:
        logging.info("[%s] allowlisted fields not offered by this endpoint: %s",
                     source_name, ", ".join(missing))
    return [f for f in wanted if f in live]


def socrata_columns(session: requests.Session, resource_url: str) -> list[str]:
    """Column names a Socrata dataset publishes, from the metadata
    endpoint that sits beside its SODA API (/api/views/{id}.json next to
    /resource/{id}.json)."""
    meta = request_json(session, resource_url.replace("/resource/", "/api/views/"), params={})
    return [c["fieldName"] for c in meta.get("columns", [])]


def arcgis_fields(session: requests.Session, layer_url: str) -> list[str]:
    """Field names an ArcGIS feature layer publishes."""
    layer = request_json(session, layer_url, params={"f": "json"})
    if "error" in layer:
        raise RuntimeError(f"ArcGIS error reading {layer_url}: {layer['error']}")
    return [f["name"] for f in layer.get("fields", [])]


# ---------------------------------------------------------------- landing

def extraction_stamp(moment: datetime) -> str:
    """Format an extraction time for the EXTRACTED_AT column: UTC, fixed
    width, microsecond precision, so string order is time order."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def prepare_raw_frame(source_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Shape a batch for the raw zone: coarsen any finer-than-block
    location, then keep only the source's allowlisted fields (plus the
    lineage column if the batch already carries one), as strings. Shared
    by land_raw and the raw-zone migration so the two can never disagree
    about what the public raw zone may hold."""
    frame = privacy.coarsen(source_name, frame)
    allowed = [*config.SOURCES[source_name]["fields"], config.EXTRACTED_AT]
    dropped = sorted(set(frame.columns) - set(allowed))
    if dropped:
        logging.info("[%s] dropping non-allowlisted fields: %s", source_name, ", ".join(dropped))
    return frame[[c for c in allowed if c in frame.columns]].astype("string")


def land_raw(records: list[dict], source_name: str) -> Path | None:
    """Write one extraction batch to the raw zone as parquet.

    Layout: {RAW_DIR}/{source}/extract_date=YYYY-MM-DD/{source}_{date}_{HHMMSS}.parquet
    Everything is stored as string to guarantee the raw layer never
    fails on a source-side type quirk. Typing happens in the warehouse.
    Fields outside the source's allowlist are dropped here, whatever the
    extractor fetched, so the public raw zone can never widen by accident.
    """
    if not records:
        logging.info("[%s] No new records to land", source_name)
        return None

    frame = prepare_raw_frame(source_name, pd.DataFrame(records))

    # one instant for the row stamp, the partition, and the file name
    moment = datetime.now(timezone.utc)
    frame[config.EXTRACTED_AT] = extraction_stamp(moment)
    day = moment.strftime("%Y-%m-%d")
    out_dir = config.RAW_DIR / source_name / f"extract_date={day}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source_name}_{day}_{moment:%H%M%S}.parquet"
    # two batches in the same second must not overwrite each other
    suffix = 1
    while out_path.exists():
        suffix += 1
        out_path = out_dir / f"{source_name}_{day}_{moment:%H%M%S}_{suffix}.parquet"
    frame.to_parquet(out_path, index=False)
    logging.info("[%s] Landed %d records -> %s", source_name, len(frame), out_path)
    return out_path
