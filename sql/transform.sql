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

-- -------------------------------------------- Prince George's County
-- Column names differ between the county's two dataset generations
-- (wb4e-w4nf vs xjru-idbe), so identifiers, offense text, and location
-- fields are coalesced across every candidate name; the loader pads any
-- candidate a batch omitted. Offense values are short uppercase phrases
-- like 'THEFT FROM AUTO', 'ASSAULT, WEAPON', 'B & E, RESIDENTIAL',
-- mapped by the keyword rules below. The dataset also includes traffic
-- ACCIDENT rows, which land in 'other' (published data stays published).
INSERT OR REPLACE INTO marts.fct_incidents (
    incident_key, jurisdiction, source_incident_id, case_number,
    occurred_at, occurred_end_at, reported_at,
    offense_raw, offense_category, severity_weight,
    block_address, city, zip_code, area_name,
    latitude, longitude, victims, method
)
WITH normalized AS (
    SELECT
        COALESCE(incident_case_id, id)                  AS src_id,
        TRY_CAST("date" AS TIMESTAMP)                   AS occurred_ts,
        UPPER(COALESCE(clearance_code_inc_type, offense, inc_type)) AS off,
        COALESCE(street_address, location, address)     AS addr,
        city, zip_code,
        COALESCE(pgpd_sector, sector, pgpd_beat)        AS area,
        NULLIF(TRY_CAST(latitude  AS DOUBLE), 0)        AS lat,
        NULLIF(TRY_CAST(longitude AS DOUBLE), 0)        AS lon
    FROM raw.pgc_incidents
),
deduped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY src_id ORDER BY occurred_ts DESC NULLS LAST
    ) AS rn
    FROM normalized
)
SELECT
    'pgc-' || src_id                                    AS incident_key,
    'pgc'                                               AS jurisdiction,
    src_id                                              AS source_incident_id,
    src_id                                              AS case_number,
    occurred_ts                                         AS occurred_at,
    NULL                                                AS occurred_end_at,
    NULL                                                AS reported_at,
    off                                                 AS offense_raw,
    CASE
        WHEN off LIKE '%HOMICIDE%' OR off LIKE '%MURDER%' THEN 'homicide'
        WHEN off LIKE '%SEX%' OR off LIKE '%RAPE%'        THEN 'sexual'
        WHEN off LIKE '%THEFT FROM AUTO%'
          OR off LIKE '%AUTO, STOLEN%'
          OR off LIKE '%STOLEN VEHICLE%'
          OR off LIKE '%CARJACK%'                         THEN 'vehicle'
        WHEN off LIKE '%ASSAULT%'
          OR off LIKE '%ROBBERY%'
          OR off LIKE '%WEAPON%'
          OR off LIKE '%SHOOTING%'                        THEN 'violent'
        WHEN off LIKE '%B & E%'
          OR off LIKE '%BREAKING%'
          OR off LIKE '%BURGLARY%'
          OR off LIKE '%THEFT%'
          OR off LIKE '%LARCENY%'
          OR off LIKE '%VANDAL%'
          OR off LIKE '%FRAUD%'
          OR off LIKE '%ARSON%'                           THEN 'property'
        WHEN off LIKE '%DRUG%'
          OR off LIKE '%NARCOT%'
          OR off LIKE '%ALCOHOL%'
          OR off LIKE '%DUI%'
          OR off LIKE '%DISORDER%'                        THEN 'disorder'
        ELSE 'other'
    END                                                 AS offense_category,
    CASE
        WHEN off LIKE '%HOMICIDE%' OR off LIKE '%MURDER%' THEN 10
        WHEN off LIKE '%SEX%' OR off LIKE '%RAPE%'        THEN 9
        WHEN off LIKE '%SHOOTING%'                        THEN 8
        WHEN off LIKE '%ROBBERY%' OR off LIKE '%CARJACK%' THEN 7
        WHEN off LIKE '%ARSON%'                           THEN 6
        WHEN off LIKE '%ASSAULT%' OR off LIKE '%B & E%'
          OR off LIKE '%BURGLARY%' OR off LIKE '%BREAKING%' THEN 5
        WHEN off LIKE '%AUTO, STOLEN%' OR off LIKE '%STOLEN VEHICLE%'
          OR off LIKE '%WEAPON%'                          THEN 4
        WHEN off LIKE '%THEFT%' OR off LIKE '%LARCENY%'   THEN 3
        WHEN off LIKE '%DRUG%' OR off LIKE '%NARCOT%'
          OR off LIKE '%DUI%'                             THEN 2
        ELSE 1
    END                                                 AS severity_weight,
    addr                                                AS block_address,
    city,
    zip_code,
    area                                                AS area_name,
    lat                                                 AS latitude,
    lon                                                 AS longitude,
    NULL                                                AS victims,
    NULL                                                AS method
