"""Shared plumbing for all extractors.

Responsibilities:
  1. Watermark state: remember the max timestamp seen per source so each
     run only pulls new or updated records (incremental extraction).
  2. Resilient HTTP: retries with backoff for flaky government endpoints.
  3. Raw landing zone: write untouched API responses to parquet,
     partitioned by source and extraction date. Raw means raw: no
     renaming, no type coercion, no filtering. All shaping happens
     downstream in the warehouse.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from config import BACKFILL_START, RAW_DIR, STATE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------- state

def read_watermark(source_name: str) -> datetime:
    """Return the last high-water mark for a source, or the full-history
    backfill start if this is the first run (no watermark yet)."""
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        raw = state.get(source_name)
        if raw:
            return datetime.fromisoformat(raw)
    return BACKFILL_START


def write_watermark(source_name: str, value: datetime) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    state[source_name] = value.isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2))


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


# ---------------------------------------------------------------- landing

def land_raw(records: list[dict], source_name: str) -> Path | None:
    """Write one extraction batch to the raw zone as parquet.

    Layout: data/raw/{source}/extract_date=YYYY-MM-DD/{timestamp}.parquet
    Everything is stored as string to guarantee the raw layer never
    fails on a source-side type quirk. Typing happens in the warehouse.
    """
    if not records:
        logging.info("[%s] No new records to land", source_name)
        return None

    frame = pd.DataFrame(records).astype("string")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    out_dir = RAW_DIR / source_name / f"extract_date={today}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source_name}_{today}_{stamp}.parquet"
    frame.to_parquet(out_path, index=False)
    logging.info("[%s] Landed %d records -> %s", source_name, len(frame), out_path)
    return out_path
