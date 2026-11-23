"""Recovering when a legacy batch was fetched, from its file name."""

from pathlib import Path

import pytest

from scripts.migrate_raw_zone import extracted_at_from_path


@pytest.mark.parametrize("name, expected", [
    ("moco_2026-10-03_020558.parquet", "2026-10-03T02:05:58.000000Z"),
    ("fairfax_2025-12-31_235959.parquet", "2025-12-31T23:59:59.000000Z"),
    # a collision suffix from the new land_raw does not confuse it
    ("dc_2026-11-24_110000_2.parquet", "2026-11-24T11:00:00.000000Z"),
])
def test_time_comes_from_the_file_name(name, expected):
    path = Path("raw") / name.split("_")[0] / "extract_date=1999-01-01" / name
    assert extracted_at_from_path(path) == expected


def test_falls_back_to_the_partition_date():
    path = Path("raw/pgc/extract_date=2026-02-03/something.parquet")
    assert extracted_at_from_path(path) == "2026-02-03T00:00:00.000000Z"


def test_refuses_to_guess():
    with pytest.raises(ValueError):
        extracted_at_from_path(Path("raw/pgc/misc/something.parquet"))
