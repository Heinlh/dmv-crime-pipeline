# DMV Crime Pipeline

End to end ETL pipeline that ingests public crime data for the DMV area
(Washington DC, Montgomery County MD, Prince George's County MD,
Fairfax County VA, and Prince William County VA), lands it in a raw
parquet zone, and builds a
unified, analysis-ready DuckDB warehouse with a shared crime taxonomy
across jurisdictions.

## Architecture

```
5 agency APIs ─> extractors/ ─> raw/{source}/*.parquet ─> load/ ─> DuckDB ─> export/ ─> site/data/*.json ─> GitHub Pages
 (Socrata,       (allowlisted     on the `data` branch        (raw -> marts.fct_incidents)    (static JSON)
  ArcGIS)         fields only)    (the system of record)
```

## Where the data lives

| What | Where | Lifetime |
|---|---|---|
| Raw parquet zone + watermarks | the repo's `data` branch | permanent, versioned |
| DuckDB warehouse | built in CI from the `data` branch | one run |
| `site/data/*.json` | exported in CI, deployed to Pages | until the next deploy |

The `data` branch is the pipeline's only durable state. Each run checks it
out beside `main` as a worktree (`scripts/datastore.sh open`), points the
pipeline at it through `DMV_RAW_DIR` and `DMV_STATE_PATH`, and pushes the
new batches back (`scripts/datastore.sh save`, a no-op on a quiet day).
Everything downstream is rebuilt from it on every run, so a transform fix
applies to the whole history at once, and any past day's raw data is a
commit away. Each parquet file is one extraction batch, and every row
carries `_extracted_at`, when it was fetched, so an agency's correction can
be told from the version it replaces. The branch has its own README
(`scripts/data_branch_readme.md`).

The raw zone is public, so it holds only each source's allowlisted
`fields` (`config.py`): the columns the transform reads, plus record
identifiers. Agencies publish more, including house numbers, geocoded street
addresses, victim demographics and officer IDs, and none of it is requested
or landed; `land_raw` enforces the allowlist whatever an extractor fetched.
Prince George's County publishes exact house addresses with coordinates
geocoded to them, so before landing, `extractors/privacy.py` rounds the
house number down to its block and coordinates to 3 decimal places.

Before the `data` branch existed, the raw zone lived only in the Actions
cache. The first run that finds no `data` branch restores that cache and
seeds the branch from it (`scripts/migrate_raw_zone.py`), applying the
same allowlists and coarsening to the history.

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
Subsequent runs pull only what changed, by a per-source strategy (next
section), and record their state in state/watermarks.json.

## How each source refreshes

Agencies publish reports days or weeks after the offense, and correct
them later. A watermark on when the offense occurred never asks about an
old offense again once newer ones have been seen, so it missed both: in
September 2026 the warehouse held 1,833 of the 3,629 rows Montgomery
County published, and in October 2026, 1,692 of Prince William's 3,203.
Each extractor therefore tracks what the agency changed, not when the
offense happened:

| Source | Asks the agency for | State key |
|---|---|---|
| Montgomery | rows whose Socrata `:updated_at` is newer than the last run (less a day), then every row of each incident touched | `moco:updated_at` |
| Prince George's | the whole current dataset, only when its `rowsUpdatedAt` has moved (the county republishes weekly, all at once) | `pgc:rows_updated_at` |
| Prince William | the whole layer (a rolling 3-year window), only when its `dataLastEditDate` has moved | `pwc:data_last_edit` |
| DC | rows whose `REPORT_DAT` is newer than the last run (less a day); a late report carries a late `REPORT_DAT` | `dc` |
| Fairfax | rows whose report date is newer than the last run (less a day), per service | `fairfax` |

Every paged query sorts on a unique key last (Socrata `:id`, the ArcGIS
object id). Sorting on a column with ties, as the old queries did, lets a
server return tied rows in a different order on each page request, so
offset paging repeats some rows and skips others.

Re-pulling more than is new costs nothing in storage: `land_changed`
compares each incident with its latest stored version and lands only
incidents that are new or differ, so a quiet day adds no files. State is
written only after the batch is landed, so a failed run re-pulls.

## Which version the warehouse shows

An incident's latest extraction is its current state: the transform
keeps, per incident, only the rows from the most recent `_extracted_at`,
so a reclassification replaces the original even when it is less severe,
and a row the agency withdrew drops out. Among those rows, the most severe
offense represents the incident, and remaining ties break on a hash of the
row's content, so rebuilding twice, or landing the same rows in a
different order, gives identical marts.

Fairfax is the exception. An incident's rows come from up to three
services that refresh independently, and the county regenerates
`UniqueID` between pulls, so no single extraction holds a Fairfax incident
whole. Fairfax versions by row instead: every distinct row ever seen
(ignoring `UniqueID`) counts, and the most severe represents the incident.

