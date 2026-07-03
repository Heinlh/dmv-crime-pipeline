# DMV Crime Pipeline

ETL pipeline ingesting public crime data for the DMV area into a unified
DuckDB warehouse. Currently covers Montgomery County MD (Socrata),
Washington DC (ArcGIS), and Prince George's County MD (Socrata, updated
weekly by the county). Built as a portfolio project demonstrating
analytics engineering: incremental extraction, raw/marts layering,
cross-jurisdiction taxonomy mapping, and idempotent loads.

## Commands

- `python run_pipeline.py` runs extract (all sources), load, transform, and site export
- `python test_pipeline_offline.py` full offline smoke test, no network needed
- `python -m extractors.moco` / `.dc` / `.pgc` run one extractor
- `python -m export.export_site_data` regenerate `site/data/` without re-extracting
- `python -m export.send_digest_email` send the daily digest via Buttondown
  (silent no-op without BUTTONDOWN_API_KEY)
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
  2016), `heatmap.json` weekday x hour x jurisdiction x category last
  90 days (the site folds hours into dayparts), `digest.json` daily
  brief for the latest data day (bullets, comparisons, notable
  incidents; feeds both site/daily.html and the email digest)
- `site/` static HTML/CSS/JS (Leaflet + markercluster + Chart.js via CDN,
  no build step, no framework) reading `site/data/`; seven pages (Map =
  index, Trends, Events, Daily Brief, Alerts, About, Privacy), nav
  repeated per page. Incident titles are factual composites built only
  from published fields via `friendlyOffense`/`incidentTitle` in
  common.js; the agency's own label always stays visible on the card. Retro neon
  cyberpunk theme, dark-only: design tokens in `:root` of
  site/css/style.css (cyan = primary accent, magenta = secondary, amber
  = warnings/elevated, red only for homicide); glow reserved for
  hover/focus/active; Chakra Petch display font for headings via Google
  Fonts with system-sans fallback. Category colors are CVD-validated
  against the dark surface. `site/js/common.js` holds the shared friendly taxonomy
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
- Unified taxonomy: homicide, violent, sexual, property, vehicle,
  disorder, other, with severity_weight 1 to 10. DC maps via
  dim_offense_map (closed set of nine offenses); MoCo maps via keyword
  rules in transform.sql. Display metadata (labels, colors,
  descriptions, examples) lives ONLY in site/js/common.js CATEGORIES
  (CSS variables in site/css/style.css mirror the colors).
- No em dashes in any prose, docs, or comments.
- Requires SOCRATA_APP_TOKEN env var for reasonable MoCo/PGC rate limits.
- NO PII in this repo or the static site, ever: email signups post
  directly to Buttondown (set BUTTONDOWN_USERNAME in site/js/common.js;
  BUTTONDOWN_API_KEY repo secret enables the daily email); subscriber
  data and signup counts live only in Buttondown's dashboard.
- Incident titles must be traceable to published data fields; never
  compose narrative details the agency did not publish.
- Arlington County has no machine-readable feed since mid-2022 (excluded
  until the county resumes); Fairfax County is the next candidate, use
  .github/workflows/probe.yml to inspect its ArcGIS schema first.

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
