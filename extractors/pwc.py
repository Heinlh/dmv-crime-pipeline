"""Prince William County (PWC Police) crime extractor.

Source: the county's "Public_Crime_Reports" hosted feature layer on
ArcGIS Online, the data behind PWC's public Crime Data Explorer.
Updated once daily by the county; covers a rolling 3-year window.
Locations are mapped to the nearest 100 block by the county, and the
county withholds sexual offense records entirely for victim privacy.

Refresh strategy: the layer has no report-date column and no per-row edit
tracking, only a layer-wide editingInfo.dataLastEditDate. So whenever
that moves, the extractor re-pulls the whole layer, which its 3-year
window keeps to a handful of pages, and land_changed stores only the
incidents that are new or differ from their latest stored version.
Incidents that roll out of the county's window are simply not fetched
again, so their last stored version stays on the data branch.

Paging sorts on the layer's object id, unique per row, so pages can
neither overlap nor skip rows. The previous occurrence-time watermark,
paged on the non-unique OccurredOn, held 1,692 of the 3,203 rows the
county published for October 2026. The layer stores state-plane
geometry, so queries request outSR=4326 to land plain lat/lng.
"""

import logging

from config import PWC_VA
from extractors.base import (arcgis_layer, arcgis_order, land_changed, layer_fields,
                             layer_object_id, make_session, read_state, request_json,
                             select_fields, write_state)

logger = logging.getLogger("pwc")

STATE_KEY = "pwc:data_last_edit"


def extract() -> tuple[list[dict], int | None]:
    """Return (records, the layer's dataLastEditDate)."""
    session = make_session()
    layer_url = f"{PWC_VA['base_url']}/{PWC_VA['layer_id']}"
    layer = arcgis_layer(session, layer_url)
    edited = (layer.get("editingInfo") or {}).get("dataLastEditDate")
    prior = read_state(STATE_KEY)
    if prior is not None and edited is not None and edited == prior:
        logger.info("Layer unchanged since dataLastEditDate %s; nothing to fetch", prior)
        return [], edited

    # latitude/longitude are flattened from the geometry below
    fields = select_fields(PWC_VA["source_name"], layer_fields(layer),
                           derived=("latitude", "longitude"))
    order = arcgis_order(None, layer_object_id(layer))
    records: list[dict] = []
    offset = 0
    while True:
        payload = request_json(session, f"{layer_url}/query", params={
            "where": "1=1",
            "outFields": ",".join(fields),
            "orderByFields": order,
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
            return records, edited
        offset += PWC_VA["page_size"]


def run() -> None:
    records, edited = extract()
    land_changed(records, PWC_VA["source_name"])
    if edited is not None:
        write_state(STATE_KEY, edited)


if __name__ == "__main__":
    run()
