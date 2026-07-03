"""Export the DuckDB warehouse to static JSON for the site.

Runs after load_duckdb.run(). Four files, each sized for what the page
actually needs:

  summary.json    small KPI/freshness payload for the home page
  incidents.json  incident-level detail for the map and the events search,
                  last INCIDENT_WINDOW_DAYS only (client-side search has to
                  download this file, so the window is a deliberate cap);
                  columnar rows to keep the payload compact
  trends.json     pre-aggregated daily counts by jurisdiction and category
                  over the FULL history, so the trends page can offer any
                  period (e.g. 2017-2020) without incident-level data
  heatmap.json    weekday x hour counts over the last HEATMAP_WINDOW_DAYS

Timestamps are cast to VARCHAR in SQL so the JSON output needs no special
datetime handling on the Python side.
"""

import json
import logging

import duckdb

from config import SITE_DATA_DIR, WAREHOUSE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s export: %(message)s")
logger = logging.getLogger(__name__)

INCIDENT_WINDOW_DAYS = 90
HEATMAP_WINDOW_DAYS = 90

NOW = "CAST(now() AS TIMESTAMP)"

INCIDENT_COLUMNS = [
    "incident_key", "jurisdiction", "offense_category", "severity_weight",
    "offense_raw", "occurred_at", "block_address", "area_name", "city",
    "case_number", "latitude", "longitude",
]

# Plain-English labels for digest bullets (emails render no JS, so the
# Python side needs its own copy; keep in sync with site/js/common.js).
JURISDICTION_LABELS = {
    "dc": "Washington DC",
    "moco": "Montgomery County",
    "pgc": "Prince George's County",
}
CATEGORY_LABELS = {
    "homicide": "Homicide / Fatal Violence",
    "violent": "Violent Crime",
    "sexual": "Sexual Offenses",
    "property": "Property Crime",
    "vehicle": "Vehicle-Related Crime",
    "disorder": "Drug / Alcohol / Disorder",
    "other": "Other / Unknown",
}


def _write_json(name: str, data) -> None:
    path = SITE_DATA_DIR / name
    # separators (no indent) keeps the larger payloads as small as possible
    path.write_text(json.dumps(data, separators=(",", ":")))
    logger.info("Wrote %s (%.1f KB)", path, path.stat().st_size / 1024)


def _rows_as_dicts(con, sql: str) -> list[dict]:
    rows = con.execute(sql).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row)) for row in rows]


def _summary(con) -> dict:
    total = con.execute("SELECT COUNT(*) FROM marts.fct_incidents").fetchone()[0]
    by_source = con.execute(
        "SELECT jurisdiction, COUNT(*) FROM marts.fct_incidents GROUP BY 1 ORDER BY 1"
    ).fetchall()
    new_24h, new_48h = con.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 24 HOUR),
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 48 HOUR)
        FROM marts.fct_incidents
    """).fetchone()
    today_count, last_7d, prev_7d, pct_missing_coords, data_start = con.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE CAST(occurred_at AS DATE) = current_date),
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 7 DAY),
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 14 DAY
                                AND occurred_at < {NOW} - INTERVAL 7 DAY),
            100.0 * COUNT(*) FILTER (WHERE latitude IS NULL) / GREATEST(COUNT(*), 1),
            CAST(MIN(CAST(occurred_at AS DATE)) AS VARCHAR)
        FROM marts.fct_incidents
    """).fetchone()
    top_category_row = con.execute(f"""
        SELECT offense_category, COUNT(*) AS n
        FROM marts.fct_incidents
        WHERE occurred_at >= {NOW} - INTERVAL 7 DAY
        GROUP BY 1 ORDER BY 2 DESC LIMIT 1
    """).fetchone()
    pct_change_7d = round((last_7d - prev_7d) / prev_7d * 100, 1) if prev_7d else None
    return {
        "last_updated": con.execute("SELECT strftime(now(), '%Y-%m-%dT%H:%M:%SZ')").fetchone()[0],
        "sources_active": [j for j, _ in by_source],
        "total_records": total,
        "records_by_jurisdiction": {j: n for j, n in by_source},
        "data_start_date": data_start,
        "incident_window_days": INCIDENT_WINDOW_DAYS,
        "new_incidents_24h": new_24h,
        "new_incidents_48h": new_48h,
        "today_count": today_count,
        "last_7d_count": last_7d,
        "prev_7d_count": prev_7d,
        "pct_change_7d": pct_change_7d,
        "top_category_7d": top_category_row[0] if top_category_row else None,
        "pct_missing_coords": round(pct_missing_coords, 1),
        "pipeline_status": "ok",
    }


