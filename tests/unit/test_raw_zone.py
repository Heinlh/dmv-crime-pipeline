"""The raw-zone boundary: what may be requested, and what may be landed."""

import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import config
from extractors.base import (extraction_stamp, land_raw, prepare_raw_frame,
                             select_fields)
from tests.fakes import UNLISTED

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


# ------------------------------------------------------------ allowlists

@pytest.mark.parametrize("source", sorted(config.SOURCES))
def test_allowlists_exclude_every_unlisted_published_field(source):
    assert not set(config.SOURCES[source]["fields"]) & UNLISTED[source]


def test_pgc_exact_address_is_fetch_only_never_landed():
    assert config.SOURCES["pgc"]["fetch_only"] == ["street_address"]
    assert "street_address" not in config.SOURCES["pgc"]["fields"]
    assert "block_address" in config.SOURCES["pgc"]["fields"]


@pytest.mark.parametrize("source", sorted(config.SOURCES))
def test_allowed_fields_end_with_the_lineage_column(source):
    assert config.allowed_fields(source)[-1] == config.EXTRACTED_AT


# --------------------------------------------------------- select_fields

def test_select_fields_intersects_with_the_live_schema_in_allowlist_order():
    live = ["longitude", "incident_id", "address_number", "start_date", "latitude"]
    assert select_fields("moco", live) == ["incident_id", "start_date", "latitude", "longitude"]


def test_select_fields_skips_derived_fields():
    live = ["UniqueID", "DateReported", "latitude"]  # pretend a layer had a lat field
    chosen = select_fields("fairfax", live, derived=("latitude", "longitude"))
    assert chosen == ["UniqueID", "DateReported"]


def test_select_fields_adds_fetch_only_inputs():
    live = ["incident_case_id", "date", "street_number", "street_address", "location"]
    chosen = select_fields("pgc", live, derived=("block_address",))
    assert chosen == ["incident_case_id", "date", "street_address"]


# ------------------------------------------------------ prepare_raw_frame

def test_prepare_drops_unlisted_fields_and_stringifies():
    frame = pd.DataFrame([{"CCN": "1", "LATITUDE": 38.9, "SHIFT": "DAY", "OBJECTID": 7}])
    out = prepare_raw_frame("dc", frame)
    assert list(out.columns) == ["CCN", "LATITUDE"]
    assert out["LATITUDE"].iloc[0] == "38.9"
    assert all(str(dtype) == "string" for dtype in out.dtypes)


def test_prepare_keeps_an_existing_lineage_column():
    frame = pd.DataFrame([{"CCN": "1", config.EXTRACTED_AT: "2026-01-01T00:00:00.000000Z"}])
    assert config.EXTRACTED_AT in prepare_raw_frame("dc", frame).columns


def test_prepare_coarsens_pgc_before_projecting():
    frame = pd.DataFrame([{"incident_case_id": "PG1", "street_address": "9759 GOOD LUCK RD",
                           "street_number": "9700 BLOCK", "latitude": "38.98377"}])
    out = prepare_raw_frame("pgc", frame)
    assert out.iloc[0]["block_address"] == "9700 BLOCK GOOD LUCK RD"
    assert out.iloc[0]["latitude"] == "38.984"
    assert not {"street_address", "street_number"} & set(out.columns)


# ------------------------------------------------------- extraction_stamp

def test_extraction_stamp_is_fixed_width_utc():
    stamp = extraction_stamp(datetime(2026, 3, 4, 5, 6, 7, 89, tzinfo=timezone.utc))
    assert stamp == "2026-03-04T05:06:07.000089Z"
    assert STAMP.match(stamp)


def test_extraction_stamps_sort_as_time():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    moments = [base + timedelta(microseconds=n) for n in (1, 10, 999_999)] + \
              [base + timedelta(days=n) for n in (1, 40, 400)]
    stamps = [extraction_stamp(m) for m in moments]
    assert stamps == sorted(stamps)


def test_extraction_stamp_converts_other_timezones_to_utc():
    est = timezone(timedelta(hours=-5))
    assert extraction_stamp(datetime(2026, 1, 1, 20, 0, tzinfo=est)) == "2026-01-02T01:00:00.000000Z"


# ---------------------------------------------------------------- land_raw

def test_land_raw_writes_allowlisted_stamped_strings(paths):
    out = land_raw([{"CCN": "1", "OFFENSE": "ROBBERY", "SHIFT": "DAY", "LATITUDE": 38.9}], "dc")
    frame = pd.read_parquet(out)
    assert list(frame.columns) == ["CCN", "OFFENSE", "LATITUDE", config.EXTRACTED_AT]
    assert STAMP.match(frame[config.EXTRACTED_AT].iloc[0])
    # partition, file name and row stamp come from the same instant
    day = frame[config.EXTRACTED_AT].iloc[0][:10]
    assert out.parent.name == f"extract_date={day}"
    assert out.name.startswith(f"dc_{day}_")
    assert out.is_relative_to(paths["RAW_DIR"] / "dc")


def test_land_raw_skips_an_empty_batch(paths):
    assert land_raw([], "moco") is None
    assert not (paths["RAW_DIR"] / "moco").exists()


def test_land_raw_never_overwrites_a_same_second_batch(paths, monkeypatch):
    frozen = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr("extractors.base.datetime", Frozen)
    first = land_raw([{"CCN": "1"}], "dc")
    second = land_raw([{"CCN": "2"}], "dc")
    assert first != second
    assert {pd.read_parquet(p)["CCN"].iloc[0] for p in (first, second)} == {"1", "2"}
