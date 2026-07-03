"""Montgomery County crime extractor.

Source: dataMontgomery Crime dataset (icn6-v9z3) via the Socrata SODA API.
Docs: https://dev.socrata.com/foundry/data.montgomerycountymd.gov/icn6-v9z3

Incremental strategy: filter on start_date greater than the stored
watermark using a SoQL $where clause, page with $limit/$offset ordered
by start_date so pagination is stable, and advance the watermark to the
max start_date seen. A small overlap window is subtracted from the
watermark to catch late-arriving records; downstream loading dedupes on
incident_id so overlap never creates duplicates.
"""

import logging
import os
from datetime import datetime, timedelta

from config import MOCO
from extractors.base import land_raw, make_session, read_watermark, request_json, write_watermark

logger = logging.getLogger("moco")

OVERLAP_HOURS = 24  # re-pull a trailing day to catch late corrections


def extract() -> list[dict]:
    session = make_session()
    # strip() because a token pasted into the repo secret with stray
    # whitespace (tab/newline) makes requests reject the header outright
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        session.headers["X-App-Token"] = token
    else:
        logger.warning("No SOCRATA_APP_TOKEN set; running with anonymous rate limits")

    watermark = read_watermark(MOCO["source_name"]) - timedelta(hours=OVERLAP_HOURS)
    # SODA floating timestamps have no timezone; format accordingly.
    since = watermark.strftime("%Y-%m-%dT%H:%M:%S")
    logger.info("Pulling Montgomery County incidents with start_date > %s", since)

    records: list[dict] = []
    offset = 0
    while True:
        page = request_json(session, MOCO["base_url"], params={
            "$where": f"{MOCO['watermark_field']} > '{since}'",
            "$order": f"{MOCO['watermark_field']} ASC",
            "$limit": MOCO["page_size"],
            "$offset": offset,
        })
        records.extend(page)
        logger.info("Fetched page at offset %d (%d rows)", offset, len(page))
        if len(page) < MOCO["page_size"]:
            break
        offset += MOCO["page_size"]

    if records:
        max_seen = max(r[MOCO["watermark_field"]] for r in records if r.get(MOCO["watermark_field"]))
        write_watermark(MOCO["source_name"], datetime.fromisoformat(max_seen))
    return records


def run() -> None:
    records = extract()
    land_raw(records, MOCO["source_name"])


if __name__ == "__main__":
    run()