def _incidents(con) -> dict:
    rows = con.execute(f"""
        SELECT
            incident_key, jurisdiction, offense_category, severity_weight,
            offense_raw, strftime(occurred_at, '%Y-%m-%dT%H:%M:%S') AS occurred_at,
            block_address, area_name, city, case_number,
            ROUND(latitude, 6) AS latitude, ROUND(longitude, 6) AS longitude
        FROM marts.fct_incidents
        WHERE occurred_at >= {NOW} - INTERVAL {INCIDENT_WINDOW_DAYS} DAY
          AND occurred_at <= {NOW} + INTERVAL 1 DAY
        ORDER BY occurred_at DESC
    """).fetchall()
    return {
        "window_days": INCIDENT_WINDOW_DAYS,
        "columns": INCIDENT_COLUMNS,
        "rows": [list(r) for r in rows],
    }


def _trends(con) -> dict:
    # occurred_at sanity bounds guard against source-side typo dates
    # (year 1900 or 2216) polluting the aggregates.
    rows = con.execute("""
        SELECT
            CAST(occurred_date AS VARCHAR) AS date,
            jurisdiction, offense_category,
            SUM(incident_count) AS count
        FROM marts.daily_counts
        WHERE occurred_date >= DATE '2016-01-01'
          AND occurred_date <= current_date
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """).fetchall()
    return {
        "columns": ["date", "jurisdiction", "offense_category", "count"],
        "rows": [list(r) for r in rows],
    }


def _heatmap(con) -> dict:
    # Keeps jurisdiction and category as dimensions so the site can
    # filter the heatmap client-side; the page aggregates hours into
    # dayparts (morning/afternoon/evening/night) itself.
    rows = con.execute(f"""
        SELECT
            dayofweek(occurred_at) AS weekday,
            hour(occurred_at)      AS hour,
            jurisdiction,
            offense_category,
            COUNT(*)               AS count
        FROM marts.fct_incidents
        WHERE occurred_at >= {NOW} - INTERVAL {HEATMAP_WINDOW_DAYS} DAY
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    """).fetchall()
    return {
        "window_days": HEATMAP_WINDOW_DAYS,
        "columns": ["weekday", "hour", "jurisdiction", "offense_category", "count"],
        "rows": [list(r) for r in rows],
    }


