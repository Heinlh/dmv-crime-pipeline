"""Prince George's County crime extractor.

Source: OpenPGC (Socrata SODA API), two datasets:
  - Crime Incidents July 2023 to Present (xjru-idbe), updated WEEKLY
  - Crime Incidents February 2017 to 5th July 2023 (wb4e-w4nf), frozen

Refresh strategy: the county republishes the whole current dataset at
once (every row carries the same :updated_at), so there is no per-row
change field to be incremental on. Instead the extractor reads the
dataset's rowsUpdatedAt and, when it has moved since the last run,
re-pulls the whole current dataset; land_changed then stores only the
incidents that are new or differ from their latest stored version, so a
weekly re-pull of ~82,000 rows adds only what actually changed. When
rowsUpdatedAt has not moved, nothing is fetched. The frozen dataset is
read only when the source has no state yet.

Paging sorts on :id so that pages can neither overlap nor skip rows.
Each dataset's published columns are read first and only allowlisted ones
requested ($select). The county's street_address is often an exact house
address, so land_changed coarsens it to block_address and never lands it
(extractors/privacy.py).
"""

import logging
import os

from config import PGC
from extractors.base import (land_changed, make_session, read_state, request_json,
                             select_fields, socrata_columns, socrata_meta, write_state)

logger = logging.getLogger("pgc")

STATE_KEY = "pgc:rows_updated_at"


def _session():
    session = make_session()
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        session.headers["X-App-Token"] = token
    else:
        logger.warning("No SOCRATA_APP_TOKEN set; running with anonymous rate limits")
    return session


def _pull_all(session, url: str) -> list[dict]:
    # block_address is derived at landing from street_address
    fields = select_fields(PGC["source_name"], socrata_columns(session, url),
                           derived=("block_address",))
    records: list[dict] = []
    offset = 0
    while True:
        page = request_json(session, url, params={
            "$select": ",".join(fields),
            "$order": ":id ASC",
            "$limit": PGC["page_size"],
            "$offset": offset,
        })
        records.extend(page)
        logger.info("%s: fetched offset %d (%d rows)", url.rsplit("/", 1)[-1], offset, len(page))
        if len(page) < PGC["page_size"]:
            return records
        offset += PGC["page_size"]


def extract() -> tuple[list[dict], int | None]:
    """Return (records, the current dataset's rowsUpdatedAt)."""
    session = _session()
    updated = socrata_meta(session, PGC["base_url"]).get("rowsUpdatedAt")
    prior = read_state(STATE_KEY)

    records: list[dict] = []
    if prior is None:
        logger.info("No %s yet: reading the frozen 2017-2023 dataset too", STATE_KEY)
        records.extend(_pull_all(session, PGC["historical_url"]))
    if prior is None or updated is None or updated != prior:
        records.extend(_pull_all(session, PGC["base_url"]))
    else:
        logger.info("Current dataset unchanged since rowsUpdatedAt %s; nothing to fetch", prior)
    return records, updated


def run() -> None:
    records, updated = extract()
    land_changed(records, PGC["source_name"])
    if updated is not None:
        write_state(STATE_KEY, updated)


if __name__ == "__main__":
    run()
