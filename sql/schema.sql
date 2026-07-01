-- =====================================================================
-- DMV Crime Pipeline: DuckDB warehouse schema
-- =====================================================================
-- Layers:
--   raw    : untouched source data loaded from parquet (all VARCHAR,
--            created by load/load_duckdb.py via read_parquet)
--   marts  : typed, unified, analysis-ready tables (defined here)
--
-- Design decisions:
--   1. Every jurisdiction lands in its own raw table with source column
--      names preserved. Rebuildable at any time from the parquet zone.
--   2. One unified fact table (marts.fct_incidents) with a shared crime
--      taxonomy, so every dashboard and digest queries a single model.
--   3. dim_offense_map makes the DC category mapping explicit data, not
--      buried logic. Montgomery County NIBRS categories are mapped by
--      keyword rules in sql/transform.sql (documented there). Both will
--      migrate cleanly to dbt seeds and models in the next phase.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS marts;

-- ---------------------------------------------------------------------
-- Unified crime taxonomy used across all jurisdictions:
--   violent | property | vehicle | drug | society | other
-- severity_weight (1-10) feeds the priority-case scoring later.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marts.dim_offense_map (
    jurisdiction     VARCHAR NOT NULL,
    source_offense   VARCHAR NOT NULL,
    unified_category VARCHAR NOT NULL,
    severity_weight  TINYINT NOT NULL,
    PRIMARY KEY (jurisdiction, source_offense)
);

-- DC publishes a closed set of nine offense values, so map them fully.
INSERT OR REPLACE INTO marts.dim_offense_map VALUES
    ('dc', 'HOMICIDE',                  'violent',  10),
    ('dc', 'SEX ABUSE',                 'violent',   9),
    ('dc', 'ASSAULT W/DANGEROUS WEAPON','violent',   8),
    ('dc', 'ROBBERY',                   'violent',   7),
    ('dc', 'ARSON',                     'property',  6),
    ('dc', 'BURGLARY',                  'property',  5),
    ('dc', 'MOTOR VEHICLE THEFT',       'vehicle',   4),
    ('dc', 'THEFT F/AUTO',              'vehicle',   3),
    ('dc', 'THEFT/OTHER',               'property',  3);

-- ---------------------------------------------------------------------
-- Unified incident fact table. One row per incident per jurisdiction.
-- incident_key = '{jurisdiction}-{source id}' guarantees global
-- uniqueness; INSERT OR REPLACE on this key makes loads idempotent.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marts.fct_incidents (
    incident_key       VARCHAR PRIMARY KEY,
    jurisdiction       VARCHAR NOT NULL,          -- 'moco' | 'dc'
    source_incident_id VARCHAR NOT NULL,
    case_number        VARCHAR,
    occurred_at        TIMESTAMP,                 -- offense start
    occurred_end_at    TIMESTAMP,
    reported_at        TIMESTAMP,                 -- when police logged it
    offense_raw        VARCHAR,                   -- source-native label
    offense_category   VARCHAR NOT NULL,          -- unified taxonomy
    severity_weight    TINYINT NOT NULL,
    block_address      VARCHAR,                   -- block-level only, as published
    city               VARCHAR,
    zip_code           VARCHAR,
    area_name          VARCHAR,                   -- police district / ward
    latitude           DOUBLE,                    -- NULL when source geocode failed
    longitude          DOUBLE,
    victims            INTEGER,                   -- MoCo only
    method             VARCHAR,                   -- DC only (gun/knife/others)
    loaded_at          TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- ---------------------------------------------------------------------
-- Daily rollup consumed by the dashboards and the digest job.
-- Rebuilt as a view so it is always consistent with the fact table.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW marts.daily_counts AS
SELECT
    jurisdiction,
    area_name,
    offense_category,
    CAST(occurred_at AS DATE) AS occurred_date,
    COUNT(*)                  AS incident_count,
    SUM(severity_weight)      AS severity_sum
FROM marts.fct_incidents
WHERE occurred_at IS NOT NULL
GROUP BY ALL;
