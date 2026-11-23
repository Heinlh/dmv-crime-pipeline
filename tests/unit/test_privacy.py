"""Block-level coarsening of finer-than-block locations (extractors/privacy.py)."""

import pandas as pd
import pytest

from extractors.privacy import block_from_address, coarsen, round_coordinate


@pytest.mark.parametrize("address, expected", [
    # exact house numbers, as PG County publishes them, round down to the block
    ("9759 GOOD LUCK RD", "9700 BLOCK GOOD LUCK RD"),
    ("6163 OXON HILL RD SB", "6100 BLOCK OXON HILL RD SB"),
    ("9759-A GOOD LUCK RD", "9700 BLOCK GOOD LUCK RD"),
    # already on a block boundary
    ("9700 BUTTERFLY LN", "9700 BLOCK BUTTERFLY LN"),
    # an address that already names its block keeps one marker, not two
    ("7700 BLK LANDOVER RD", "7700 BLOCK LANDOVER RD"),
    ("7700 blk. landover rd", "7700 BLOCK landover rd"),
    ("1200 BLOCK OF H ST NE", "1200 BLOCK H ST NE"),
    # numbers under 100 fall in the zero block
    ("50 ELM ST", "0 BLOCK ELM ST"),
    # intersections and place names carry no house number: unchanged
    ("SANSBURY RD / RITCHIE MARLBORO RD SB", "SANSBURY RD / RITCHIE MARLBORO RD SB"),
    ("  UNIVERSITY BLVD  ", "UNIVERSITY BLVD"),
])
def test_block_from_address(address, expected):
    assert block_from_address(address) == expected


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA, "", "   "])
def test_block_from_address_keeps_missing_missing(missing):
    assert block_from_address(missing) is None


@pytest.mark.parametrize("number", [1, 99, 101, 9759, 12345])
def test_exact_house_number_never_survives(number):
    assert block_from_address(f"{number} MAIN ST") == f"{number // 100 * 100} BLOCK MAIN ST"


@pytest.mark.parametrize("value, expected", [
    ("38.98377172648907", "38.984"),
    ("-76.84168869256973", "-76.842"),
    (38.9, "38.900"),
    ("0", "0.000"),        # the transform nulls 0,0 geocodes later
    ("n/a", None),
    ("", None),
    (None, None),
])
def test_round_coordinate(value, expected):
    assert round_coordinate(value) == expected


def test_coarsen_pgc_derives_block_and_drops_exact_fields():
    frame = pd.DataFrame([{
        "incident_case_id": "PG1", "street_number": "9700 BLOCK",
        "street_address": "9759 GOOD LUCK RD",
        "latitude": "38.98377172648907", "longitude": "-76.84168869256973",
        "location": "(38.98377172648907,-76.84168869256973)",
    }])
    out = coarsen("pgc", frame)
    row = out.iloc[0]
    assert row["block_address"] == "9700 BLOCK GOOD LUCK RD"
    assert (row["latitude"], row["longitude"]) == ("38.984", "-76.842")
    assert "street_address" not in out.columns and "location" not in out.columns
    # the input is never modified in place
    assert frame.iloc[0]["street_address"] == "9759 GOOD LUCK RD"


@pytest.mark.parametrize("source", ["moco", "dc", "fairfax", "pwc"])
def test_coarsen_leaves_block_level_sources_untouched(source):
    frame = pd.DataFrame([{"latitude": "38.98377172648907", "street_address": "1 X ST"}])
    assert coarsen(source, frame) is frame
