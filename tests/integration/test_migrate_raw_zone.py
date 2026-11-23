"""The one-time migration of the legacy raw zone (scripts/migrate_raw_zone.py),
run over a legacy-shaped zone: every field the agencies returned, exact
PG County addresses, and no extraction stamps."""

import json

import pandas as pd
import pytest

import config
from scripts.migrate_raw_zone import main, migrate


def write_legacy(root, source, day, hhmmss, records):
    path = root / source / f"extract_date={day}" / f"{source}_{day}_{hhmmss}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).astype("string").to_parquet(path, index=False)
    return path


@pytest.fixture
def legacy(tmp_path):
    src = tmp_path / "legacy_raw"
    write_legacy(src, "moco", "2026-09-01", "110512", [
        {"incident_id": "1", "start_date": "2026-08-31T10:00:00.000", "crimename2": "Robbery",
         "location": "100 BLK  MAIN ST", "address_number": "123", "address_street": "MAIN",
         "geolocation_address": "123 MAIN ST", "latitude": "39.0", "longitude": "-77.1"},
    ])
    write_legacy(src, "moco", "2026-09-02", "110733", [
        {"incident_id": "1", "start_date": "2026-08-31T10:00:00.000", "crimename2": "Homicide",
         "location": "100 BLK  MAIN ST", "address_number": "123"},
        {"incident_id": "2", "start_date": "2026-09-01T09:00:00.000", "crimename2": "Theft"},
    ])
    write_legacy(src, "pgc", "2026-09-02", "110801", [
        {"incident_case_id": "PG1", "date": "2026-09-01T00:00:00.000",
         "street_number": "9700 BLOCK", "street_address": "9759 GOOD LUCK RD",
         "latitude": "38.98377172648907", "longitude": "-76.84168869256973",
         "location": "(38.98377172648907,-76.84168869256973)",
         "clearance_code_inc_type": "THEFT"},
    ])
    state = tmp_path / "legacy_state.json"
    state.write_text(json.dumps({"moco": "2026-09-01T09:00:00", "pgc": "2026-09-01T00:00:00"}))
    return src, state


def all_rows(dest, source):
    files = sorted((dest / "raw" / source).glob("*/*.parquet"))
    return files, pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def test_rows_and_batches_are_preserved(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    summary = migrate(src, state, dest)
    assert summary["moco"]["files"] == 2 and summary["moco"]["rows"] == 3
    assert summary["pgc"]["files"] == 1 and summary["pgc"]["rows"] == 1
    files, _ = all_rows(dest, "moco")
    # one migrated file per legacy batch, at the same relative path
    assert [f.relative_to(dest / "raw") for f in files] == \
           sorted(p.relative_to(src) for p in (src / "moco").glob("*/*.parquet"))


def test_unlisted_fields_are_stripped(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    summary = migrate(src, state, dest)
    _, moco = all_rows(dest, "moco")
    assert set(moco.columns) <= set(config.allowed_fields("moco"))
    assert {"address_number", "address_street", "geolocation_address"} <= summary["moco"]["dropped"]


def test_pgc_history_is_coarsened_to_the_block(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    migrate(src, state, dest)
    files, pgc = all_rows(dest, "pgc")
    row = pgc.iloc[0]
    assert row["block_address"] == "9700 BLOCK GOOD LUCK RD"
    assert (row["latitude"], row["longitude"]) == ("38.984", "-76.842")
    assert not {"street_address", "street_number", "location"} & set(pgc.columns)
    assert b"9759 GOOD LUCK" not in files[0].read_bytes()


def test_extraction_times_are_recovered_from_file_names(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    migrate(src, state, dest)
    _, moco = all_rows(dest, "moco")
    stamps = moco.groupby("incident_id")[config.EXTRACTED_AT].apply(sorted).to_dict()
    # incident 1 was fetched twice: the later batch is the later version
    assert stamps["1"] == ["2026-09-01T11:05:12.000000Z", "2026-09-02T11:07:33.000000Z"]
    assert stamps["2"] == ["2026-09-02T11:07:33.000000Z"]


def test_watermarks_and_readme_come_along(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    migrate(src, state, dest)
    assert json.loads((dest / "state" / "watermarks.json").read_text())["moco"] == "2026-09-01T09:00:00"
    assert "system of record" in (dest / "README.md").read_text()


def test_rerunning_gives_identical_data(tmp_path, legacy):
    src, state = legacy
    first, second = tmp_path / "a", tmp_path / "b"
    migrate(src, state, first)
    migrate(src, state, second)
    for source in ("moco", "pgc"):
        a = all_rows(first, source)[1].sort_values(list(all_rows(first, source)[1].columns))
        b = all_rows(second, source)[1].sort_values(list(all_rows(second, source)[1].columns))
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_missing_legacy_zone_seeds_an_empty_branch(tmp_path):
    dest = tmp_path / "datastore"
    assert migrate(tmp_path / "nothing", tmp_path / "none.json", dest) == {}
    assert (dest / "README.md").exists()
    assert not (dest / "raw").exists()


def test_command_line_entry_point(tmp_path, legacy):
    src, state = legacy
    dest = tmp_path / "datastore"
    assert main(["--src", str(src), "--state", str(state), "--dest", str(dest)]) == 0
    assert (dest / "raw" / "pgc").exists()
