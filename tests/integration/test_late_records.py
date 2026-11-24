"""Finding 1: records published late, corrected, or tied in the paging sort
were silently missed. Each test runs the real extractors against the faked
agencies, changes what an agency publishes, and runs again."""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import config
from extractors import dc, moco, pgc, pwc
from tests.fakes import epoch_ms, iso

MOCO_URL = config.MOCO["base_url"]
PWC_LAYER = f"{config.PWC_VA['base_url']}/{config.PWC_VA['layer_id']}"
NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def batches(paths, source) -> list[pd.DataFrame]:
    """Each landed batch, oldest first."""
    files = sorted((paths["RAW_DIR"] / source).glob("*/*.parquet"))
    return [pd.read_parquet(f) for f in files]


def queries(world, url):
    return [p for u, p in world.calls if u == url]


def moco_row(incident, code, crime, started):
    return {"incident_id": incident, "offence_code": code, "crimename2": crime,
            "start_date": iso(started), "location": "100 BLK  TEST ST"}


# ------------------------------------------------------------- Montgomery

def test_moco_picks_up_a_report_published_weeks_late(paths, world):
    moco.run()
    # an offense from a month ago, published only today: older than every
    # start_date already seen, which the old watermark never asked about
    world.publish(MOCO_URL, moco_row("201300500", "0501", "Burglary", NOW - timedelta(days=30)))
    moco.run()
    late = batches(paths, "moco")[-1]
    assert list(late["incident_id"]) == ["201300500"]


def test_moco_correction_to_one_row_lands_the_whole_incident(paths, world):
    world.publish(MOCO_URL, moco_row("201300600", "0901", "Assault", NOW - timedelta(days=2)))
    world.publish(MOCO_URL, moco_row("201300600", "2305", "Theft From Motor Vehicle",
                                     NOW - timedelta(days=2)))
    moco.run()
    world.calls.clear()
    # the county reclassifies one of the incident's two rows
    world.publish(MOCO_URL, moco_row("201300600", "0901", "Aggravated Assault", NOW - timedelta(days=2)),
                  replace=lambda r: r["incident_id"] == "201300600" and r["offence_code"] == "0901")
    moco.run()
    landed = batches(paths, "moco")[-1]
    # only the touched incident, and all of it (the untouched theft row too)
    assert set(landed["incident_id"]) == {"201300600"}
    assert sorted(landed["crimename2"]) == ["Aggravated Assault", "Theft From Motor Vehicle"]
    assert any(p.get("$where", "").startswith("incident_id in (") for p in queries(world, MOCO_URL))


def test_moco_quiet_day_lands_nothing_and_keeps_its_mark(paths, world):
    moco.run()
    mark = json.loads(paths["STATE_PATH"].read_text())[moco.STATE_KEY]
    count = len(batches(paths, "moco"))
    moco.run()  # the overlap re-sends rows; none changed
    assert len(batches(paths, "moco")) == count
    assert json.loads(paths["STATE_PATH"].read_text())[moco.STATE_KEY] == mark


# ------------------------------------------------------- Prince George's

def test_pgc_fetches_nothing_while_the_dataset_is_unchanged(paths, world):
    pgc.run()
    world.calls.clear()
    pgc.run()
    resource_calls = [u for u, _ in world.calls if "/resource/" in u]
    assert resource_calls == [], "unchanged rowsUpdatedAt: no rows requested"


def test_pgc_republish_lands_only_what_changed(paths, world):
    pgc.run()
    world.calls.clear()
    # the weekly republish: one late report, one reclassification
    world.publish(config.PGC["base_url"], {
        "incident_case_id": "PG26000777", "date": iso(NOW - timedelta(days=40)),
        "clearance_code_inc_type": "BURGLARY", "street_address": "4410 KNOX RD"})
    world.publish(config.PGC["base_url"], {
        "incident_case_id": "PG26000101", "date": iso(NOW - timedelta(days=4)),
        "clearance_code_inc_type": "STOLEN VEHICLE", "street_address": "9759 GOOD LUCK RD"},
        replace=lambda r: r["incident_case_id"] == "PG26000101")
    pgc.run()
    landed = batches(paths, "pgc")[-1].set_index("incident_case_id")
    assert set(landed.index) == {"PG26000777", "PG26000101"}
    assert landed.loc["PG26000101", "clearance_code_inc_type"] == "STOLEN VEHICLE"
    assert landed.loc["PG26000777", "block_address"] == "4400 BLOCK KNOX RD"
    # the frozen 2017-2023 dataset is read once, never again
    assert not [u for u, _ in world.calls if u == config.PGC["historical_url"]]


