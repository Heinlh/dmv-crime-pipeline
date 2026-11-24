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

import duckdb
import pandas as pd
import requests

import config
from config import BACKFILL_START
from extractors import privacy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------- state

def read_state(key: str):
    """A value stored in the watermarks file, or None if it has none yet."""
    if config.STATE_PATH.exists():
        return json.loads(config.STATE_PATH.read_text()).get(key)
    return None


def write_state(key: str, value) -> None:
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(config.STATE_PATH.read_text()) if config.STATE_PATH.exists() else {}
    state[key] = value
    config.STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def read_watermark(source_name: str) -> datetime:
    """Return the last high-water mark for a source, or the full-history
    backfill start if this is the first run (no watermark yet)."""
    raw = read_state(source_name)
    return datetime.fromisoformat(raw) if raw else BACKFILL_START


def write_watermark(source_name: str, value: datetime) -> None:
    write_state(source_name, value.isoformat())


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


def socrata_meta(session: requests.Session, resource_url: str) -> dict:
    """A Socrata dataset's metadata, from the endpoint beside its SODA API
    (/api/views/{id}.json next to /resource/{id}.json): its columns, and
    rowsUpdatedAt, when its rows last changed."""
    return request_json(session, resource_url.replace("/resource/", "/api/views/"), params={})


def socrata_columns(session: requests.Session, resource_url: str) -> list[str]:
    """Column names a Socrata dataset publishes."""
    return [c["fieldName"] for c in socrata_meta(session, resource_url).get("columns", [])]


def arcgis_layer(session: requests.Session, layer_url: str) -> dict:
    """An ArcGIS feature layer's metadata: fields, objectIdField, and
    editingInfo (when its data last changed)."""
    layer = request_json(session, layer_url, params={"f": "json"})
    if "error" in layer:
        raise RuntimeError(f"ArcGIS error reading {layer_url}: {layer['error']}")
    return layer


def layer_fields(layer: dict) -> list[str]:
    return [f["name"] for f in layer.get("fields", [])]


def layer_object_id(layer: dict) -> str:
    """The layer's unique row id field, falling back to the field typed
    as an object id, then the ArcGIS default name."""
    if layer.get("objectIdField"):
        return layer["objectIdField"]
    for field in layer.get("fields", []):
        if field.get("type") == "esriFieldTypeOID":
            return field["name"]
    return "OBJECTID"


# Paging must sort on something unique. Offset paging over a sort with ties
# (many rows share one OccurredOn or start_date) lets the server order the
# tied rows differently on each page request, so consecutive pages overlap
# and some rows fall between them and are never fetched. A Prince William
# batch held 48,000 duplicated rows this way while missing others.

def socrata_order(field: str) -> str:
    """$order for stable Socrata paging: the field, then the row id."""
    return f"{field} ASC, :id ASC"


def arcgis_order(field: str | None, object_id: str) -> str:
    """orderByFields for stable ArcGIS paging: the field, then the object id."""
    return f"{field} ASC, {object_id} ASC" if field else f"{object_id} ASC"


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
    return _write_batch(source_name, prepare_raw_frame(source_name, pd.DataFrame(records)))


# ------------------------------------------------- change-detected landing

def incident_key_sql(source_name: str) -> str:
    """SQL for a row's incident key: the first non-blank of the source's
    incident_key fields, trimmed (Fairfax and Prince William pad theirs).
    sql/transform.sql partitions on the same expression."""
    parts = [f"NULLIF(TRIM(\"{f}\"), '')" for f in config.SOURCES[source_name]["incident_key"]]
    return parts[0] if len(parts) == 1 else f"COALESCE({', '.join(parts)})"


def _row_hash_sql(fields: list[str]) -> str:
    """md5 over every allowlisted field, NULL kept distinct from ''."""
    cells = ", ".join(f"COALESCE(CAST(\"{f}\" AS VARCHAR), chr(0))" for f in fields)
    return f"md5(concat_ws(chr(31), {cells}))"


