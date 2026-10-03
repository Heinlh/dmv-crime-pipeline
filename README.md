# DMV Crime Pipeline

End to end ETL pipeline that ingests public crime data for the DMV area
(Washington DC, Montgomery County MD, Prince George's County MD,
Fairfax County VA, and Prince William County VA), lands it in a raw
parquet zone, and builds a
unified, analysis-ready DuckDB warehouse with a shared crime taxonomy
across jurisdictions.

## Architecture

```
Socrata API (MoCo)  ─┐
                     ├─> extractors/ ─> data/raw/{source}/*.parquet ─> load/ ─> DuckDB ─> export/ ─> site/data/*.json
ArcGIS API (DC)     ─┘        (incremental, watermarked)                 (raw -> marts.fct_incidents)   (static JSON)
```

Layers in the warehouse (data/warehouse/crime.duckdb):

| Layer | Object | Purpose |
|---|---|---|
| raw | raw.moco_incidents, raw.dc_incidents, raw.pgc_incidents, raw.fairfax_incidents, raw.pwc_incidents | Source data as published, all VARCHAR, rebuilt from parquet |
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
page can serve any period without incident-level data in the browser, plus
jurisdiction populations for the per-100k toggle), `heatmap.json` (weekday
x hour counts), `hexes.json` (H3 hex-cell counts over 7 and 30 day windows
with boundary polygons precomputed so the browser needs no H3 library),
`aoristic.json` and `nowcast.json` (the two time corrections described
below), and `digest.json` (the daily brief, including FDR-corrected
anomaly signals and the detector's own null calibration).
`export/render_og_card.py` then renders a daily 1200x630 Open Graph share
card as a PNG, in Python via cairosvg, no headless browser. `site/` is a static, dependency-free
HTML/CSS/JS app (Leaflet + Leaflet.markercluster + Chart.js from CDN, no
build step) with a dark, restrained-cyber visual identity; the category
palette is validated for colorblind safety against the dark surface:

| Page | Shows |
|---|---|
| site/index.html | Map: freshness banner, plain-English weekly summary and KPI tiles, then the clustered incident map (hover a dot for the offense, click for the full summary card) with a live-count legend, filterable by jurisdiction, date range, category, severity; a Google-Maps-style search box (autocomplete over crime types and places, built from the loaded data so no geocoder is called); H3 hotspot shading (off / 7d / 30d) and a day-by-day playback scrubber over the last 30 days |
| site/trends.html | Full-history trends with period presets (90D / 1Y / YTD / ALL) plus a custom month range (e.g. 2017-2020) and day/week/month granularity; COUNTS / PER 100K toggle (Census Vintage 2023 populations); volume line, category breakdown with prior-period deltas, day/daypart heatmap, table view per chart |
| site/events.html | Searchable incident log over the last 90 days: free-text search (offense, street, case number, district) plus jurisdiction/category/date/sort filters, rendered as summary cards with factual plain-English titles (agency label always shown) |
| site/daily.html | Daily Brief: plain-English bullets for the latest data day, anomaly signals (each neighborhood x category vs its own 8-week same-weekday baseline, corrected for multiple comparisons), category and 14-day charts, and the day's most serious incidents; powered by digest.json |
| site/methodology.html | The three statistical corrections, each with the measured effect from the current run: aoristic weighting vs the naive start-time chart, the reporting-delay nowcast with its uncertainty band, and the signal detector's false-alarm calibration. Every number on the page comes from a payload, none are hardcoded |
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

## Statistical corrections

`export/analysis.py` sits between the warehouse and the site and applies
three corrections for ways published crime data misleads when aggregated
naively. All three are computed at build time from whatever the warehouse
holds, so the figures on the Methodology page are measured on the current
run rather than asserted. The module is deliberately dependency-free
(`math` only, no scipy); every distribution used is small enough that
direct summation is exact.

Not every source supports every correction: aoristic weighting needs
published offense end times, nowcasting needs published report dates. The
applicable jurisdictions are derived from the data itself, so a new source
enrolls automatically the day it starts publishing the column, and each
payload carries its own scope so the site can say exactly what was covered
instead of implying the whole region was.

1. **Aoristic weighting** (`aoristic()`). Hour-of-day charts are almost
   always built from the offense start time. For offenses discovered later
   (burglary, theft from a vehicle) that start time is really "when the
   victim was last there", so the conventional chart measures victim
   schedules rather than offense timing. Each incident's single unit of
   probability is instead spread uniformly across its published start-to-end
   window; a window of 24 hours or more carries no hour-of-day information
   at all and contributes exactly 1/24 to every hour. The payload carries
   both curves and the total variation distance between them, per category,
   so the size of the distortion is visible rather than claimed.
2. **Reporting-delay nowcasting** (`nowcast()`). Reports arrive days after
   the offense, so the newest days of any occurrence-date series are
   structurally undercounted and the right edge of every trend line slopes
   down for entirely artificial reasons. The empirical delay CDF is fitted
   on mature days only (older than `MATURITY_DAYS`, since younger days are
   themselves still filling in), and recent days are rescaled by their
   expected completeness `F(a)`. Intervals come from binomial thinning.
   Below `MIN_COMPLETENESS` the correction amplifies noise faster than it
   removes bias, so those days are published as observed-only and labeled
   as such rather than extrapolated.
3. **FDR-controlled signal detection** (`detect_signals()`,
   `calibrate_detector()`). The Daily Brief compares hundreds of
   (area x category) series against their own 8-week same-weekday baselines
   every day. At that many tests an uncorrected 5% threshold produces
   alarming-looking alerts daily from pure chance. Per-series Poisson tail
   probabilities are corrected with Benjamini-Hochberg at `FDR_Q = 0.10`.
   Because real alerts have no ground truth, the honest backtest is null
   calibration: `calibrate_detector()` redraws every series from its own
   fitted baseline (so no real spike exists by construction) and reports the
   realized false-alarm rate for both the naive and corrected thresholds.
   Those measured rates are published on the Methodology page, and the
   digest carries how many signals an uncorrected threshold would have
   reported alongside how many survived.

What is deliberately *not* corrected is stated on the page too: reporting
rates (crimes never reported to police are invisible to every source here),
differences in how agencies classify the same conduct, and any form of
prediction. The corrections make the descriptive record less misleading;
they do not turn it into a forecast.

## Security and privacy posture

The site renders public agency data with no accounts, cookies, or
first-party analytics, so the attack surface is small; the code still
holds the line defensively:

- Every API-derived string is HTML-escaped before it reaches `innerHTML`,
  and raw taxonomy codes never render unlabeled.
- All SQL timestamp bounds are laundered through Python `datetime` objects
  before interpolation, so a malformed value from an upstream API cannot
  become SQL. Extractors never use `eval`, a shell, or `pickle`, and TLS
  verification is never disabled.
- Secrets (`SOCRATA_APP_TOKEN`, `BUTTONDOWN_API_KEY`) come from the
  environment only and never touch the repo or the site.
- Every page sends a `Content-Security-Policy` meta tag: `default-src
  'self'`, scripts and styles limited to self plus the pinned CDNs, images
  to self/`data:`/the map-tile host, `connect-src 'self'`, and
  `form-action` limited to the Buttondown signup endpoint.
- The map search is deliberately geocoder-free: suggestions are built from
  the already-loaded incidents, so nothing a visitor types is sent
  anywhere.

Two items need infrastructure a static GitHub Pages host cannot provide:
clickjacking protection (`frame-ancestors`, an HTTP header) and
Subresource Integrity on the CDN scripts (self-hosting the libraries is
the alternative). Both are documented rather than silently skipped.

Every page shares a Ctrl+K / Cmd+K command palette and shareable URLs:
map, trends, and events filters live in the location hash, so any view
can be copied and sent. The site is also an installable PWA (manifest +
network-first service worker) with an offline fallback to the last data
seen. All cinematic motion (nav scanline, KPI count-up, brand glitch,
playback autoplay) is disabled under prefers-reduced-motion.

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

The statistical corrections are tested against hand-computable references
rather than golden files: a purpose-built four-incident warehouse whose
aoristic mass placement can be worked out on paper (and must still sum to
exactly the incident count), a delay distribution rigged so a day observed
at 50% completeness must nowcast to precisely twice its observed count,
Poisson tail probabilities checked to 1e-9 against values re-derived from
the series definition, a Benjamini-Hochberg case with a known rejection
set, and 1000 simulated null days that must produce zero rejections.

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

- dbt project replacing sql/transform.sql: step-by-step migration guide
  in [docs/dbt-migration.md](docs/dbt-migration.md)
- Priority-case scoring (severity x recency x cluster bonus)
- Local news RSS matching
- Arlington County (its 2024 Crime Data Hub publishes weekly bulk data;
  endpoint recon in progress). Loudoun, Alexandria, Frederick, Charles,
  Howard, and Anne Arundel publish no incident-level feeds and are
  documented as excluded on the About page
