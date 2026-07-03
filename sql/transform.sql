-- =====================================================================
-- Transform: raw source tables -> marts.fct_incidents
-- =====================================================================
-- Idempotent by design: dedupe on the source id (keep the most recent
-- version of each incident), then INSERT OR REPLACE on incident_key.
-- Re-running the pipeline, overlap windows, and agency corrections all
-- resolve to the latest version of each record.
--
-- Montgomery County category mapping uses keyword rules over the NIBRS
-- category in crimename2 because NIBRS has dozens of values; the rules
-- below cover the taxonomy the dashboards need. DC uses the explicit
-- dim_offense_map since its offense list is a closed set of nine.
-- =====================================================================

-- ------------------------------------------------- Montgomery County
INSERT OR REPLACE INTO marts.fct_incidents (
    incident_key, jurisdiction, source_incident_id, case_number,
    occurred_at, occurred_end_at, reported_at,
    offense_raw, offense_category, severity_weight,
    block_address, city, zip_code, area_name,
    latitude, longitude, victims, method
)
WITH deduped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY incident_id
        ORDER BY TRY_CAST(start_date AS TIMESTAMP) DESC NULLS LAST
    ) AS rn
    FROM raw.moco_incidents
)
SELECT
    'moco-' || incident_id                              AS incident_key,
    'moco'                                              AS jurisdiction,
    incident_id                                         AS source_incident_id,
    case_number,
    TRY_CAST(start_date AS TIMESTAMP)                   AS occurred_at,
    TRY_CAST(end_date AS TIMESTAMP)                     AS occurred_end_at,
    NULL                                                AS reported_at,
    COALESCE(crimename2, crimename3)                    AS offense_raw,
    CASE
        WHEN UPPER(crimename2) LIKE '%HOMICIDE%'
          OR UPPER(crimename2) LIKE '%MURDER%'
          OR UPPER(crimename2) LIKE '%MANSLAUGHTER%'    THEN 'homicide'
        WHEN UPPER(crimename2) LIKE '%RAPE%'
          OR UPPER(crimename2) LIKE '%SEX%'
          OR UPPER(crimename2) LIKE '%SODOMY%'
          OR UPPER(crimename2) LIKE '%FONDLING%'        THEN 'sexual'
        WHEN UPPER(crimename2) LIKE '%CARJACK%'         THEN 'vehicle'
        WHEN UPPER(crimename2) LIKE '%AGGRAVATED ASSAULT%' THEN 'violent'
        WHEN UPPER(crimename2) LIKE '%ROBBERY%'         THEN 'violent'
        WHEN UPPER(crimename2) LIKE '%ASSAULT%'
          OR UPPER(crimename2) LIKE '%INTIMIDATION%'
          OR UPPER(crimename2) LIKE '%KIDNAP%'
          OR UPPER(crimename2) LIKE '%WEAPON%'          THEN 'violent'
        WHEN UPPER(crimename2) LIKE '%ARSON%'           THEN 'property'
        WHEN UPPER(crimename2) LIKE '%BURGLARY%'        THEN 'property'
        WHEN UPPER(crimename2) LIKE '%MOTOR VEHICLE THEFT%' THEN 'vehicle'
        WHEN UPPER(crimename2) LIKE '%FROM MOTOR VEHICLE%'  THEN 'vehicle'
        WHEN UPPER(crimename2) LIKE '%THEFT%'
          OR UPPER(crimename2) LIKE '%LARCENY%'
          OR UPPER(crimename2) LIKE '%SHOPLIFT%'
          OR UPPER(crimename2) LIKE '%STOLEN%'
          OR UPPER(crimename2) LIKE '%FRAUD%'
          OR UPPER(crimename2) LIKE '%VANDALISM%'
          OR UPPER(crimename2) LIKE '%DESTRUCTION%'     THEN 'property'
        WHEN UPPER(crimename2) LIKE '%DRUG%'
          OR UPPER(crimename2) LIKE '%NARCOTIC%'
          OR UPPER(crimename2) LIKE '%DUI%'
          OR UPPER(crimename2) LIKE '%LIQUOR%'
          OR UPPER(crimename2) LIKE '%DISORDERLY%'
          OR UPPER(crimename2) LIKE '%PROSTITUTION%'
          OR UPPER(crimename2) LIKE '%GAMBLING%'        THEN 'disorder'
        ELSE 'other'
    END                                                 AS offense_category,
    CASE
        WHEN UPPER(crimename2) LIKE '%HOMICIDE%'
          OR UPPER(crimename2) LIKE '%MURDER%'
          OR UPPER(crimename2) LIKE '%MANSLAUGHTER%'    THEN 10
        WHEN UPPER(crimename2) LIKE '%RAPE%'
          OR UPPER(crimename2) LIKE '%SEX%'
          OR UPPER(crimename2) LIKE '%SODOMY%'
          OR UPPER(crimename2) LIKE '%FONDLING%'        THEN 9
        WHEN UPPER(crimename2) LIKE '%AGGRAVATED ASSAULT%' THEN 8
        WHEN UPPER(crimename2) LIKE '%ROBBERY%'
          OR UPPER(crimename2) LIKE '%CARJACK%'         THEN 7
        WHEN UPPER(crimename2) LIKE '%ARSON%'           THEN 6
        WHEN UPPER(crimename2) LIKE '%ASSAULT%'
          OR UPPER(crimename2) LIKE '%KIDNAP%'
          OR UPPER(crimename2) LIKE '%BURGLARY%'        THEN 5
        WHEN UPPER(crimename2) LIKE '%MOTOR VEHICLE THEFT%'
          OR UPPER(crimename2) LIKE '%WEAPON%'          THEN 4
        WHEN UPPER(crimename2) LIKE '%THEFT%'
          OR UPPER(crimename2) LIKE '%LARCENY%'
          OR UPPER(crimename2) LIKE '%FROM MOTOR VEHICLE%' THEN 3
        WHEN UPPER(crimename2) LIKE '%DRUG%'
          OR UPPER(crimename2) LIKE '%NARCOTIC%'
          OR UPPER(crimename2) LIKE '%DUI%'
          OR UPPER(crimename2) LIKE '%LIQUOR%'
          OR UPPER(crimename2) LIKE '%DISORDERLY%'      THEN 2
        ELSE 1
    END                                                 AS severity_weight,
    location                                            AS block_address,
    city,
    zip_code,
    district                                            AS area_name,
    NULLIF(TRY_CAST(latitude  AS DOUBLE), 0)            AS latitude,
    NULLIF(TRY_CAST(longitude AS DOUBLE), 0)            AS longitude,
    TRY_CAST(TRY_CAST(victims AS DOUBLE) AS INTEGER)    AS victims,
    NULL                                                AS method
