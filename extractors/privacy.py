"""Location coarsening applied before anything reaches the raw zone.

The project shows locations at block level, never sharper, and the raw
zone is published on the public `data` branch, so an agency field finer
than a block must be coarsened before it is landed rather than at
display time.

Prince George's County is the source that needs it. Its column names
mislead: `street_number` holds the block ("9700 BLOCK"), while
`street_address` holds the address itself, often with the exact house
number ("9759 GOOD LUCK RD"), and its coordinates are geocodes of that
address. So for PG County this module:

  - derives `block_address` by rounding the leading house number down to
    its hundred ("9700 BLOCK GOOD LUCK RD"); intersections and other
    addresses without a house number pass through unchanged
  - drops `street_address` and `location` (the same point as text)
  - rounds latitude and longitude to BLOCK_PRECISION decimal places

The other four sources already publish block-level locations: DC and
Fairfax offset their points to the block, Prince William maps to the
nearest 100 block, and Montgomery's `location` is a block address.
"""

import re

import pandas as pd

# 3 decimal places is about 110 m north-south and 85 m east-west at the
# DMV's latitude: roughly one city block.
BLOCK_PRECISION = 3

# a leading house number (with any suffix like "9759-A"), then an
# optional block marker the address may already carry, then the street
_HOUSE_NUMBER = re.compile(r"^\s*(\d+)\S*\s+(?:BLK\.?\s+|BLOCK\s+(?:OF\s+)?)?(.+?)\s*$",
                           re.IGNORECASE)


def block_from_address(address):
    """'9759 GOOD LUCK RD' -> '9700 BLOCK GOOD LUCK RD'. An address that
    already names its block ('7700 BLK LANDOVER RD') comes out in the same
    form. Addresses with no leading house number (intersections, place
    names) are returned stripped but otherwise unchanged; missing values
    stay missing."""
    if address is None or pd.isna(address):
        return None
    text = str(address).strip()
    if not text:
        return None
    match = _HOUSE_NUMBER.match(text)
    if not match:
        return text
    hundred = int(match.group(1)) // 100 * 100
    return f"{hundred} BLOCK {match.group(2)}"


def round_coordinate(value):
    """Round a coordinate (as published, usually a string) to
    BLOCK_PRECISION places; values that are not numbers become missing."""
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{round(number, BLOCK_PRECISION):.{BLOCK_PRECISION}f}"


def coarsen(source_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Return `frame` with any finer-than-block location coarsened. Only
    sources that need it are touched; the input is never modified."""
    if source_name != "pgc":
        return frame
    out = frame.copy()
    if "street_address" in out.columns:
        out["block_address"] = out["street_address"].map(block_from_address)
    for column in ("latitude", "longitude"):
        if column in out.columns:
            out[column] = out[column].map(round_coordinate)
    return out.drop(columns=[c for c in ("street_address", "location") if c in out.columns])