## History bounds

The site's history starts on 1 January 2016 (`HISTORY_START` in
`export/export_site_data.py`). Records dated before it, in the future, or
not at all (DC carries offenses dated as early as 1940) stay in the
warehouse but are left out of the totals, the trends and the front page's
"warehouse starts" date, and are counted in `summary.json` as
`records_outside_history`.

## Design decisions

1. Raw means raw, within the allowlist. The landing zone stores the
   allowlisted fields exactly as the APIs return them, as strings. All
   typing, renaming, and cleaning happens in SQL, so the pipeline never
   breaks on a source-side type quirk and the warehouse can always be
   rebuilt from parquet. The one exception is location detail finer than
   a block, which is coarsened before landing because the raw zone is
   public.
2. Idempotent everywhere. Raw tables are full rebuilds from the parquet
   zone; the fact table uses INSERT OR REPLACE on a globally unique
   incident_key. Re-running any step is always safe.
3. Category mapping is transparent. DC publishes a closed set of nine
   offenses, mapped in dim_offense_map as data. Montgomery County uses
   NIBRS categories with dozens of values, mapped by documented keyword
   rules in sql/transform.sql. Both migrate directly to dbt seeds and
   models in the next phase.
4. Locations stay block-level, never sharper. Where an agency publishes
   something finer (Prince George's County's exact house addresses), it
   is coarsened to the block at landing. Failed geocodes (DC reports these
   as 0,0) are stored as NULL, never guessed.

## Data notes

- MoCo: dataMontgomery Crime dataset icn6-v9z3, founded crimes since
  July 2016, UCR/NIBRS classified, preliminary reports subject to change.
- DC: MPD Crime Incidents feature layers (one per year, configured in
  config.py). REPORT_DAT drives incrementality; coordinates are
  anonymized to the block. A few records carry offense dates decades
  back (1940); they fall outside the history bounds above.
- Both agencies publish with a lag of one or more days. Dashboards
  should say "as of last published data", not "live".

## Testing

```
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
python -m pytest
```

Four tiers under `tests/`, run on every pull request by
`.github/workflows/test.yml`. None of them touch the network, apart from
the browser tier loading the site's CDN libraries as a visitor would.

| Tier | Covers |
|---|---|
| `unit` | field allowlists, block-level coarsening, landing, extraction stamps, path overrides, change detection (what `land_changed` stores), unique paging sorts |
| `integration` | every real extractor against faked agency APIs (`tests/fakes.py`), late reports and corrections being picked up, unchanged datasets not re-fetched, tied rows paging exactly once, state not advancing when landing fails, which version the transform shows, the history bounds, the loader and transform on what they land, the raw-zone migration, and `datastore.sh` against a throwaway git remote |
| `e2e` | `run_pipeline.main()` over several simulated days with all state carried by a `data` branch, late and corrected records published between days, a rebuild from the branch alone with every agency down, and the first-run migration |
| `browser` | the built site, from two simulated days, in Chromium through Playwright: map, case lookup, events search, late records and a correction as a visitor sees them, the front page's history start, and that no exact address reaches a visitor |

The faked agencies publish the same extra fields the real ones do (house
numbers, victim demographics), so the tests prove those are never
requested or landed. They also keep `:updated_at`, `rowsUpdatedAt` and
`dataLastEditDate` as the real services do, and return rows tied in a
query's sort in a different order on each request, which is how the real
servers lost rows. Behind a proxy or offline, `TEST_VENDOR_DIR` points
the browser tier at a local `node_modules/` holding Leaflet,
Leaflet.markercluster and Chart.js, and `PW_CHROMIUM_EXECUTABLE` at a
specific Chromium build.

The original standalone script still runs on its own (and as part of the
`e2e` tier):

```
python test_pipeline_offline.py
```

It runs the full land, load, transform, and export path against synthetic
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

The workflow needs `contents: write` to push the `data` branch. That
token can also push to `main`, so a branch protection rule (or ruleset)
on `main` requiring pull requests is recommended. A fresh fork with no
`data` branch and no legacy cache backfills the full history from
`BACKFILL_START` on its first run, which is slow but safe.

## Roadmap

- dbt project replacing sql/transform.sql: step-by-step migration guide
  in [docs/dbt-migration.md](docs/dbt-migration.md)
- Priority-case scoring (severity x recency x cluster bonus)
- Local news RSS matching
- Arlington County (its 2024 Crime Data Hub publishes weekly bulk data;
  endpoint recon in progress). Loudoun, Alexandria, Frederick, Charles,
  Howard, and Anne Arundel publish no incident-level feeds and are
  documented as excluded on the About page