FROM deduped
WHERE rn = 1 AND incident_id IS NOT NULL;

-- ----------------------------------------------------------------- DC
INSERT OR REPLACE INTO marts.fct_incidents (
    incident_key, jurisdiction, source_incident_id, case_number,
    occurred_at, occurred_end_at, reported_at,
    offense_raw, offense_category, severity_weight,
    block_address, city, zip_code, area_name,
    latitude, longitude, victims, method
)
WITH deduped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY CCN
        ORDER BY TRY_CAST(REPORT_DAT AS BIGINT) DESC NULLS LAST
    ) AS rn
    FROM raw.dc_incidents
)
SELECT
    'dc-' || CCN                                        AS incident_key,
    'dc'                                                AS jurisdiction,
    CCN                                                 AS source_incident_id,
    CCN                                                 AS case_number,
    epoch_ms(TRY_CAST(START_DATE AS BIGINT))            AS occurred_at,
    epoch_ms(TRY_CAST(END_DATE AS BIGINT))              AS occurred_end_at,
    epoch_ms(TRY_CAST(REPORT_DAT AS BIGINT))            AS reported_at,
    d.OFFENSE                                           AS offense_raw,
    COALESCE(m.unified_category, 'other')               AS offense_category,
    COALESCE(m.severity_weight, 1)                      AS severity_weight,
    d.BLOCK                                             AS block_address,
    'WASHINGTON'                                        AS city,
    NULL                                                AS zip_code,
    'Ward ' || d.WARD                                   AS area_name,
    NULLIF(TRY_CAST(d.LATITUDE  AS DOUBLE), 0)          AS latitude,
    NULLIF(TRY_CAST(d.LONGITUDE AS DOUBLE), 0)          AS longitude,
    NULL                                                AS victims,
    d.METHOD                                            AS method
FROM deduped d
LEFT JOIN marts.dim_offense_map m
       ON m.jurisdiction = 'dc' AND m.source_offense = d.OFFENSE
WHERE d.rn = 1 AND d.CCN IS NOT NULL;
