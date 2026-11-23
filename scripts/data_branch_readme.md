# DMV Crime Watch: raw data

This branch is the pipeline's system of record: every batch of incident
records it has fetched from the five agencies, plus the watermarks that
say where each source's next fetch starts. It is written only by the
daily workflow on `main` (`.github/workflows/pipeline.yml`). Do not commit
to it by hand.

Everything on the site, and the DuckDB warehouse behind it, is rebuilt
from this branch on every run.

## Layout

```
raw/{source}/extract_date=YYYY-MM-DD/{source}_{YYYY-MM-DD}_{HHMMSS}.parquet
state/watermarks.json
```

Sources: `dc` (MPD), `moco` (Montgomery County), `pgc` (Prince George's
County), `fairfax` (FCPD), `pwc` (Prince William County).

Each parquet file is one extraction batch, stored exactly as the agency
returned it apart from the two rules below, with every value as text.
The `_extracted_at` column records when the batch was fetched (UTC), so
when an agency corrects a record the newer version can be told from the
older one. A record can therefore appear in several files.

## What is deliberately not here

The pipeline keeps only the fields listed for each source in `config.py`
(`fields`) on `main`. Agencies publish more than that, including house
numbers, geocoded street addresses, victim demographics, and officer
identifiers, and none of it is fetched or stored.

Locations are block level, never sharper. Prince George's County
publishes exact house addresses with coordinates geocoded to them, so for
that source the house number is rounded down to its block (`9759 GOOD
LUCK RD` becomes `block_address` `9700 BLOCK GOOD LUCK RD`), the exact
address is never stored, and coordinates are rounded to 3 decimal places
(about one block).

## Using it

```
git clone --branch data --single-branch https://github.com/Heinlh/dmv-crime-pipeline.git dmv-data
```

The files read directly in DuckDB, pandas, or anything else that reads
parquet. To rebuild the warehouse and site from them, check out `main`
and point the pipeline at the clone:

```
DMV_RAW_DIR=dmv-data/raw DMV_STATE_PATH=dmv-data/state/watermarks.json python run_pipeline.py
```

These are public records as published by each agency, and preliminary:
agencies correct and reclassify records after the fact.
