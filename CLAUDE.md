# DMV Crime Pipeline

ETL pipeline ingesting public crime data for the DMV area into a unified
DuckDB warehouse. Currently covers Montgomery County MD (Socrata),
Washington DC (ArcGIS), Prince George's County MD (Socrata, updated
weekly by the county), Fairfax County VA (FCPD "Crimes Against"
ArcGIS services, updated hourly by the county), and Prince William
County VA (PWC Police Public_Crime_Reports ArcGIS layer, updated daily,
rolling 3-year window). Built as a portfolio
project demonstrating analytics engineering: incremental extraction,
raw/marts layering, cross-jurisdiction taxonomy mapping, and idempotent
loads.

## Commands

- `python run_pipeline.py` runs extract (all sources), load, transform, site export, and OG card render
- `python test_pipeline_offline.py` full offline smoke test, no network needed
- `python -m extractors.moco` / `.dc` / `.pgc` / `.fairfax` / `.pwc` run one extractor
- `python -m export.export_site_data` regenerate `site/data/` without re-extracting
- `python -m export.render_og_card` re-render `site/og/daily.png` from digest.json
- `python -m export.send_digest_email` send the daily digest via Buttondown
  (silent no-op without BUTTONDOWN_API_KEY)
- `cd site && python -m http.server 8000` preview the static site locally (fetch() needs http://, not file://)

## Architecture

- `extractors/` incremental API pulls, watermarks in `state/watermarks.json`,
  raw parquet landed under `data/raw/{source}/extract_date=YYYY-MM-DD/`.
  First run (no watermark) backfills from `BACKFILL_START` (July 2016).
  The DC extractor discovers its per-year layer ids from the FeatureServer
  at runtime, falling back to `DC["fallback_year_layers"]` in config.py.
  The Fairfax extractor reads each FCPD service's field list at runtime
  (the three services have divergent schemas) and requests ONLY the
  fields in `FAIRFAX["desired_fields"]`; the services also publish
  victim demographics and officer identifiers, which are deliberately
  never pulled. Fairfax rows are victim/offense level; the transform
  dedupes to one incident per IncidentNumber keeping the most severe
  offense row. The PWC extractor watermarks on OccurredOn with a 7-day
  overlap (the layer has no report-date column and reports are filed
  late); its layer stores state-plane geometry so queries request
  outSR=4326. PWC withholds sexual offenses at the source (victim
  privacy), so that category is structurally undercounted there.
- `load/load_duckdb.py` rebuilds raw tables from parquet (padding any
  columns a sparse batch omitted), runs `sql/transform.sql`
- `sql/schema.sql` DDL for raw schema, `marts.dim_offense_map`,
  `marts.fct_incidents`, `marts.daily_counts` view
- `export/export_site_data.py` snapshots the warehouse into `site/data/`
  (gitignored, regenerated every run): `summary.json` KPIs + POPULATIONS,
  `incidents.json` incident-level last 90 days in columnar form (feeds
  the map and events search), `trends.json` daily counts by jurisdiction
  and category over the full history plus jurisdiction populations
  (feeds any trends period back to 2016 and the per-100k toggle),
  `heatmap.json` weekday x hour x jurisdiction x category last 90 days
  (the site folds hours into dayparts), `hexes.json` H3 resolution-8
  cell counts for 7 and 30 day windows with boundary polygons
  precomputed server-side (the client needs no H3 library), `digest.json`
  daily brief for the latest data day (bullets, comparisons, notable
  incidents, plus `signals`: per jurisdiction x category same-weekday
  8-week anomaly detection, baseline floor 3.0, spike >= 1.5x, lull
  <= 0.5x; feeds site/daily.html and the email digest).
  POPULATIONS are U.S. Census Bureau Vintage 2023 estimates and exist
  ONLY in export_site_data.py on the Python side (trends.json/summary.json
  carry them to the browser).
- `export/render_og_card.py` renders `site/og/daily.png` (1200x630 Open
  Graph share card) from digest.json via hand-composed SVG + cairosvg,
  no headless browser; every page's `og:image` points at it with
  absolute URLs. site/og/ is gitignored and regenerated every run.
- `site/` static HTML/CSS/JS (Leaflet + markercluster + Chart.js via CDN,
  no build step, no framework) reading `site/data/`; eight pages (Map =
  index, Trends, Events, Daily Brief, Alerts, About, Contact, Privacy),
  nav repeated per page. Incident titles are factual composites built only
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
- Cinematic layer ("cinematic but clear"): HUD corner brackets on cards
  and panels, nav scanline sweep, brand glitch on hover, blinking kicker
  cursor, KPI count-up (`countUp` in common.js). EVERY moving part is
  gated behind `prefers-reduced-motion: no-preference`; reduced-motion
  users get the identical static design and the map playback's autoplay
  button is hidden entirely (manual stepping still works).
- Map extras: `hotspotGroup` hex layer (Off/7d/30d select, default 30d,
  single-hue cyan fill under the dots, tooltips with count + top
  category, explicitly framed as "where reports cluster", never
  predictive), and a day-by-day playback scrubber over the last 30 days
  anchored to the newest incident.
- Map search (`mapSearch` in home.js): a floating CASE NUMBER lookup
  over the top-left of the map (zoom control moved to top-right).
  Typing 3+ characters suggests matching cases from the loaded
  incidents (no third-party geocoder; nothing typed leaves the
  browser); selecting one shows exactly that incident, ignoring the
  dropdown filters (a case number identifies one incident, so range or
  category filters must not hide it), fits the map, and opens the
  summary card via markercluster's zoomToShowLayer. The term rides in
  the Map hash as `q`. Free-text place/offense search deliberately
  lives ONLY on the Events page; on the map it fought the filters and
  was removed. Leaflet event propagation is disabled on the overlay so
  interacting with it never pans the map.
- Shareable URL state: filter state lives in the location hash on Map
  (j/days/cat/sev/hex/day), Trends (preset/from/to/gran/j/cat/rate), and
  Events (q/j/cat/days/sort) via `readHashState`/`writeHashState` in
  common.js. Ctrl+K / Cmd+K command palette (vanilla JS in common.js)
  jumps between pages, applies canned filters, and forwards free text to
  the Events search via `events.html#q=`.
- Trends per-capita: COUNTS / PER 100K toggle divides by jurisdiction
  populations; combined figures use the union region's summed
  population, never summed rates. `ACTIVE_JURISDICTIONS` narrows to
  jurisdictions that actually have data so a newly added source cannot
  draw a phantom zero series or dilute denominators.
- PWA: `site/manifest.webmanifest` + `site/sw.js` (network-first,
  cache fallback, same-origin GETs only) + `site/icons/` (committed,
  generated from the DMV mark). All paths relative so the scope works
  under GitHub Pages project hosting.
- `.github/workflows/pipeline.yml` daily cron: restore `data/raw` +
  `state/` from the Actions cache (runs are incremental; a cache miss
  triggers a full, safe re-backfill), run the pipeline, deploy `site/`
  to GitHub Pages via `actions/deploy-pages`
- `.github/workflows/probe.yml` workflow_dispatch curl probe, used to
  reconnoiter external API schemas from CI (this is how the FCPD
  services and their layer ids/fields were discovered)
- Warehouse file: `data/warehouse/crime.duckdb` (gitignored)

## Conventions and invariants

- Raw means raw: the landing zone stores API responses untouched, all
  strings. All typing and cleaning happens in SQL, never in extractors.
  (Field *selection* at the API level is allowed and used for Fairfax to
  avoid ever landing victim demographics.)
- Everything idempotent: raw tables are full rebuilds, fct_incidents uses
  INSERT OR REPLACE on incident_key ('{jurisdiction}-{source_id}').
- Locations stay block-level as published. Failed geocodes become NULL,
  never guessed or sharpened. Hex hotspot cells (~0.7 km2) are coarser
  than block level by construction. FCPD publishes no street addresses,
  so Fairfax incidents show their police district as area_name.
- Unified taxonomy: homicide, violent, sexual, property, vehicle,
  disorder, other, with severity_weight 1 to 10. DC maps via
  dim_offense_map (closed set of nine offenses); MoCo, PGC, and Fairfax
  map via keyword rules in transform.sql. Display metadata (labels,
  colors, descriptions, examples) lives ONLY in site/js/common.js
  CATEGORIES (CSS variables in site/css/style.css mirror the colors).
- Anomaly signals are statistics, not judgments: same-weekday 8-week
  baseline, floor of 3.0 so thin slices (e.g. one homicide vs a near-zero
  average) never masquerade as spikes; always presented with the
  baseline number and "one day is noise" framing.
- No em dashes in any prose, docs, or comments.
- Requires SOCRATA_APP_TOKEN env var for reasonable MoCo/PGC rate limits.
- NO PII in this repo or the static site, ever: email signups post
  directly to Buttondown (set BUTTONDOWN_USERNAME in site/js/common.js;
  BUTTONDOWN_API_KEY repo secret enables the daily email); subscriber
  data and signup counts live only in Buttondown's dashboard. Fairfax
  victim demographics and officer identifiers are never requested from
  the FCPD services.
- Incident titles must be traceable to published data fields; never
  compose narrative details the agency did not publish.
- Nothing on the site is predictive: hotspots and signals describe
  published reports, and the copy says so explicitly.
- Security posture: all API-derived strings are escaped via `esc()`
  before innerHTML; all SQL timestamp bounds are laundered through Python
  `datetime` objects before string interpolation (never raw API strings);
  extractors select fields at the API level but never eval/shell/pickle;
  secrets come from env only. Every page ships a Content-Security-Policy
  meta tag (default-src 'self'; scripts/styles limited to self + the
  pinned CDNs; img to self/data/carto; connect 'self'; form-action to
  Buttondown). The map search is deliberately geocoder-free so no query
  leaves the browser. Clickjacking protection (frame-ancestors) needs an
  HTTP header GitHub Pages cannot set; CDN scripts could additionally use
  Subresource Integrity if the project ever pins exact hashes.
- DMV coverage audit (July 2026): Arlington County resumed publishing in
  2024 via its Crime Data Hub (weekly, data since 2021, backend at
  datahub-v2.arlingtonva.us; dataset endpoint discovery still pending,
  integration is a fast-follow). Loudoun County and the City of
  Alexandria publish dashboards/CityProtect only, no feed. Frederick,
  Charles, Howard, and Anne Arundel MD publish only annual summaries or
  PDFs. The About page documents all of this for visitors.

## Roadmap (in order)

1. Migrate transform.sql to a dbt project following docs/dbt-migration.md
   (step-by-step guide: dbt-duckdb profile, sources, staging models,
   offense-map seed, marts, schema tests, CI wiring, parity validation)
2. Priority-case scoring (severity x recency x cluster bonus)
3. Local news RSS matching
4. Azure migration when GitHub Actions free minutes get tight ($100
   credit available; containerize the pipeline, keep Pages or move to
   Static Web Apps)

Done: GitHub Actions daily cron (`.github/workflows/pipeline.yml`), Leaflet
map with hover tooltips and incident summary cards, full-history trends
dashboard with period and granularity pickers, searchable events page,
2016+ backfill carried across runs by the Actions cache, dark neon
cyberpunk theme, PG County + Fairfax County sources, Daily Brief page +
Buttondown email digest with designed HTML newsletter, factual incident
titles, privacy policy + contact pages, H3 hex hotspot layer (7d/30d),
Census per-capita toggle, anomaly signals in the digest/brief/newsletter,
day-by-day map playback, Ctrl+K command palette, shareable URL state,
Python-rendered daily OG share card, PWA shell, cinematic-but-clear
theme pass (all motion behind prefers-reduced-motion).
