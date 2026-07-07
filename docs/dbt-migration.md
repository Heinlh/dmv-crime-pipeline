# Migrating the transform layer to dbt (dbt-duckdb)

A step-by-step guide for moving `sql/schema.sql` + `sql/transform.sql`
into a dbt project, while keeping the extractors, the loader's raw
tables, and the site export exactly as they are. The end state: dbt owns
everything between the raw schema and `site/data/`, with schema tests
running on every pipeline run.

Why bother: dbt gives this project versioned, testable, self-documenting
SQL. Each transformation becomes a model with lineage; the offense
mapping becomes a seed (a CSV that is diffable in PRs); assumptions like
"incident_key is unique" become tests that fail the build instead of
silently corrupting the site.

## Current state (what is being replaced)

- `load/load_duckdb.py` rebuilds `raw.moco_crime`, `raw.dc_crime`,
  `raw.pgc_crime` from parquet, then executes `sql/schema.sql` and
  `sql/transform.sql` as plain SQL scripts.
- `sql/schema.sql` creates `marts.dim_offense_map` (a closed set of nine
  DC offenses) and `marts.fct_incidents`, plus the `marts.daily_counts`
  view.
- `sql/transform.sql` does per-source SELECTs (typing, dedupe with
  ROW_NUMBER, taxonomy mapping) and INSERT OR REPLACE into
  `marts.fct_incidents`.

Only the schema/transform half moves to dbt. Raw loading stays in
Python: dbt is a transform tool, and the "raw means raw" invariant is
already enforced upstream.

## Step 1: Install and scaffold

```bash
pip install dbt-duckdb        # add dbt-duckdb>=1.8 to requirements.txt
mkdir dbt && cd dbt
dbt init dmv_crime --skip-profile-setup
```

Project layout to aim for:

```
dbt/
  dbt_project.yml
  profiles.yml              # committed; no secrets involved
  seeds/
    offense_map.csv         # replaces the INSERT block in schema.sql
  models/
    sources.yml             # declares the raw tables Python loads
    staging/
      stg_moco.sql
      stg_dc.sql
      stg_pgc.sql
      staging.yml           # column docs + tests
    marts/
      fct_incidents.sql
      daily_counts.sql
      marts.yml             # docs + tests
```

## Step 2: Point dbt at the warehouse (profiles.yml)

DuckDB is file-based, so the committed profile is enough; there are no
credentials.

```yaml
# dbt/profiles.yml
dmv_crime:
  target: prod
  outputs:
    prod:
      type: duckdb
      path: ../data/warehouse/crime.duckdb
      threads: 4
```

`dbt_project.yml` essentials:

```yaml
name: dmv_crime
version: "1.0"
profile: dmv_crime
model-paths: ["models"]
seed-paths: ["seeds"]
models:
  dmv_crime:
    staging:
      +materialized: view
      +schema: staging
    marts:
      +materialized: table
      +schema: marts
seeds:
  dmv_crime:
    +schema: marts
```

Note on schema naming: dbt-duckdb prefixes custom schemas with the
target schema by default (`main_staging`). If you want the exact
`marts.fct_incidents` name the site export expects, add the standard
`generate_schema_name` macro override that returns the custom schema
name as-is.

## Step 3: Declare sources (models/sources.yml)

The raw tables are loaded by Python, so they are dbt *sources*, not
models. Freshness checks are free documentation of the daily cadence.

```yaml
version: 2
sources:
  - name: raw
    schema: raw
    tables:
      - name: moco_crime
      - name: dc_crime
      - name: pgc_crime
```

## Step 4: Seed the offense map

Export the current dimension once:

```sql
COPY (SELECT * FROM marts.dim_offense_map) TO 'dbt/seeds/offense_map.csv' (HEADER);
```

The CSV has the nine DC offenses with category and severity_weight.
From now on, changing a mapping is a one-line CSV diff reviewed in a
PR, not an edit to a DDL script. `dbt seed` materializes it as
`marts.offense_map`; keep a `dim_offense_map` view or just update the
one JOIN that references it.

## Step 5: Staging models (one per source)

