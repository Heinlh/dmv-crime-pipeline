"""DC (MPD) crime extractor.

Source: Open Data DC, Crime Incidents feature layers on the MPD FEEDS
FeatureServer. DC publishes one layer per calendar year (2026 is layer
41), so the extractor queries every configured layer whose year falls
inside the extraction window. Attributes already include block-level
LATITUDE/LONGITUDE, so geometry is not requested.

Incremental strategy: filter on REPORT_DAT (epoch milliseconds) greater
than the stored watermark, page with resultOffset ordered by REPORT_DAT,
and advance the watermark to the max REPORT_DAT seen. Same overlap and
dedupe approach as the Montgomery County extractor.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import DC
from extractors.base import land_raw, make_session, read_watermark, request_json, write_watermark

logger = logging.getLogger("dc")

OVERLAP_HOURS = 24


def _query_layer(session, layer_id: int, since: datetime) -> list[dict]:
    url = f"{DC['base_url']}/{layer_id}/query"
    since_sql = since.strftime("%Y-%m-%d %H:%M:%S")
    records: list[dict] = []
    offset = 0
    while True:
        payload = request_json(session, url, params={
            "where": f"{DC['watermark_field']} > TIMESTAMP '{since_sql}'",
            "outFields": "*",
            "orderByFields": f"{DC['watermark_field']} ASC",
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": DC["page_size"],
            "f": "json",
        })
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on layer {layer_id}: {payload['error']}")
        features = payload.get("features", [])
        records.extend(f["attributes"] for f in features)
        logger.info("Layer %d: fetched offset %d (%d rows)", layer_id, offset, len(features))
        if len(features) < DC["page_size"]:
            break
        offset += DC["page_size"]
    return records


def extract() -> list[dict]:
    session = make_session()
    watermark = read_watermark(DC["source_name"]) - timedelta(hours=OVERLAP_HOURS)
    logger.info("Pulling DC incidents with REPORT_DAT > %s", watermark.isoformat())

    current_year = datetime.now(timezone.utc).year
    years_in_window = [y for y in DC["year_layers"] if watermark.year <= y <= current_year]

    records: list[dict] = []
    for year in sorted(years_in_window):
        records.extend(_query_layer(session, DC["year_layers"][year], watermark))

    if records:
        max_millis = max(r[DC["watermark_field"]] for r in records if r.get(DC["watermark_field"]))
        new_mark = datetime.fromtimestamp(max_millis / 1000, tz=timezone.utc)
        write_watermark(DC["source_name"], new_mark)
    return records


def run() -> None:
    records = extract()
    land_raw(records, DC["source_name"])


if __name__ == "__main__":
    run()