def changed_incidents(source_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """The rows of `frame` (already shaped by prepare_raw_frame) belonging
    to incidents that are new, or whose set of rows differs from the
    incident's latest stored version: an incident is compared as a whole,
    every one of its rows, against the rows of the most recent extraction
    that held it. Changed incidents come back complete, so the latest
    extraction of every incident is always its whole current state, which
    is what sql/transform.sql relies on. A revert (A, then B, then A
    again) lands again, because A differs from the latest version, B.

    Identical duplicate rows within the batch are dropped. A row with no
    incident key is its own incident, keyed by its content."""
    fields = config.SOURCES[source_name]["fields"]
    present = [f for f in fields if f in frame.columns]
    # string dtype, so absent columns are true NULLs on both sides
    padded = frame.reindex(columns=fields).astype("string")
    key = incident_key_sql(source_name)
    row_hash = _row_hash_sql(fields)
    stored = sorted((config.RAW_DIR / source_name).glob("*/*.parquet"))

    con = duckdb.connect()
    con.register("incoming", padded)
    if stored:
        files = ", ".join(f"'{p.as_posix()}'" for p in stored)
        cols = ", ".join(f'"{f}"' for f in fields)
        con.execute(f"""
            CREATE TEMP VIEW stored_raw AS
            SELECT * FROM read_parquet([{files}], union_by_name=true, hive_partitioning=false)""")
        # pad allowlisted columns no stored file happened to carry
        have = {r[0] for r in con.execute("DESCRIBE stored_raw").fetchall()}
        select = ", ".join(f'"{f}"' if f in have else f'NULL::VARCHAR AS "{f}"' for f in fields)
        stored_sql = f"""
            SELECT _k, md5(array_to_string(list_sort(list_distinct(list(_h))), ',')) AS ih
            FROM (
                SELECT COALESCE(_k0, '#' || _h) AS _k, _h, {config.EXTRACTED_AT} AS x,
                       MAX({config.EXTRACTED_AT}) OVER (PARTITION BY COALESCE(_k0, '#' || _h)) AS latest
                FROM (SELECT {key} AS _k0, {row_hash} AS _h, {config.EXTRACTED_AT}
                      FROM (SELECT {select}, {config.EXTRACTED_AT} FROM stored_raw))
            ) WHERE x = latest
            GROUP BY _k"""
    else:
        stored_sql = "SELECT NULL::VARCHAR AS _k, NULL::VARCHAR AS ih WHERE false"

    result = con.execute(f"""
        WITH incoming_rows AS (
            SELECT DISTINCT * FROM incoming
        ),
        keyed AS (
            SELECT *, COALESCE({key}, '#' || {row_hash}) AS _k, {row_hash} AS _h FROM incoming_rows
        ),
        incoming_hash AS (
            SELECT _k, md5(array_to_string(list_sort(list_distinct(list(_h))), ',')) AS ih
            FROM keyed GROUP BY _k
        ),
        stored_hash AS ({stored_sql})
        SELECT keyed.* EXCLUDE (_k, _h)
        FROM keyed
        JOIN incoming_hash USING (_k)
        LEFT JOIN stored_hash USING (_k)
        WHERE stored_hash.ih IS DISTINCT FROM incoming_hash.ih
        ORDER BY _k, _h
    """).df()
    con.close()
    return result[present].astype("string")


def new_rows(source_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """For row-versioned sources (config "versioning": "rows"): the rows
    of `frame` whose content has never been stored, ignoring the source's
    unstable_fields, which the agency regenerates between pulls."""
    source = config.SOURCES[source_name]
    fields = source["fields"]
    content = [f for f in fields if f not in source.get("unstable_fields", [])]
    present = [f for f in fields if f in frame.columns]
    padded = frame.reindex(columns=fields).astype("string")
    row_hash = _row_hash_sql(content)
    stored = sorted((config.RAW_DIR / source_name).glob("*/*.parquet"))

    con = duckdb.connect()
    con.register("incoming", padded)
    if stored:
        files = ", ".join(f"'{p.as_posix()}'" for p in stored)
        con.execute(f"""CREATE TEMP VIEW stored_raw AS SELECT * FROM
                        read_parquet([{files}], union_by_name=true, hive_partitioning=false)""")
        have = {r[0] for r in con.execute("DESCRIBE stored_raw").fetchall()}
        select = ", ".join(f'"{f}"' if f in have else f'NULL::VARCHAR AS "{f}"' for f in content)
        seen = f"SELECT DISTINCT {row_hash} AS _h FROM (SELECT {select} FROM stored_raw)"
    else:
        seen = "SELECT NULL::VARCHAR AS _h WHERE false"
    result = con.execute(f"""
        SELECT * EXCLUDE (_h) FROM (
            SELECT *, {row_hash} AS _h FROM incoming
            QUALIFY ROW_NUMBER() OVER (PARTITION BY {row_hash} ORDER BY {_row_hash_sql(fields)}) = 1
        ) WHERE _h NOT IN ({seen})
        ORDER BY _h
    """).df()
    con.close()
    return result[present].astype("string")


def land_changed(records: list[dict], source_name: str) -> Path | None:
    """Land only what is new: for incident-versioned sources, the
    incidents that are new or have changed since their latest stored
    version (changed_incidents); for row-versioned ones, the rows never
    stored before (new_rows). This lets an extractor re-pull far more than
    is new (a deep overlap, or a whole dataset) without the raw zone
    growing by anything but real changes."""
    if not records:
        logging.info("[%s] No records fetched", source_name)
        return None
    frame = prepare_raw_frame(source_name, pd.DataFrame(records))
    if config.SOURCES[source_name].get("versioning") == "rows":
        changed = new_rows(source_name, frame)
    else:
        changed = changed_incidents(source_name, frame)
    logging.info("[%s] %d rows fetched, %d rows in new or changed incidents",
                 source_name, len(frame), len(changed))
    if changed.empty:
        return None
    return _write_batch(source_name, changed)


def _write_batch(source_name: str, frame: pd.DataFrame) -> Path:
    """Stamp a shaped batch with its extraction time and write it."""
    frame = frame.copy()
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
