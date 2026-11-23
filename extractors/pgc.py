"""Prince George's County crime extractor.

Source: OpenPGC (Socrata SODA API), two datasets:
  - Crime Incidents July 2023 to Present (xjru-idbe), updated WEEKLY
  - Crime Incidents February 2017 to 5th July 2023 (wb4e-w4nf), frozen

Incremental strategy mirrors the Montgomery County extractor: filter on
the date field past the stored watermark, page with $limit/$offset, and
advance the watermark to the max date seen. The frozen historical
dataset is only queried when the watermark predates its cutoff (i.e. on
a backfill). Column names have shifted between the county's dataset
generations, so each dataset's published columns are read first and only
allowlisted ones are requested ($select); the transform coalesces across
candidate names. The county's street_address is often an exact house
address, so land_raw coarsens it to block_address and never lands it
(extractors/privacy.py).
"""

import logging
import os
from datetime import datetime, timedelta

from config import PGC
from extractors.base import (land_raw, make_session, read_watermark, request_json,
                             select_fields, socrata_columns, write_watermark)

logger = logging.getLogger("pgc")

OVERLAP_HOURS = 24


def _pull(session, url: str, since: str) -> list[dict]:
    # block_address is derived at landing from street_address
    fields = select_fields(PGC["source_name"], socrata_columns(session, url),
                           derived=("block_address",))
    records: list[dict] = []
    offset = 0
    while True:
        page = request_json(session, url, params={
            "$select": ",".join(fields),
            "$where": f"{PGC['watermark_field']} > '{since}'",
            "$order": f"{PGC['watermark_field']} ASC",
            "$limit": PGC["page_size"],
            "$offset": offset,
        })
        records.extend(page)
        logger.info("%s: fetched offset %d (%d rows)", url.rsplit("/", 1)[-1], offset, len(page))
        if len(page) < PGC["page_size"]:
            break
        offset += PGC["page_size"]
    return records


def extract() -> list[dict]:
    session = make_session()
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        session.headers["X-App-Token"] = token
    else:
        logger.warning("No SOCRATA_APP_TOKEN set; running with anonymous rate limits")

    watermark = read_watermark(PGC["source_name"]) - timedelta(hours=OVERLAP_HOURS)
    since = watermark.strftime("%Y-%m-%dT%H:%M:%S")
    logger.info("Pulling PG County incidents with %s > %s", PGC["watermark_field"], since)

    records: list[dict] = []
    if since < PGC["historical_cutoff"]:
        records.extend(_pull(session, PGC["historical_url"], since))
    records.extend(_pull(session, PGC["base_url"], since))

    if records:
        # Schema reconnaissance: the county has renamed columns between
        # dataset generations; this line documents what we actually got.
        logger.info("First record fields: %s", sorted(records[0].keys()))
        max_seen = max(r[PGC["watermark_field"]] for r in records if r.get(PGC["watermark_field"]))
        write_watermark(PGC["source_name"], datetime.fromisoformat(max_seen))
    return records


def run() -> None:
    records = extract()
    land_raw(records, PGC["source_name"])


if __name__ == "__main__":
    run()