FROM deduped
WHERE rn = 1 AND src_id IS NOT NULL;

-- ------------------------------------------------------ Fairfax County
-- FCPD's "Crimes Against" services are victim/offense level: an incident
-- with several victims repeats its IncidentNumber, each row carrying the
-- offense that applied to that victim. Dedupe keeps one row per incident,
-- preferring the most severe offense (then the latest report) so a
-- homicide with three victims lands as one homicide, matching the
-- incident-level semantics of the other jurisdictions. Category is FCPD's
-- own NIBRS-group label ('Aggravated Assault', 'Larceny'); the longer
-- ViolationCodeReference_Descript is kept as the raw offense text.
INSERT OR REPLACE INTO marts.fct_incidents (
    incident_key, jurisdiction, source_incident_id, case_number,
    occurred_at, occurred_end_at, reported_at,
    offense_raw, offense_category, severity_weight,
    block_address, city, zip_code, area_name,
    latitude, longitude, victims, method
)
WITH normalized AS (
    SELECT
        TRIM(IncidentNumber)                            AS src_id,
        epoch_ms(TRY_CAST(BeginDate AS BIGINT))         AS occurred_ts,
        epoch_ms(TRY_CAST(COALESCE(DateReported, ReportDate) AS BIGINT)) AS reported_ts,
        UPPER(TRIM(COALESCE(ViolationCodeReference_Descript,
                            IBRDescription, EventDescription, Category))) AS off,
        DISTRICT                                        AS district,
        NULLIF(TRY_CAST(latitude  AS DOUBLE), 0)        AS lat,
        NULLIF(TRY_CAST(longitude AS DOUBLE), 0)        AS lon
    FROM raw.fairfax_incidents
),
scored AS (
    SELECT *,
        CASE
            WHEN off LIKE '%HOMICIDE%' OR off LIKE '%MURDER%'
              OR off LIKE '%MANSLAUGHTER%'                THEN 10
            WHEN off LIKE '%RAPE%' OR off LIKE '%SEX%'
              OR off LIKE '%SODOMY%' OR off LIKE '%FONDLING%' THEN 9
            WHEN off LIKE '%AGGRAVATED%'                  THEN 8
            WHEN off LIKE '%ROBBERY%' OR off LIKE '%CARJACK%' THEN 7
            WHEN off LIKE '%ARSON%'                       THEN 6
            WHEN off LIKE '%ASSAULT%' OR off LIKE '%KIDNAP%'
              OR off LIKE '%BURGLARY%' OR off LIKE '%BREAKING%' THEN 5
            WHEN off LIKE '%MOTOR VEHICLE THEFT%'
              OR off LIKE '%STOLEN%' OR off LIKE '%WEAPON%' THEN 4
            WHEN off LIKE '%THEFT%' OR off LIKE '%LARCENY%'
              OR off LIKE '%FRAUD%' OR off LIKE '%VANDAL%'
              OR off LIKE '%DESTRUCTION%'                 THEN 3
            WHEN off LIKE '%DRUG%' OR off LIKE '%NARCOT%'
              OR off LIKE '%DUI%' OR off LIKE '%LIQUOR%'
              OR off LIKE '%DISORDER%' OR off LIKE '%PROSTITUTION%'
              OR off LIKE '%GAMBLING%'                    THEN 2
            ELSE 1
        END AS sev
    FROM normalized
),
deduped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY src_id
        ORDER BY sev DESC, reported_ts DESC NULLS LAST
    ) AS rn
    FROM scored
)
SELECT
    'fairfax-' || src_id                                AS incident_key,
    'fairfax'                                           AS jurisdiction,
    src_id                                              AS source_incident_id,
    src_id                                              AS case_number,
    COALESCE(occurred_ts, reported_ts)                  AS occurred_at,
    NULL                                                AS occurred_end_at,
    reported_ts                                         AS reported_at,
    off                                                 AS offense_raw,
    CASE
        WHEN sev = 10                                   THEN 'homicide'
        WHEN sev = 9                                    THEN 'sexual'
        WHEN off LIKE '%CARJACK%'
          OR off LIKE '%MOTOR VEHICLE THEFT%'
          OR off LIKE '%FROM MOTOR VEHICLE%'            THEN 'vehicle'
        WHEN off LIKE '%AGGRAVATED%' OR off LIKE '%ROBBERY%'
          OR off LIKE '%ASSAULT%' OR off LIKE '%KIDNAP%'
          OR off LIKE '%INTIMIDATION%' OR off LIKE '%WEAPON%' THEN 'violent'
        WHEN off LIKE '%ARSON%' OR off LIKE '%BURGLARY%'
          OR off LIKE '%BREAKING%' OR off LIKE '%THEFT%'
          OR off LIKE '%LARCENY%' OR off LIKE '%STOLEN%'
          OR off LIKE '%FRAUD%' OR off LIKE '%VANDAL%'
          OR off LIKE '%DESTRUCTION%' OR off LIKE '%COUNTERFEIT%'
          OR off LIKE '%EMBEZZLE%' OR off LIKE '%EXTORT%' THEN 'property'
        WHEN off LIKE '%DRUG%' OR off LIKE '%NARCOT%'
          OR off LIKE '%DUI%' OR off LIKE '%LIQUOR%'
          OR off LIKE '%DISORDER%' OR off LIKE '%PROSTITUTION%'
          OR off LIKE '%GAMBLING%' OR off LIKE '%PORNOGRAPHY%' THEN 'disorder'
        ELSE 'other'
    END                                                 AS offense_category,
    sev                                                 AS severity_weight,
    NULL                                                AS block_address,
    NULL                                                AS city,
    NULL                                                AS zip_code,
    CASE WHEN district IS NOT NULL
         THEN CONCAT(UPPER(SUBSTR(district, 1, 1)), LOWER(SUBSTR(district, 2)), ' District')
    END                                                 AS area_name,
    lat                                                 AS latitude,
    lon                                                 AS longitude,
    NULL                                                AS victims,
    NULL                                                AS method