Each `stg_*.sql` is the typing/cleaning SELECT lifted out of
`transform.sql`, minus the INSERT wrapper. Pattern for each:

- read from `{{ source('raw', '<source>_crime') }}`
- CAST strings to real types (timestamps, doubles)
- normalize column drift with COALESCE (the PGC current/historical
  split lives here)
- compute `incident_key` and the ROW_NUMBER() dedupe
- map `offense_category` (keyword CASE for MoCo/PGC; the DC model joins
  `{{ ref('offense_map') }}`)

Keep staging as views: they cost nothing and always reflect raw.

## Step 6: Marts

`fct_incidents.sql` becomes a UNION ALL of the three staging models.
Two honest options for materialization:

1. `materialized: table` -- simplest, rebuilds the fact from raw every
   run. Because the Python loader already rebuilds raw tables in full
   from the parquet landing zone each run, this is functionally
   identical to today's INSERT OR REPLACE flow and is the recommended
   starting point.
2. `materialized: incremental` with `unique_key='incident_key'` -- the
   dbt-native equivalent of INSERT OR REPLACE, worth switching to only
   if full rebuilds ever get slow (at ~1M rows they are not).

`daily_counts.sql` is the existing view SELECT, materialized as a view.

## Step 7: Tests (the actual payoff)

`marts.yml`:

```yaml
version: 2
models:
  - name: fct_incidents
    columns:
      - name: incident_key
        tests: [unique, not_null]
      - name: jurisdiction
        tests:
          - accepted_values:
              values: [dc, moco, pgc]
      - name: offense_category
        tests:
          - accepted_values:
              values: [homicide, violent, sexual, property, vehicle, disorder, other]
      - name: severity_weight
        tests: [not_null]
```

Add staging tests for the invariants that have actually bitten before:
`occurred_at` not null after casting, coordinates within a DMV bounding
box or null (never guessed), dedupe leaving exactly one row per key.

## Step 8: Rewire the pipeline

In `load/load_duckdb.py`, replace the two `con.execute(sql_file)` calls
with a subprocess (dbt owns its own connection, so close DuckDB first):

```python
subprocess.run(
    ["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt"],
    check=True,
)
```

`dbt build` runs seeds, models, and tests in dependency order and exits
nonzero if a test fails, which fails the GitHub Actions run: exactly the
behavior wanted. No workflow YAML changes needed beyond the new pip
install.

## Step 9: Parity validation (do not skip)

Run old and new side by side once before deleting anything:

1. Check out main, run the pipeline, and save fingerprints:
   `SELECT COUNT(*), COUNT(DISTINCT incident_key) FROM marts.fct_incidents;`
   plus a `GROUP BY jurisdiction, offense_category` matrix exported to CSV.
2. Check out the dbt branch, run the pipeline against the same raw
   parquet (same cache), export the same fingerprints.
3. Diff. Zero row-level tolerance: same counts, same category matrix,
   same MIN/MAX(occurred_at). Investigate every difference; "close
   enough" hides mapping regressions.
4. Run `python test_pipeline_offline.py`; it asserts on mart contents
   and should pass unchanged.

## Step 10: Clean up

- Delete `sql/transform.sql`; shrink `sql/schema.sql` to just the raw
  schema DDL the loader still needs.
- Update CLAUDE.md (commands + architecture) and the README.
- Optional: `dbt docs generate` produces a lineage-graph site; it could
  even deploy under `site/dbt/` alongside the dashboard as a portfolio
  artifact.

## Gotchas learned from this codebase

- Socrata omits null columns per batch; the loader pads them today.
  Keep that padding in Python; staging models should still COALESCE
  defensively.
- DuckDB file locking: dbt and the export cannot hold write connections
  simultaneously; run them sequentially (the pipeline already does).
- Seed types: force `severity_weight` to integer with `+column_types`
  in `dbt_project.yml` if CSV inference ever wobbles.
- The site export reads `marts.fct_incidents` and `marts.daily_counts`
  by name; the `generate_schema_name` note in Step 2 is what keeps
  those names stable.
