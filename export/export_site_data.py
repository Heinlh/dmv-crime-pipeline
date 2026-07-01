"""Export the DuckDB warehouse to static JSON/GeoJSON for the site.

Runs after load_duckdb.run(). Timestamps are cast to VARCHAR in SQL so the
JSON output needs no special datetime handling on the Python side.
"""

import json
import logging

import duckdb

from config import SITE_DATA_DIR, WAREHOUSE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s export: %(message)s")
logger = logging.getLogger(__name__)

INCIDENT_WINDOW_DAYS = 30
TREND_WINDOW_DAYS = 90
CATEGORY_WINDOW_DAYS = 7
HEATMAP_WINDOW_DAYS = 90

NOW = "CAST(now() AS TIMESTAMP)"


def _write_json(name: str, data) -> None:
    path = SITE_DATA_DIR / name
    path.write_text(json.dumps(data, indent=2))
    logger.info("Wrote %s", path)


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
    today_count, last_7d, prev_7d, pct_missing_coords = con.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE CAST(occurred_at AS DATE) = current_date),
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 7 DAY),
            COUNT(*) FILTER (WHERE occurred_at >= {NOW} - INTERVAL 14 DAY
                                AND occurred_at < {NOW} - INTERVAL 7 DAY),
            100.0 * COUNT(*) FILTER (WHERE latitude IS NULL) / COUNT(*)
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


def _incidents_geojson(con) -> dict:
    rows = _rows_as_dicts(con, f"""
        SELECT
            incident_key, jurisdiction, offense_raw, offense_category,
            severity_weight, strftime(occurred_at, '%Y-%m-%dT%H:%M:%S') AS occurred_at,
            case_number, area_name, block_address, latitude, longitude
        FROM marts.fct_incidents
        WHERE occurred_at >= {NOW} - INTERVAL {INCIDENT_WINDOW_DAYS} DAY
          AND latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    features = []
    for record in rows:
        lat, lon = record.pop("latitude"), record.pop("longitude")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": record,
        })
    return {"type": "FeatureCollection", "features": features}


def _trends_daily(con) -> list[dict]:
    return _rows_as_dicts(con, f"""
        SELECT jurisdiction, CAST(occurred_date AS VARCHAR) AS date, SUM(incident_count) AS count
        FROM marts.daily_counts
        WHERE occurred_date >= current_date - {TREND_WINDOW_DAYS}
        GROUP BY 1, 2
        ORDER BY 2, 1
    """)


def _trends_category(con) -> list[dict]:
    return _rows_as_dicts(con, f"""
        SELECT
            jurisdiction, offense_category,
            COALESCE(SUM(incident_count) FILTER (
                WHERE occurred_date >= current_date - {CATEGORY_WINDOW_DAYS}), 0) AS count,
            COALESCE(SUM(incident_count) FILTER (
                WHERE occurred_date >= current_date - 2 * {CATEGORY_WINDOW_DAYS}
                  AND occurred_date < current_date - {CATEGORY_WINDOW_DAYS}), 0) AS prev_count
        FROM marts.daily_counts
        WHERE occurred_date >= current_date - 2 * {CATEGORY_WINDOW_DAYS}
        GROUP BY 1, 2
        ORDER BY 3 DESC
    """)


def _trends_heatmap(con) -> list[dict]:
    return _rows_as_dicts(con, f"""
        SELECT
            dayofweek(occurred_at) AS weekday,
            hour(occurred_at)      AS hour,
            COUNT(*)               AS count
        FROM marts.fct_incidents
        WHERE occurred_at >= {NOW} - INTERVAL {HEATMAP_WINDOW_DAYS} DAY
        GROUP BY 1, 2
        ORDER BY 1, 2
    """)


def run() -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    _write_json("summary.json", _summary(con))
    _write_json("incidents.geojson", _incidents_geojson(con))
    _write_json("trends_daily.json", _trends_daily(con))
    _write_json("trends_category.json", _trends_category(con))
    _write_json("trends_heatmap.json", _trends_heatmap(con))

    con.close()
    logger.info("Site data export complete")


if __name__ == "__main__":
    run()