def _digest(con) -> dict:
    """Daily brief for the latest complete data day: counts, comparisons,
    plain-English bullets, and the day's most severe incidents. Feeds
    both the Daily Brief page and the optional email digest. Every
    statement is computed from the warehouse; nothing is editorialized
    beyond comparisons to the incidents' own history."""
    latest_day = con.execute("""
        SELECT MAX(CAST(occurred_at AS DATE)) FROM marts.fct_incidents
        WHERE occurred_at <= now()
    """).fetchone()[0]
    if latest_day is None:
        return {"latest_day": None, "bullets": ["No data available yet."],
                "by_jurisdiction": [], "by_category": [], "notable": [], "last14": []}
    day = f"DATE '{latest_day}'"

    total, prev_day_total = con.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE CAST(occurred_at AS DATE) = {day}),
            COUNT(*) FILTER (WHERE CAST(occurred_at AS DATE) = {day} - 1)
        FROM marts.fct_incidents
    """).fetchone()
    # typical volume: same weekday over the prior 8 weeks
    trailing_avg = con.execute(f"""
        SELECT AVG(n) FROM (
            SELECT CAST(occurred_at AS DATE) AS d, COUNT(*) AS n
            FROM marts.fct_incidents
            WHERE CAST(occurred_at AS DATE) BETWEEN {day} - 56 AND {day} - 1
              AND dayofweek(occurred_at) = dayofweek({day})
            GROUP BY 1
        )
    """).fetchone()[0]

    by_jurisdiction = _rows_as_dicts(con, f"""
        SELECT jurisdiction, COUNT(*) AS count
        FROM marts.fct_incidents
        WHERE CAST(occurred_at AS DATE) = {day}
        GROUP BY 1 ORDER BY 2 DESC
    """)
    by_category = _rows_as_dicts(con, f"""
        SELECT offense_category, COUNT(*) AS count
        FROM marts.fct_incidents
        WHERE CAST(occurred_at AS DATE) = {day}
        GROUP BY 1 ORDER BY 2 DESC
    """)
    notable = _rows_as_dicts(con, f"""
        SELECT jurisdiction, offense_category, offense_raw, method,
               block_address, area_name,
               strftime(occurred_at, '%Y-%m-%dT%H:%M:%S') AS occurred_at,
               severity_weight
        FROM marts.fct_incidents
        WHERE CAST(occurred_at AS DATE) = {day}
        ORDER BY severity_weight DESC, occurred_at DESC
        LIMIT 6
    """)
    last14 = _rows_as_dicts(con, f"""
        SELECT CAST(CAST(occurred_at AS DATE) AS VARCHAR) AS date, COUNT(*) AS count
        FROM marts.fct_incidents
        WHERE CAST(occurred_at AS DATE) BETWEEN {day} - 13 AND {day}
        GROUP BY 1 ORDER BY 1
    """)

    bullets = []
    if trailing_avg:
        ratio = total / trailing_avg
        mood = ("higher than typical" if ratio > 1.15
                else "quieter than typical" if ratio < 0.85 else "in the typical range")
        bullets.append(
            f"{total:,} incidents were reported on {latest_day:%A, %B %-d}, "
            f"{mood} for a {latest_day:%A} (average {trailing_avg:.0f} over the prior 8 weeks).")
    else:
        bullets.append(f"{total:,} incidents were reported on {latest_day:%A, %B %-d}.")
    if by_category:
        top = by_category[0]
        bullets.append(
            f"The most common category was "
            f"{CATEGORY_LABELS.get(top['offense_category'], top['offense_category'])} "
            f"({top['count']:,} of {total:,} incidents).")
    for row in by_jurisdiction:
        bullets.append(
            f"{JURISDICTION_LABELS.get(row['jurisdiction'], row['jurisdiction'])}: "
            f"{row['count']:,} reported incidents.")
    homicides = sum(r["count"] for r in by_category if r["offense_category"] == "homicide")
    if homicides:
        bullets.append(f"{homicides} homicide{'s' if homicides != 1 else ''} reported.")

    return {
        "generated_at": con.execute("SELECT strftime(now(), '%Y-%m-%dT%H:%M:%SZ')").fetchone()[0],
        "latest_day": str(latest_day),
        "total": total,
        "prev_day_total": prev_day_total,
        "trailing_avg_same_weekday": round(trailing_avg, 1) if trailing_avg else None,
        "by_jurisdiction": by_jurisdiction,
        "by_category": by_category,
        "notable": notable,
        "last14": last14,
        "bullets": bullets,
    }


def run() -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    _write_json("summary.json", _summary(con))
    _write_json("incidents.json", _incidents(con))
    _write_json("trends.json", _trends(con))
    _write_json("heatmap.json", _heatmap(con))
    _write_json("digest.json", _digest(con))

    con.close()
    logger.info("Site data export complete")


if __name__ == "__main__":
    run()
