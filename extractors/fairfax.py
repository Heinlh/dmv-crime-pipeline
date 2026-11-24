"""Fairfax County (FCPD) crime extractor.

Source: the Fairfax County Police Department's public "Crimes Against"
feature services on ArcGIS Online (the same layers behind the FCPD
Crime Mapping Dashboard). One service per NIBRS crimes-against group
(Person / Property / Society); each is updated hourly by the county.

The three services do not share a schema: Person/Property rows are
victim/offense level (a multi-victim incident repeats its
IncidentNumber) keyed on DateReported, while Society rows are event
level keyed on ReportDate with different offense-text columns. So the
extractor first reads each layer's field list, requests only the
allowlisted `fields` that layer actually has, and uses whichever
configured watermark field the layer carries. The services also publish
victim demographics and officer identifiers; those are deliberately
never requested. Point geometry is requested: the county publishes it
already offset to a block-equivalent location, and it is the only
location detail FCPD releases.

Incremental strategy: filter on the layer's watermark field (epoch
milliseconds) greater than the stored source watermark, page with
resultOffset sorted on that field then the object id (unique, so pages
never overlap or skip rows), and advance the watermark to the max value
seen across all services once the batch is landed. land_changed stores
only incidents that are new or changed. A service that fails logs and is
skipped so one group never blocks the others.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import FAIRFAX
from extractors.base import (arcgis_order, land_changed, layer_object_id, make_session,
                             read_watermark, request_json, select_fields, write_watermark)

logger = logging.getLogger("fairfax")

OVERLAP_HOURS = 24


def _layer_info(session, service: str) -> tuple[int, list[str], str] | None:
    """Return (layer_id, field_names, object_id_field) for the service's single layer.
    The layer id is not always 0 (Person uses 1), so ask the server."""
    base = f"{FAIRFAX['base_url']}/{service}/FeatureServer"
    root = request_json(session, base, params={"f": "json"})
    if "error" in root or not root.get("layers"):
        logger.warning("Service %s unavailable or empty: %s", service, root.get("error"))
        return None
    layer_id = root["layers"][0]["id"]
    layer = request_json(session, f"{base}/{layer_id}", params={"f": "json"})
    fields = [f["name"] for f in layer.get("fields", [])]
    return layer_id, fields, layer_object_id(layer)


def _query_service(session, service: str, since: datetime) -> tuple[list[dict], str | None]:
    info = _layer_info(session, service)
    if info is None:
        return [], None
    layer_id, fields, object_id = info

    watermark_field = next((f for f in FAIRFAX["watermark_fields"] if f in fields), None)
    if watermark_field is None:
        logger.warning("Service %s has none of the watermark fields %s; skipping",
                       service, FAIRFAX["watermark_fields"])
        return [], None
    # latitude/longitude are flattened from the geometry below
    out_fields = select_fields(FAIRFAX["source_name"], fields, derived=("latitude", "longitude"))

    url = f"{FAIRFAX['base_url']}/{service}/FeatureServer/{layer_id}/query"
    since_sql = since.strftime("%Y-%m-%d %H:%M:%S")
    records: list[dict] = []
    offset = 0
    while True:
        payload = request_json(session, url, params={
            "where": f"{watermark_field} > TIMESTAMP '{since_sql}'",
            "outFields": ",".join(out_fields),
            # then the object id: a unique sort, so pages never overlap or skip
            "orderByFields": arcgis_order(watermark_field, object_id),
            "returnGeometry": "true",
            "resultOffset": offset,
            "resultRecordCount": FAIRFAX["page_size"],
            "f": "json",
        })
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on {service}: {payload['error']}")
        features = payload.get("features", [])
        for feature in features:
            record = dict(feature["attributes"])
            # geometry is part of the published record; flatten the point
            # into columns so the raw parquet stays a flat table
            geometry = feature.get("geometry") or {}
            record["longitude"] = geometry.get("x")
            record["latitude"] = geometry.get("y")
            records.append(record)
        logger.info("%s: fetched offset %d (%d rows)", service, offset, len(features))
        if len(features) < FAIRFAX["page_size"]:
            break
        offset += FAIRFAX["page_size"]
    return records, watermark_field


def extract() -> tuple[list[dict], datetime | None]:
    """Return (records, the newest report time seen across services)."""
    session = make_session()
    watermark = read_watermark(FAIRFAX["source_name"]) - timedelta(hours=OVERLAP_HOURS)
    logger.info("Pulling Fairfax incidents reported after %s", watermark.isoformat())

    records: list[dict] = []
    max_millis = 0
    for service in FAIRFAX["services"]:
        try:
            service_records, watermark_field = _query_service(session, service, watermark)
            records.extend(service_records)
            for r in service_records:
                value = r.get(watermark_field)
                if value and value > max_millis:
                    max_millis = value
        except Exception:
            logger.exception("Service %s failed; continuing with the others", service)

    new_mark = datetime.fromtimestamp(max_millis / 1000, tz=timezone.utc) if max_millis else None
    return records, new_mark


def run() -> None:
    records, new_mark = extract()
    land_changed(records, FAIRFAX["source_name"])
    # only once the batch is safely landed, so a failed landing re-pulls
    if new_mark:
        write_watermark(FAIRFAX["source_name"], new_mark)


if __name__ == "__main__":
    run()
