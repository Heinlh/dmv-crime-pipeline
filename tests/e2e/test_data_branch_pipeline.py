"""The whole pipeline, run the way CI runs it: each "day" starts from a
fresh shallow clone, checks the data branch out, runs run_pipeline.main()
against the faked agencies, and saves the branch. Nothing carries over
between days except what the data branch holds."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import config
import run_pipeline
from export import render_og_card
from scripts.migrate_raw_zone import migrate
from tests.fakes import UNLISTED, build_world, iso

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "datastore.sh"
IDENTITY = {"GIT_AUTHOR_NAME": "ci", "GIT_AUTHOR_EMAIL": "ci@example.invalid",
            "GIT_COMMITTER_NAME": "ci", "GIT_COMMITTER_EMAIL": "ci@example.invalid"}


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **IDENTITY}).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    sh(tmp_path, "git", "init", "--quiet", "--initial-branch=main", str(seed))
    (seed / ".gitignore").write_text(".datastore/\n")
    sh(seed, "git", "add", ".gitignore")
    sh(seed, "git", "commit", "--quiet", "-m", "main")
    sh(seed, "git", "push", "--quiet", str(bare), "main")
    return bare


class Day:
    """One CI run: fresh clone, data branch opened, private scratch dirs."""

    def __init__(self, remote: Path, name: str, monkeypatch):
        self.root = remote.parent / name
        sh(remote.parent, "git", "clone", "--quiet", "--depth=1", f"file://{remote}", str(self.root))
        self.exists = sh(self.root, "bash", str(SCRIPT), "open", ".datastore") == "exists=true"
        self.store = self.root / ".datastore"
        self.site = self.root / "site_data"
        monkeypatch.setattr(config, "RAW_DIR", self.store / "raw")
        monkeypatch.setattr(config, "STATE_PATH", self.store / "state" / "watermarks.json")
        monkeypatch.setattr(config, "WAREHOUSE_PATH", self.root / "warehouse" / "crime.duckdb")
        monkeypatch.setattr(config, "SITE_DATA_DIR", self.site)
        monkeypatch.setattr(render_og_card, "OG_DIR", self.root / "og")

    def save(self, message: str):
        sh(self.root, "bash", str(SCRIPT), "save", ".datastore", message)

    def incident(self, key: str) -> dict:
        payload = json.loads((self.site / "incidents.json").read_text())
        rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
        return next(r for r in rows if r["incident_key"] == key)

    def incident_keys(self) -> set:
        payload = json.loads((self.site / "incidents.json").read_text())
        index = payload["columns"].index("incident_key")
        return {row[index] for row in payload["rows"]}


def branch_frames(store: Path, source: str) -> pd.DataFrame:
    files = sorted((store / "raw" / source).glob("*/*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def test_three_days_through_the_data_branch(remote, monkeypatch):
    world = build_world().install(monkeypatch)

    # day 1: no data branch yet; full backfill against the fakes
    day1 = Day(remote, "day1", monkeypatch)
    assert not day1.exists
    assert run_pipeline.main() == 0
    day1.save("data: run 1")
    first_keys = day1.incident_keys()
    assert {"moco-201300001", "pgc-PG26000101", "dc-26150001",
            "fairfax-20260330001", "pwc-PD260009001"} <= first_keys

    # day 2: a new Montgomery incident is published overnight
    fresh = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    world.publish(config.MOCO["base_url"], {
        "incident_id": "201300099", "offence_code": "1200", "case_number": "26059999",
        "start_date": iso(fresh), "crimename2": "Robbery", "district": "WHEATON",
        "location": "11200 BLK  GEORGIA AVE", "address_number": "11234",
        "latitude": "39.0398", "longitude": "-77.0552"})
    day2 = Day(remote, "day2", monkeypatch)
    assert day2.exists, "day 2 must find day 1's branch"
    world.calls.clear()
    assert run_pipeline.main() == 0
    day2.save("data: run 2")
    # incremental: day 2 asked Montgomery only for rows updated since day 1
    where = next(p["$where"] for u, p in world.calls if u == config.MOCO["base_url"])
    assert where.startswith(":updated_at > '")
    # day 2's site has day 1's incidents (from the branch) plus the new one
    assert day2.incident_keys() == first_keys | {"moco-201300099"}

    # day 3: rebuild from a fresh clone with every agency unreachable, to
    # prove the branch alone reproduces the site
    def unreachable(*a, **k):
        raise ConnectionError("agency down")
    for module in ("moco", "pgc", "dc", "fairfax", "pwc"):
        monkeypatch.setattr(f"extractors.{module}.request_json", unreachable)
    monkeypatch.setattr("extractors.base.request_json", unreachable)
    day3 = Day(remote, "day3", monkeypatch)
    assert run_pipeline.main() == 1, "extractor failures are still reported"
    assert day3.incident_keys() == day2.incident_keys()

    # the branch never held a field outside the allowlists, on any day
    for source in config.SOURCES:
        cols = set(branch_frames(day3.store, source).columns)
        assert cols <= set(config.allowed_fields(source)), (source, cols)
        assert not cols & UNLISTED[source]


def test_first_run_migrates_the_legacy_cache(remote, monkeypatch, tmp_path):
    """No data branch, but a legacy cache zone: seed from it, then run."""
    build_world().install(monkeypatch)

    legacy = tmp_path / "legacy"
    batch = legacy / "pgc" / "extract_date=2026-09-01"
    batch.mkdir(parents=True)
    pd.DataFrame([{
        "incident_case_id": "PG25999999", "date": "2026-08-30T10:00:00.000",
        "clearance_code_inc_type": "ROBBERY", "street_number": "6100 BLOCK",
        "street_address": "6163 OXON HILL RD SB",
        "latitude": "38.80562247335911", "longitude": "-76.98342069983482",
        "location": "(38.80562247335911,-76.98342069983482)",
    }]).astype("string").to_parquet(batch / "pgc_2026-09-01_110801.parquet", index=False)
    (tmp_path / "wm.json").write_text(json.dumps({"pgc": "2026-08-30T10:00:00"}))

    day1 = Day(remote, "day1", monkeypatch)
    assert not day1.exists
    migrate(legacy, tmp_path / "wm.json", day1.store)
    assert run_pipeline.main() == 0
    day1.save("data: run 1 (seeded)")

    day2 = Day(remote, "day2", monkeypatch)
    pgc = branch_frames(day2.store, "pgc").set_index("incident_case_id")
    # the legacy record survived, coarsened, with its extraction time recovered
    assert pgc.loc["PG25999999", "block_address"] == "6100 BLOCK OXON HILL RD SB"
    assert pgc.loc["PG25999999", config.EXTRACTED_AT] == "2026-09-01T11:08:01.000000Z"
    assert "street_address" not in pgc.columns
    # and the exact address exists nowhere in the branch's files
    for path in (day2.store / "raw").rglob("*.parquet"):
        assert b"6163 OXON HILL" not in path.read_bytes()
    assert (day2.store / "README.md").exists()


def test_warehouse_reads_the_branch_not_the_checkout(remote, monkeypatch):
    """With the data branch configured, data/raw in the checkout is ignored."""
    build_world().install(monkeypatch)
    day1 = Day(remote, "day1", monkeypatch)
    stray = Path(config.PROJECT_ROOT) / "data" / "raw"
    before = sorted(stray.rglob("*.parquet")) if stray.exists() else []
    assert run_pipeline.main() == 0
    after = sorted(stray.rglob("*.parquet")) if stray.exists() else []
    assert after == before, "nothing may be written to the checkout's data/raw"
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM marts.fct_incidents").fetchone()[0] == 8
    con.close()


def test_late_and_corrected_records_reach_the_site(remote, monkeypatch):
    """Findings 1 and 2 across real CI days: a Montgomery report published
    weeks after the offense, a Prince William offense added long after it
    occurred, and a Montgomery reclassification, each appearing on a later
    day, all reach the site, and survive a rebuild from the branch alone."""
    from tests.fakes import epoch_ms
    world = build_world().install(monkeypatch)
    day1 = Day(remote, "day1", monkeypatch)
    assert run_pipeline.main() == 0
    day1.save("data: run 1")
    assert day1.incident("moco-201300001")["offense_category"] == "vehicle"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    world.publish(config.MOCO["base_url"], {
        "incident_id": "201300777", "offence_code": "0501", "start_date": iso(now - timedelta(days=20)),
        "crimename2": "Burglary/Breaking and Entering", "location": "800 BLK  ELLSWORTH DR",
        "latitude": "38.9967", "longitude": "-77.0261"})
    world.publish(config.MOCO["base_url"], {
        **{k: v for k, v in world.socrata[config.MOCO["base_url"]]["records"][0].items()
           if not k.startswith(":")},
        "crimename2": "Robbery", "crimename3": "ROBBERY - STREET"},
        replace=lambda r: r["incident_id"] == "201300001")
    world.publish(f"{config.PWC_VA['base_url']}/0", {"attributes": {
        "InstanceID": "pwc-77", "CaseNo": "PD260007777", "OccurredOn": epoch_ms(now - timedelta(days=25)),
        "BlockAddress": "4300 Dale Blvd", "City": "DALE CITY", "IBRCode": "BURGLARY",
        "CrimeCategory": "Burglary"}, "geometry": {"x": -77.31, "y": 38.64}})

    day2 = Day(remote, "day2", monkeypatch)
    assert run_pipeline.main() == 0
    day2.save("data: run 2")
    assert {"moco-201300777", "pwc-PD260007777"} <= day2.incident_keys()
    assert day2.incident("moco-201300001")["offense_category"] == "violent"

    # a fresh clone with the agencies down rebuilds the same thing
    for module in ("moco", "pgc", "dc", "fairfax", "pwc"):
        monkeypatch.setattr(f"extractors.{module}.request_json", ConnectionError)
    monkeypatch.setattr("extractors.base.request_json", ConnectionError)
    day3 = Day(remote, "day3", monkeypatch)
    run_pipeline.main()
    assert day3.incident_keys() == day2.incident_keys()
    assert day3.incident("moco-201300001")["offense_category"] == "violent"
