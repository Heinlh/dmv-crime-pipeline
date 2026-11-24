"""Finding 2: which version of an incident the warehouse shows. Finding 3:
the bounds of the history the site reports. Batches are landed directly
with land_changed, then loaded, transformed and exported as in a run."""

import json
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

import config
from export import export_site_data
from extractors.base import land_changed
from load import load_duckdb

NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
WHEN = (NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000")


def moco(inc, code, crime, start=WHEN):
    return {"incident_id": inc, "offence_code": code, "crimename2": crime, "start_date": start,
            "location": "100 BLK  TEST ST", "latitude": "39.08", "longitude": "-77.15"}


def ms(moment):
    return str(int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000))


def build():
    load_duckdb.run()
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    rows = {k: (cat, sev, raw) for k, cat, sev, raw in con.execute(
        "SELECT incident_key, offense_category, severity_weight, offense_raw FROM marts.fct_incidents"
    ).fetchall()}
    con.close()
    return rows


# --------------------------------------------------------------- finding 2

def test_a_later_correction_wins_even_when_less_severe(paths):
    land_changed([moco("501", "1200", "Robbery")], "moco")
    land_changed([moco("501", "2305", "Theft From Motor Vehicle")], "moco")
    assert build()["moco-501"][0] == "vehicle"


def test_the_most_severe_offense_of_the_latest_version_represents_it(paths):
    land_changed([moco("502", "2305", "Theft From Motor Vehicle"),
                  moco("502", "0901", "Murder and Nonnegligent Manslaughter"),
                  moco("502", "1300", "Assault")], "moco")
    assert build()["moco-502"][:2] == ("homicide", 10)


def test_a_row_dropped_by_the_agency_drops_from_the_warehouse(paths):
    land_changed([moco("503", "0901", "Murder and Nonnegligent Manslaughter"),
                  moco("503", "2305", "Theft From Motor Vehicle")], "moco")
    # republished whole without the homicide row (a coding error withdrawn)
    land_changed([moco("503", "2305", "Theft From Motor Vehicle")], "moco")
    assert build()["moco-503"][0] == "vehicle"


def test_equal_severity_ties_resolve_the_same_way_regardless_of_landing_order(paths, tmp_path, monkeypatch):
    a = moco("504", "2305", "Theft From Motor Vehicle")
    b = moco("504", "2399", "Theft From Building")
    land_changed([a, b], "moco")
    first = build()["moco-504"]
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw2")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", tmp_path / "wh2" / "crime.duckdb")
    land_changed([b, a], "moco")
    assert build()["moco-504"] == first


def test_rebuilding_twice_gives_identical_marts(paths):
    land_changed([moco(str(600 + i), "2305", "Theft") for i in range(5)]
                 + [moco("600", "1200", "Robbery")], "moco")
    load_duckdb.run()
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    once = con.execute("SELECT * FROM marts.fct_incidents ORDER BY incident_key").fetchall()
    con.close()
    load_duckdb.run()
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    assert con.execute("SELECT * FROM marts.fct_incidents ORDER BY incident_key").fetchall() == once
    con.close()


def test_dc_reclassification_takes_the_latest(paths):
    rec = {"CCN": "26113502", "REPORT_DAT": ms(NOW - timedelta(days=2)),
           "BLOCK": "1 - 99 BLOCK OF K STREET NE", "LATITUDE": "38.9", "LONGITUDE": "-77.0"}
    land_changed([{**rec, "OFFENSE": "MOTOR VEHICLE THEFT"}], "dc")
    land_changed([{**rec, "OFFENSE": "THEFT F/AUTO"}], "dc")
    assert build()["dc-26113502"][2] == "THEFT F/AUTO"


def test_fairfax_keeps_the_assault_when_a_later_pull_resends_only_property(paths):
    def ffx(uid, offense):
        return {"UniqueID": uid, "IncidentNumber": "20262880232",
                "DateReported": ms(NOW - timedelta(days=2)),
                "ViolationCodeReference_Descript": offense,
                "latitude": "38.8", "longitude": "-77.2"}
    land_changed([ffx("u1", "ASSAULT - AGGRAVATED (13A)"), ffx("u2", "EXTORTION (210)")], "fairfax")
    land_changed([ffx("u3", "EXTORTION (210)")], "fairfax")
    assert build()["fairfax-20262880232"][0] == "violent"


# --------------------------------------------------------------- finding 3

@pytest.fixture
def summary(paths):
    land_changed([
        moco("701", "2305", "Theft", "2016-07-02T10:00:00.000"),
        moco("702", "2305", "Theft", WHEN),
        # DC carries offenses dated as far back as 1940, and agencies
        # sometimes publish a date in the future by typo
        moco("703", "2305", "Theft", "1940-02-17T00:00:00.000"),
        moco("704", "2305", "Theft", (NOW + timedelta(days=400)).strftime("%Y-%m-%dT00:00:00.000")),
    ], "moco")
    load_duckdb.run()
    export_site_data.run()
    return json.loads((config.SITE_DATA_DIR / "summary.json").read_text())


def test_history_starts_in_2016_not_1940(summary):
    assert summary["data_start_date"] == "2016-07-02"


def test_out_of_range_records_are_counted_separately(summary):
    assert summary["total_records"] == 2
    assert summary["records_outside_history"] == 2
    assert sum(summary["records_by_jurisdiction"].values()) == summary["total_records"]


def test_trends_start_inside_the_history(paths, summary):
    trends = json.loads((config.SITE_DATA_DIR / "trends.json").read_text())
    dates = sorted(row[0] for row in trends["rows"])
    assert dates[0] == "2016-07-02"
    assert dates[-1] <= NOW.date().isoformat()
    assert sum(row[3] for row in trends["rows"]) == summary["total_records"]
