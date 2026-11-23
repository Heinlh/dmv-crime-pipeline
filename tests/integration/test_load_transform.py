"""Loader and transform over what the real extractors landed: the field
allowlists must still feed every column sql/transform.sql binds."""

import duckdb
import pytest

import run_pipeline
from load import load_duckdb


@pytest.fixture
def warehouse(paths, world):
    for module in (run_pipeline.moco, run_pipeline.dc, run_pipeline.pgc,
                   run_pipeline.fairfax, run_pipeline.pwc):
        module.run()
    load_duckdb.run()
    con = duckdb.connect(str(paths["WAREHOUSE_PATH"]), read_only=True)
    yield con
    con.close()


def rows(con, sql):
    return con.execute(sql).fetchall()


def test_every_source_reaches_the_fact_table(warehouse):
    counts = dict(rows(warehouse, "SELECT jurisdiction, COUNT(*) FROM marts.fct_incidents GROUP BY 1"))
    assert counts == {"moco": 2, "pgc": 2, "dc": 2, "fairfax": 1, "pwc": 1}


def test_raw_tables_hold_only_allowlisted_columns(warehouse):
    import config
    for source, table in load_duckdb.SOURCES.items():
        cols = {r[0] for r in rows(warehouse, f"DESCRIBE {table}")}
        # DuckDB adds the extract_date=... directory as a hive partition
        # column: derived from the path, not a field in any file
        cols.discard("extract_date")
        assert cols == set(config.allowed_fields(source)), (source, cols ^ set(config.allowed_fields(source)))


def test_taxonomy_and_fields_survive_the_allowlist(warehouse):
    got = {k: (cat, sev) for k, cat, sev in rows(
        warehouse, "SELECT incident_key, offense_category, severity_weight FROM marts.fct_incidents")}
    assert got["moco-201300002"] == ("homicide", 10)
    assert got["moco-201300001"][0] == "vehicle"
    assert got["dc-26150001"] == ("violent", 7)
    assert got["pgc-PG26000102"][0] == "violent"
    assert got["fairfax-20260330001"] == ("violent", 8)
    assert got["pwc-PD260009001"][0] == "vehicle"


def test_pgc_shows_the_block_not_the_house(warehouse):
    addr, lat, lon = rows(warehouse, """
        SELECT block_address, latitude, longitude FROM marts.fct_incidents
        WHERE incident_key = 'pgc-PG26000101'""")[0]
    assert addr == "9700 BLOCK GOOD LUCK RD"
    assert (lat, lon) == (38.984, -76.842)
    hits = rows(warehouse, "SELECT COUNT(*) FROM marts.fct_incidents WHERE block_address LIKE '%9759%'")
    assert hits == [(0,)]


def test_moco_block_address_is_untouched(warehouse):
    assert rows(warehouse, """SELECT block_address FROM marts.fct_incidents
                              WHERE incident_key = 'moco-201300001'""") == [("100 BLK  MONROE ST",)]
