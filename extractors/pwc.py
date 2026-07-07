"""Prince William County (PWC Police) crime extractor.

Source: the county's "Public_Crime_Reports" hosted feature layer on
ArcGIS Online, the data behind PWC's public Crime Data Explorer.
Updated once daily by the county; covers a rolling 3-year window.
Locations are mapped to the nearest 100 block by the county, and the
county withholds sexual offense records entirely for victim privacy.

Incremental strategy: filter on OccurredOn (epoch milliseconds) past
the stored watermark. The layer publishes no report-date column and
reports are routinely filed days after the offense, so the overlap
window is a deep OVERLAP_DAYS rather than the 24 hours the daily
sources use; the loader dedupes on CaseNo, so overlap never creates
duplicates. The layer stores state-plane geometry, so queries request
outSR=4326 to land plain lat/lng.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import PWC_VA
from extractors.base import land_raw, make_session, read_watermark, request_json, write_watermark

logger = logging.getLogger("pwc")

# Deep overlap: PWC keys records on occurrence time, not report time, so
# a shallow window would permanently miss reports filed late. Seven days
# catches the practical filing lag; the transform dedupes the re-pulls.
OVERLAP_DAYS = 7


def extract() -> list[dict]:
    session = make_session()
    watermark = read_watermark(PWC_VA["source_name"]) - timedelta(days=OVERLAP_DAYS)
    since_sql = watermark.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Pulling Prince William incidents with OccurredOn > %s", since_sql)

    url = f"{PWC_VA['base_url']}/{PWC_VA['layer_id']}/query"
    records: list[dict] = []
    offset = 0
    while True:
        payload = request_json(session, url, params={
            "where": f"{PWC_VA['watermark_field']} > TIMESTAMP '{since_sql}'",
            "outFields": ",".join(PWC_VA["out_fields"]),
            "orderByFields": f"{PWC_VA['watermark_field']} ASC",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PWC_VA["page_size"],
            "f": "json",
        })
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on Public_Crime_Reports: {payload['error']}")
        features = payload.get("features", [])
        for feature in features:
            record = dict(feature["attributes"])
            geometry = feature.get("geometry") or {}
            record["longitude"] = geometry.get("x")
            record["latitude"] = geometry.get("y")
            records.append(record)
        logger.info("Public_Crime_Reports: fetched offset %d (%d rows)", offset, len(features))
        if len(features) < PWC_VA["page_size"]:
            break
        offset += PWC_VA["page_size"]

    if records:
        max_millis = max(r[PWC_VA["watermark_field"]] for r in records
                         if r.get(PWC_VA["watermark_field"]))
        new_mark = datetime.fromtimestamp(max_millis / 1000, tz=timezone.utc)
        write_watermark(PWC_VA["source_name"], new_mark)
    return records


def run() -> None:
    records = extract()
    land_raw(records, PWC_VA["source_name"])


if __name__ == "__main__":
    run()
