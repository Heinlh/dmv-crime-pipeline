"""DC (MPD) crime extractor.

Source: Open Data DC, Crime Incidents feature layers on the MPD FEEDS
FeatureServer. DC publishes one "Crime Incidents in YYYY" layer per
calendar year, so the extractor first asks the FeatureServer for its
layer listing and builds the year -> layer id map from the layer names
(falling back to the ids pinned in config if that request fails), then
queries every year layer that falls inside the extraction window.
Attributes already include block-level LATITUDE/LONGITUDE, so geometry
is not requested.

Incremental strategy: filter on REPORT_DAT (epoch milliseconds) greater
than the stored watermark, page with resultOffset ordered by REPORT_DAT,
and advance the watermark to the max REPORT_DAT seen. Same overlap and
dedupe approach as the Montgomery County extractor.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from config import DC
from extractors.base import land_raw, make_session, read_watermark, request_json, write_watermark

logger = logging.getLogger("dc")

OVERLAP_HOURS = 24

# The service names its layers "Crime Incidents - 2026" today, but Open
# Data DC has also published them as "Crime Incidents in 2026", so match
# any separator between the phrase and the year.
LAYER_NAME_PATTERN = re.compile(r"Crime Incidents\s*(?:-|in)?\s*(\d{4})", re.IGNORECASE)


def discover_year_layers(session) -> dict[int, int]:
    """Build {year: layer_id} from the FeatureServer's own layer listing."""
    try:
        payload = request_json(session, DC["base_url"], params={"f": "json"})
        layers = {}
        for layer in payload.get("layers", []):
            match = LAYER_NAME_PATTERN.search(layer.get("name", ""))
            if match:
                layers[int(match.group(1))] = layer["id"]
        if layers:
            logger.info("Discovered %d year layers: %s", len(layers), sorted(layers))
            return layers
        logger.warning(
            "Layer discovery matched no crime layers; using config fallback. Layer names seen: %s",
            [layer.get("name") for layer in payload.get("layers", [])][:40],
        )
    except Exception:
        logger.exception("Layer discovery failed; using config fallback")
    return DC["fallback_year_layers"]


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

    year_layers = discover_year_layers(session)
    current_year = datetime.now(timezone.utc).year
    years_in_window = [y for y in year_layers if watermark.year <= y <= current_year]

    records: list[dict] = []
    for year in sorted(years_in_window):
        records.extend(_query_layer(session, year_layers[year], watermark))

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
