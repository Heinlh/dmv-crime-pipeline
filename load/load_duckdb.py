"""Load the raw parquet zone into DuckDB and run the transform.

Approach: the raw tables are fully rebuilt from every parquet file
landed so far (read_parquet with union_by_name tolerates columns being
added by the source over time). The transform then dedupes and upserts
into marts.fct_incidents. Both steps are idempotent, so re-running the
pipeline is always safe.
"""

import logging

import duckdb

from config import PROJECT_ROOT, RAW_DIR, WAREHOUSE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s loader: %(message)s")
logger = logging.getLogger(__name__)

SOURCES = {
    "moco": "raw.moco_incidents",
    "dc": "raw.dc_incidents",
}


def run() -> None:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))

    con.execute((PROJECT_ROOT / "sql" / "schema.sql").read_text())
    logger.info("Schema applied")

    loaded_any = False
    for source, table in SOURCES.items():
        glob = RAW_DIR / source / "*" / "*.parquet"
        if not list((RAW_DIR / source).glob("*/*.parquet")):
            logger.warning("No raw files for %s yet, skipping", source)
            continue
        con.execute(f"""
            CREATE OR REPLACE TABLE {table} AS
            SELECT * FROM read_parquet('{glob.as_posix()}', union_by_name=true)
        """)
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        logger.info("Rebuilt %s: %d rows", table, count)
        loaded_any = True

    if loaded_any:
        # Guard the transform: only run source blocks whose raw table exists.
        transform_sql = "\n".join(
            line for line in (PROJECT_ROOT / "sql" / "transform.sql").read_text().splitlines()
            if not line.lstrip().startswith("--")
        )
        existing = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw'"
            ).fetchall()
        }
        for statement in transform_sql.split(";"):
            if not statement.strip():
                continue
            if "raw.moco_incidents" in statement and "moco_incidents" not in existing:
                continue
            if "raw.dc_incidents" in statement and "dc_incidents" not in existing:
                continue
            con.execute(statement)
        total = con.execute("SELECT COUNT(*) FROM marts.fct_incidents").fetchone()[0]
        by_source = con.execute(
            "SELECT jurisdiction, COUNT(*) FROM marts.fct_incidents GROUP BY 1 ORDER BY 1"
        ).fetchall()
        logger.info("fct_incidents: %d total rows (%s)", total,
                     ", ".join(f"{j}={n}" for j, n in by_source))

    con.close()


if __name__ == "__main__":
    run()
