# DMV Crime Pipeline

ETL pipeline ingesting public crime data for the DMV area into a unified
DuckDB warehouse. Currently covers Montgomery County MD (Socrata) and
Washington DC (ArcGIS). Built as a portfolio project demonstrating
analytics engineering: incremental extraction, raw/marts layering,
cross-jurisdiction taxonomy mapping, and idempotent loads.

## Commands

- `python run_pipeline.py` runs extract (both sources), load, transform, and site export
- `python test_pipeline_offline.py` full offline smoke test, no network needed
- `python -m extractors.moco` or `python -m extractors.dc` run one extractor
- `python -m export.export_site_data` regenerate `site/data/` without re-extracting
- `cd site && python -m http.server 8000` preview the static site locally (fetch() needs http://, not file://)

## Architecture

- `extractors/` incremental API pulls, watermarks in `state/watermarks.json`,
  raw parquet landed under `data/raw/{source}/extract_date=YYYY-MM-DD/`.
  First run (no watermark) backfills from `BACKFILL_START` (July 2016).
  The DC extractor discovers its per-year layer ids from the FeatureServer
  at runtime, falling back to `DC["fallback_year_layers"]` in config.py.
- `load/load_duckdb.py` rebuilds raw tables from parquet (padding any
  columns a sparse batch omitted), runs `sql/transform.sql`
- `sql/schema.sql` DDL for raw schema, `marts.dim_offense_map`,
  `marts.fct_incidents`, `marts.daily_counts` view
- `export/export_site_data.py` snapshots the warehouse into `site/data/`
  (gitignored, regenerated every run): `summary.json` KPIs,
  `incidents.json` incident-level last 90 days in columnar form (feeds
  the map and events search), `trends.json` daily counts by jurisdiction
  and category over the full history (feeds any trends period back to
  2016), `heatmap.json` weekday x hour last 90 days
- `site/` static HTML/CSS/JS (Leaflet + markercluster + Chart.js via CDN,
  no build step, no framework) reading `site/data/`; four pages (Map =
  index, Trends, Events, About), nav repeated per page. Dark-only
  restrained cyber theme; category colors are CVD-validated against the
  dark surface. `site/js/common.js` holds the shared friendly taxonomy
  labels/colors/formatters plus `esc()` -- API-derived strings are always
  escaped before innerHTML, and raw `offense_category` values never
  reach the UI unlabeled.
- `.github/workflows/pipeline.yml` daily cron: restore `data/raw` +
  `state/` from the Actions cache (runs are incremental; a cache miss
  triggers a full, safe re-backfill), run the pipeline, deploy `site/`
  to GitHub Pages via `actions/deploy-pages`
- Warehouse file: `data/warehouse/crime.duckdb` (gitignored)

## Conventions and invariants

- Raw means raw: the landing zone stores API responses untouched, all
  strings. All typing and cleaning happens in SQL, never in extractors.
- Everything idempotent: raw tables are full rebuilds, fct_incidents uses
  INSERT OR REPLACE on incident_key ('{jurisdiction}-{source_id}').
- Locations stay block-level as published. Failed geocodes become NULL,
  never guessed or sharpened.
- Unified taxonomy: violent, property, vehicle, drug, society, other,
  with severity_weight 1 to 10. DC maps via dim_offense_map (closed set
  of nine offenses); MoCo maps via keyword rules in transform.sql.
- No em dashes in any prose, docs, or comments.
- Requires SOCRATA_APP_TOKEN env var for reasonable MoCo rate limits.

## Roadmap (in order)

1. Migrate transform.sql to a dbt project (dbt-duckdb): staging models,
   seeds for offense mapping, schema tests
2. Priority-case scoring (severity x recency x cluster bonus) and daily digest
3. H3 hex hotspot layer on the map (7 day window)
4. Add Prince George's County and NoVA sources
5. Local news RSS matching, Census per-capita normalization

Done: GitHub Actions daily cron (`.github/workflows/pipeline.yml`), Leaflet
map with hover tooltips and incident summary cards, full-history trends
dashboard with period and granularity pickers, searchable events page,
2016+ backfill carried across runs by the Actions cache, dark themed
four-page site.
