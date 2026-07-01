# DMV Crime Pipeline

End to end ETL pipeline that ingests public crime data for the DMV area
(Montgomery County MD and Washington DC so far), lands it in a raw
parquet zone, and builds a unified, analysis-ready DuckDB warehouse with
a shared crime taxonomy across jurisdictions.

## Architecture

```
Socrata API (MoCo)  ─┐
                     ├─> extractors/ ─> data/raw/{source}/*.parquet ─> load/ ─> DuckDB ─> export/ ─> site/data/*.json
ArcGIS API (DC)     ─┘        (incremental, watermarked)                 (raw -> marts.fct_incidents)   (static JSON/GeoJSON)
```

Layers in the warehouse (data/warehouse/crime.duckdb):

| Layer | Object | Purpose |
|---|---|---|
| raw | raw.moco_incidents, raw.dc_incidents | Source data as published, all VARCHAR, rebuilt from parquet |
| marts | marts.dim_offense_map | Explicit DC offense to unified category mapping with severity weights |
| marts | marts.fct_incidents | One row per incident, unified schema and taxonomy, idempotent upserts |
| marts | marts.daily_counts | Rollup view feeding dashboards and the daily digest |

Unified taxonomy: violent, property, vehicle, drug, society, other, each
with a 1 to 10 severity weight that feeds priority case scoring.

`export/export_site_data.py` runs after every load and snapshots the
warehouse into `site/data/` (summary stats, a 30 day incidents GeoJSON, and
a few pre-aggregated trend JSON files). `site/` is a static, dependency-free
HTML/CSS/JS app (Leaflet + Leaflet.markercluster + Chart.js from CDN, no
build step) that reads those files directly:

| Page | Shows |
|---|---|
| site/index.html | Landing page: freshness banner, plain-English weekly summary and KPI cards, then the clustered incident map with a legend, filterable by jurisdiction, date range, category, severity |
| site/trends.html | Daily volume, category breakdown with week-over-week deltas, jurisdiction comparison, day/hour heatmap, each with a plain-language caption |
| site/about.html | This architecture, written for a non-technical visitor |

`site/js/common.js` holds the shared friendly-label taxonomy (`CATEGORY_LABELS`,
`CATEGORY_DESCRIPTIONS`), colors, and formatters used across pages, so raw
taxonomy values (`offense_category`, NIBRS codes, etc.) never reach the UI
directly.

## Setup

```
pip install -r requirements.txt
export SOCRATA_APP_TOKEN= your_token  # optional but recommended, free at
                                      # data.montgomerycountymd.gov developer settings
python run_pipeline.py
```

The first run backfills INITIAL_LOOKBACK_DAYS (config.py, default 90).
Widen it for a deeper backfill, then subsequent runs are incremental:
each extractor stores a per-source watermark in state/watermarks.json
and only pulls records newer than it, minus a 24 hour overlap window to
catch agency corrections. The loader dedupes on the source incident id,
so overlap never produces duplicates.

## Design decisions

1. Raw means raw. The landing zone stores exactly what the APIs return,
   as strings. All typing, renaming, and cleaning happens in SQL, so the
   pipeline never breaks on a source-side type quirk and the warehouse
   can always be rebuilt from parquet.
2. Idempotent everywhere. Raw tables are full rebuilds from the parquet
   zone; the fact table uses INSERT OR REPLACE on a globally unique
   incident_key. Re-running any step is always safe.
3. Category mapping is transparent. DC publishes a closed set of nine
   offenses, mapped in dim_offense_map as data. Montgomery County uses
   NIBRS categories with dozens of values, mapped by documented keyword
   rules in sql/transform.sql. Both migrate directly to dbt seeds and
   models in the next phase.
4. Locations stay block-level as published. Failed geocodes (DC reports
   these as 0,0) are stored as NULL, never guessed.

## Data notes

- MoCo: dataMontgomery Crime dataset icn6-v9z3, founded crimes since
  July 2016, UCR/NIBRS classified, preliminary reports subject to change.
- DC: MPD Crime Incidents feature layers (one per year, configured in
  config.py). REPORT_DAT drives incrementality; coordinates are
  anonymized to the block.
- Both agencies publish with a lag of one or more days. Dashboards
  should say "as of last published data", not "live".

## Testing

```
python test_pipeline_offline.py
```

Runs the full land, load, and transform path against synthetic
API-shaped records and asserts dedupe, idempotency, geocode nulling,
and taxonomy mapping, with no network required.

## Viewing the site locally

`run_pipeline.py` regenerates `site/data/` on every run. Since the pages
`fetch()` those files, open them through a local server rather than as
`file://` (browsers block that fetch under `file://`):

```
cd site
python -m http.server 8000
```

Then visit http://localhost:8000.

## Deploying

The included `.github/workflows/pipeline.yml` runs the pipeline daily and
publishes `site/` to GitHub Pages. One-time setup on GitHub (not something
this repo can do for you):

1. Create the GitHub repo and push this project to it.
2. Repo Settings -> Secrets and variables -> Actions -> add
   `SOCRATA_APP_TOKEN`.
3. Repo Settings -> Pages -> Source -> "GitHub Actions".
4. Run the workflow once manually (Actions tab -> Daily crime data pipeline
   -> Run workflow) to confirm it deploys, then let the daily schedule take
   over.

Note: `data/` and `state/` are gitignored, so every Actions run starts from
a clean checkout and re-pulls the last `INITIAL_LOOKBACK_DAYS` window rather
than truly incrementing from the prior run's watermark. The transform is
fully idempotent, so this is safe, just less efficient than a local run
with a persisted `state/watermarks.json`.

## Roadmap

- dbt project replacing sql/transform.sql (staging, seeds, tests, marts)
- Priority-case scoring (severity x recency x cluster bonus) and a daily
  digest, published as a dated page and/or emailed
- H3 hex hotspot layer on the map
- Additional jurisdictions: Prince George's County, NoVA
- Local news RSS matching, Census per-capita normalization
