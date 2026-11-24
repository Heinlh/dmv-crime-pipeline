"""Montgomery County crime extractor.

Source: dataMontgomery Crime dataset (icn6-v9z3) via the Socrata SODA API.
Docs: https://dev.socrata.com/foundry/data.montgomerycountymd.gov/icn6-v9z3

Incremental strategy: pull every row whose :updated_at (Socrata's system
field for when a row was last published or modified) is newer than the
stored watermark, less an overlap. This catches reports published days
after the offense and later corrections alike; the old start_date
watermark never asked for an offense again once newer ones had been seen,
and so missed both (September 2026: 1,833 of 3,629 published rows).

The dataset has one row per offense, and an update may touch only one of
an incident's rows, so every incident the pull touches is then re-fetched
whole: land_changed stores incidents complete, and only when they differ
from their latest stored version, which makes the overlap and the
re-fetch free in storage. Paging sorts on :updated_at then :id so that
pages can neither overlap nor skip rows.

The watermark lives under STATE_KEY. With none (the first run after this
strategy replaced the start_date watermark) the pull covers the whole
dataset, which also recovers everything the old strategy missed.

Only allowlisted fields are requested ($select), intersected with the
columns the dataset publishes; see config MOCO["fields"].
"""

import logging
import os
from datetime import datetime, timedelta

from config import MOCO
from extractors.base import (land_changed, make_session, read_state, request_json,
                             select_fields, socrata_columns, socrata_order, write_state)

logger = logging.getLogger("moco")

STATE_KEY = "moco:updated_at"
# Re-ask for a day before the watermark: covers any time zone reading of
# the comparison and rows indexed late; unchanged incidents land nothing.
OVERLAP = timedelta(days=1)
# incident ids per completion request, keeping the URL short
COMPLETION_CHUNK = 150


def _session():
    session = make_session()
    # strip() because a token pasted into the repo secret with stray
    # whitespace (tab/newline) makes requests reject the header outright
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        session.headers["X-App-Token"] = token
    else:
        logger.warning("No SOCRATA_APP_TOKEN set; running with anonymous rate limits")
    return session


def _pull(session, select: str, where: str | None) -> list[dict]:
    records: list[dict] = []
    offset = 0
    while True:
        params = {"$select": select, "$order": socrata_order(MOCO["change_field"]),
                  "$limit": MOCO["page_size"], "$offset": offset}
        if where:
            params["$where"] = where
        page = request_json(session, MOCO["base_url"], params=params)
        records.extend(page)
        logger.info("Fetched page at offset %d (%d rows)", offset, len(page))
        if len(page) < MOCO["page_size"]:
            return records
        offset += MOCO["page_size"]


def extract() -> tuple[list[dict], str | None]:
    """Return (records, newest :updated_at seen)."""
    session = _session()
    fields = select_fields(MOCO["source_name"], socrata_columns(session, MOCO["base_url"]))
    select = ",".join([*fields, MOCO["change_field"]])

    mark = read_state(STATE_KEY)
    if mark:
        since = (datetime.fromisoformat(mark) - OVERLAP).strftime("%Y-%m-%dT%H:%M:%S")
        logger.info("Pulling Montgomery County rows updated after %s", since)
        records = _pull(session, select, f"{MOCO['change_field']} > '{since}'")
    else:
        logger.info("No %s watermark: pulling the whole dataset", STATE_KEY)
        records = _pull(session, select, None)

    if mark and records:
        # complete every touched incident: an update can touch one row of several
        ids = sorted({r["incident_id"] for r in records if r.get("incident_id")})
        for i in range(0, len(ids), COMPLETION_CHUNK):
            chunk = ", ".join("'" + x.replace("'", "''") + "'" for x in ids[i:i + COMPLETION_CHUNK])
            records.extend(_pull(session, select, f"incident_id in ({chunk})"))
        logger.info("Completed %d touched incidents", len(ids))

    # the watermark only ever advances, even if this run saw only overlap
    seen = [r[MOCO["change_field"]] for r in records if r.get(MOCO["change_field"])]
    newest = max([*seen, *([mark] if mark else [])], key=datetime.fromisoformat, default=None)
    return records, newest


def run() -> None:
    records, newest = extract()
    land_changed(records, MOCO["source_name"])
    # only once the batch is safely landed, so a failed landing re-pulls
    if newest:
        write_state(STATE_KEY, newest)


if __name__ == "__main__":
    run()
