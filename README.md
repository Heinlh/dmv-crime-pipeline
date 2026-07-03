# DMV Crime Pipeline

End to end ETL pipeline that ingests public crime data for the DMV area
(Montgomery County MD and Washington DC so far), lands it in a raw
parquet zone, and builds a unified, analysis-ready DuckDB warehouse with
a shared crime taxonomy across jurisdictions.

## Architecture

```
Socrata API (MoCo)  ─┐
                     ├─> extractors/ ─> data/raw/{source}/*.parquet ─> load/ ─> DuckDB ─> export/ ─> site/data/*.json
ArcGIS API (DC)     ─┘        (incremental, watermarked)                 (raw -> marts.fct_incidents)   (static JSON)
```

Layers in the warehouse (data/warehouse/crime.duckdb):

| Layer | Object | Purpose |
|---|---|---|
| raw | raw.moco_incidents, raw.dc_incidents | Source data as published, all VARCHAR, rebuilt from parquet |
| marts | marts.dim_offense_map | Explicit DC offense to unified category mapping with severity weights |
| marts | marts.fct_incidents | One row per incident, unified schema and taxonomy, idempotent upserts |
| marts | marts.daily_counts | Rollup view feeding dashboards and the daily digest |

Unified taxonomy: homicide/fatal violence, violent crime, sexual
offenses, property crime, vehicle-related crime, drug/alcohol/disorder,
and other/unknown, each with a 1 to 10 severity weight that feeds
priority case scoring. Display metadata for the taxonomy (plain-English
labels, colorblind-validated colors, descriptions, examples) has a
single source of truth in `site/js/common.js` (`CATEGORIES`).

`export/export_site_data.py` runs after every load and snapshots the
warehouse into `site/data/`: `summary.json` (KPIs and freshness),
`incidents.json` (incident-level detail for the last 90 days, columnar to
keep the payload small), `trends.json` (pre-aggregated daily counts by
jurisdiction and category over the full history since 2016, so the trends
page can serve any period without incident-level data in the browser), and
`heatmap.json` (weekday x hour counts). `site/` is a static, dependency-free
HTML/CSS/JS app (Leaflet + Leaflet.markercluster + Chart.js from CDN, no
build step) with a dark, restrained-cyber visual identity; the category
palette is validated for colorblind safety against the dark surface:

| Page | Shows |
|---|---|
| site/index.html | Map: freshness banner, plain-English weekly summary and KPI tiles, then the clustered incident map (hover a dot for the offense, click for the full summary card) with a live-count legend, filterable by jurisdiction, date range, category, severity |
| site/trends.html | Full-history trends with period presets (90D / 1Y / YTD / ALL) plus a custom month range (e.g. 2017-2020) and day/week/month granularity; volume line, category breakdown with prior-period deltas, day/daypart heatmap, table view per chart |
| site/events.html | Searchable incident log over the last 90 days: free-text search (offense, street, case number, district) plus jurisdiction/category/date/sort filters, rendered as summary cards with factual plain-English titles (agency label always shown) |
| site/daily.html | Daily Brief: plain-English bullets for the latest data day, category and 14-day charts, and the day's most serious incidents; powered by digest.json |
| site/alerts.html | Email signup for the daily brief, handled entirely by Buttondown (double opt-in, unsubscribe, subscriber dashboard); shows setup instructions until BUTTONDOWN_USERNAME is configured in site/js/common.js |
| site/privacy.html | Plain-English privacy policy: no first-party data collection, third-party services disclosed, email handling explained |
| site/about.html | Purpose, sources, pipeline mechanics, and honest caveats, written for a non-technical visitor |

Email digest: `export/send_digest_email.py` runs at the end of the daily
workflow and posts the digest to the Buttondown API; it is a silent no-op
until a `BUTTONDOWN_API_KEY` repository secret exists, so the email layer
is fully optional. Subscriber addresses never touch this repository or
the site; signup tracking lives in Buttondown's dashboard.

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

The first run backfills the full available history from BACKFILL_START
(config.py, July 2016, where the Montgomery County dataset begins; DC's
per-year layers are discovered from the FeatureServer automatically).
Subsequent runs are incremental: each extractor stores a per-source
watermark in state/watermarks.json and only pulls records newer than it,
minus a 24 hour overlap window to catch agency corrections. The loader
dedupes on the source incident id, so overlap never produces duplicates.

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

Runs the full land, load, transform, and export path against synthetic
API-shaped records and asserts dedupe, idempotency, geocode nulling,
taxonomy mapping, and the exported site JSON (including that old records
stay in the full-history trends but out of the 90 day incident window),
with no network required.

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

Note: `data/` and `state/` are gitignored, so the workflow carries the raw
parquet zone and the watermarks between daily runs with `actions/cache`.
On a cache hit each run pulls only the last day or so; on a miss (first
run, or cache evicted) it re-backfills the full history from
`BACKFILL_START`, which is slower but safe because every downstream step
is idempotent.

## Roadmap

- dbt project replacing sql/transform.sql (staging, seeds, tests, marts)
- Priority-case scoring (severity x recency x cluster bonus)
- H3 hex hotspot layer on the map
- Fairfax County (probe its ArcGIS schema first with
  `.github/workflows/probe.yml`); Arlington County is excluded until the
  county resumes publishing machine-readable incident data (their open
  dataset stopped in mid-2022)
- Local news RSS matching, Census per-capita normalization