FROM deduped
WHERE rn = 1 AND src_id IS NOT NULL AND src_id <> '';

-- -------------------------------------------- Prince William County
-- PWC publishes NIBRS offense text in IBRCode (alias "Crime Type",
-- e.g. 'MOTOR VEHICLE THEFT', 'ALL OTHER OFFENSES') plus its own
-- CrimeCategory rollup. A case can repeat across offense rows, so
-- dedupe keeps one row per CaseNo preferring the most severe offense.
-- The county withholds sexual offenses entirely and maps locations to
-- the nearest 100 block; data covers a rolling 3-year window.
INSERT OR REPLACE INTO marts.fct_incidents (
    incident_key, jurisdiction, source_incident_id, case_number,
    occurred_at, occurred_end_at, reported_at,
    offense_raw, offense_category, severity_weight,
    block_address, city, zip_code, area_name,
    latitude, longitude, victims, method
)
WITH normalized AS (
    SELECT
        COALESCE(TRIM(CaseNo), InstanceID)              AS src_id,
        epoch_ms(TRY_CAST(OccurredOn AS BIGINT))        AS occurred_ts,
        epoch_ms(TRY_CAST(OccurredBetween AS BIGINT))   AS occurred_end_ts,
        UPPER(TRIM(COALESCE(IBRCode, CrimeCategory)))   AS off,
        BlockAddress                                    AS addr,
        City                                            AS city,
        ZipCode                                         AS zip,
        NULLIF(TRY_CAST(latitude  AS DOUBLE), 0)        AS lat,
        NULLIF(TRY_CAST(longitude AS DOUBLE), 0)        AS lon
    FROM raw.pwc_incidents
),
scored AS (
    SELECT *,
        CASE
            WHEN off LIKE '%HOMICIDE%' OR off LIKE '%MURDER%'
              OR off LIKE '%MANSLAUGHTER%'                THEN 10
            WHEN off LIKE '%RAPE%' OR off LIKE '%SEX%'
              OR off LIKE '%SODOMY%' OR off LIKE '%FONDLING%' THEN 9
            WHEN off LIKE '%AGGRAVATED%'                  THEN 8
            WHEN off LIKE '%ROBBERY%' OR off LIKE '%CARJACK%' THEN 7
            WHEN off LIKE '%ARSON%'                       THEN 6
            WHEN off LIKE '%ASSAULT%' OR off LIKE '%KIDNAP%'
              OR off LIKE '%BURGLARY%' OR off LIKE '%BREAKING%' THEN 5
            WHEN off LIKE '%MOTOR VEHICLE THEFT%'
              OR off LIKE '%STOLEN%' OR off LIKE '%WEAPON%' THEN 4
            WHEN off LIKE '%THEFT%' OR off LIKE '%LARCENY%'
              OR off LIKE '%FRAUD%' OR off LIKE '%VANDAL%'
              OR off LIKE '%DESTRUCTION%'                 THEN 3
            WHEN off LIKE '%DRUG%' OR off LIKE '%NARCOT%'
              OR off LIKE '%DUI%' OR off LIKE '%LIQUOR%'
              OR off LIKE '%DISORDER%' OR off LIKE '%PROSTITUTION%'
              OR off LIKE '%GAMBLING%'                    THEN 2
            ELSE 1
        END AS sev
    FROM normalized
),
deduped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY src_id
        ORDER BY sev DESC, occurred_ts DESC NULLS LAST
    ) AS rn
    FROM scored
)
SELECT
    'pwc-' || src_id                                    AS incident_key,
    'pwc'                                               AS jurisdiction,
    src_id                                              AS source_incident_id,
    src_id                                              AS case_number,
    occurred_ts                                         AS occurred_at,
    occurred_end_ts                                     AS occurred_end_at,
    NULL                                                AS reported_at,
    off                                                 AS offense_raw,
    CASE
        WHEN sev = 10                                   THEN 'homicide'
        WHEN sev = 9                                    THEN 'sexual'
        WHEN off LIKE '%CARJACK%'
          OR off LIKE '%MOTOR VEHICLE THEFT%'
          OR off LIKE '%FROM MOTOR VEHICLE%'            THEN 'vehicle'
        WHEN off LIKE '%AGGRAVATED%' OR off LIKE '%ROBBERY%'
          OR off LIKE '%ASSAULT%' OR off LIKE '%KIDNAP%'
          OR off LIKE '%INTIMIDATION%' OR off LIKE '%WEAPON%' THEN 'violent'
        WHEN off LIKE '%ARSON%' OR off LIKE '%BURGLARY%'
          OR off LIKE '%BREAKING%' OR off LIKE '%THEFT%'
          OR off LIKE '%LARCENY%' OR off LIKE '%STOLEN%'
          OR off LIKE '%FRAUD%' OR off LIKE '%VANDAL%'
          OR off LIKE '%DESTRUCTION%' OR off LIKE '%COUNTERFEIT%'
          OR off LIKE '%EMBEZZLE%' OR off LIKE '%EXTORT%' THEN 'property'
        WHEN off LIKE '%DRUG%' OR off LIKE '%NARCOT%'
          OR off LIKE '%DUI%' OR off LIKE '%LIQUOR%'
          OR off LIKE '%DISORDER%' OR off LIKE '%PROSTITUTION%'
          OR off LIKE '%GAMBLING%' OR off LIKE '%PORNOGRAPHY%' THEN 'disorder'
        ELSE 'other'
    END                                                 AS offense_category,
    sev                                                 AS severity_weight,
    addr                                                AS block_address,
    city,
    zip                                                 AS zip_code,
    city                                                AS area_name,
    lat                                                 AS latitude,
    lon                                                 AS longitude,
    NULL                                                AS victims,
    NULL                                                AS method
FROM deduped
WHERE rn = 1 AND src_id IS NOT NULL AND src_id <> '';

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