# -------------------------------------------------------- Prince William

def pwc_feature(case, offense, occurred, instance):
    return {"attributes": {"InstanceID": instance, "CaseNo": case, "OccurredOn": epoch_ms(occurred),
                           "BlockAddress": "4300 Dale Blvd", "City": "DALE CITY",
                           "IBRCode": offense, "CrimeCategory": offense},
            "geometry": {"x": -77.31, "y": 38.64}}


def test_pwc_skips_an_unchanged_layer(paths, world):
    pwc.run()
    world.calls.clear()
    pwc.run()
    assert not [u for u, _ in world.calls if u.endswith("/query")]


def test_pwc_picks_up_an_old_offense_added_today(paths, world):
    pwc.run()
    # occurred 60 days ago, well past the old 7 day overlap
    world.publish(PWC_LAYER, pwc_feature("PD260001234", "BURGLARY", NOW - timedelta(days=60), "pwc-9"))
    pwc.run()
    assert list(batches(paths, "pwc")[-1]["CaseNo"]) == ["PD260001234"]


# --------------------------------------------------------------- paging

def _crowd(world, n):
    """n DC incidents all reported at the same instant: one big tie."""
    moment = epoch_ms(NOW - timedelta(hours=3))
    layer = f"{config.DC['base_url']}/41"
    for i in range(n):
        world.publish(layer, {"attributes": {
            "CCN": f"2619{i:04d}", "REPORT_DAT": moment, "OFFENSE": "THEFT/OTHER",
            "BLOCK": "600 - 699 BLOCK OF H STREET NE", "LATITUDE": 38.9, "LONGITUDE": -77.0}})


def test_tied_rows_page_exactly_once(paths, world, monkeypatch):
    monkeypatch.setitem(config.DC, "page_size", 3)
    _crowd(world, 40)
    dc.run()
    ccns = pd.concat(batches(paths, "dc"))["CCN"]
    tied = ccns[ccns.str.startswith("2619")]
    assert len(tied) == 40 and tied.is_unique


def test_the_tie_check_would_catch_a_non_unique_sort(paths, world, monkeypatch):
    """Guard on the test above: without the object id tiebreak the fake
    server duplicates and drops tied rows, as the real ones did."""
    monkeypatch.setitem(config.DC, "page_size", 3)
    monkeypatch.setattr(dc, "arcgis_order", lambda field, oid: f"{field} ASC")
    _crowd(world, 40)
    records, _ = dc.extract()
    tied = {r["CCN"] for r in records if r["CCN"].startswith("2619")}
    assert len(tied) < 40


def test_moco_ties_on_updated_at_page_exactly_once(paths, world, monkeypatch):
    """A Socrata bulk publish stamps many rows with one :updated_at."""
    monkeypatch.setitem(config.MOCO, "page_size", 4)
    for i in range(30):
        world.publish(MOCO_URL, moco_row(f"2013009{i:02d}", "2305", "Theft", NOW - timedelta(days=1)))
    for row in world.socrata[MOCO_URL]["records"]:
        row[":updated_at"] = "2026-01-01T00:00:01.000Z"
    moco.run()
    ids = pd.concat(batches(paths, "moco"))["incident_id"]
    assert ids.is_unique and len(ids) == 32


# ------------------------------------------------- state after landing

@pytest.mark.parametrize("module, key", [(moco, moco.STATE_KEY), (pgc, pgc.STATE_KEY),
                                         (pwc, pwc.STATE_KEY), (dc, "dc")])
def test_state_is_not_advanced_when_landing_fails(paths, world, monkeypatch, module, key):
    def broken(records, source):
        raise OSError("disk full")
    monkeypatch.setattr(module, "land_changed", broken)
    with pytest.raises(OSError):
        module.run()
    state = json.loads(paths["STATE_PATH"].read_text()) if paths["STATE_PATH"].exists() else {}
    assert key not in state, "a failed landing must leave the next run to re-pull"
