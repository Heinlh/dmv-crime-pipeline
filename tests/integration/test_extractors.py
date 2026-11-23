"""Every real extractor, run unmodified against the faked agency APIs:
what each one asks for, and what reaches the raw zone."""

import json

import pandas as pd
import pytest

import config
from extractors import dc, fairfax, moco, pgc, pwc
from tests.fakes import UNLISTED

MODULES = {"moco": moco, "pgc": pgc, "dc": dc, "fairfax": fairfax, "pwc": pwc}


def landed(paths, source) -> pd.DataFrame:
    files = sorted((paths["RAW_DIR"] / source).glob("*/*.parquet"))
    assert files, f"{source} landed nothing"
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


@pytest.mark.parametrize("source", sorted(MODULES))
def test_requests_only_allowlisted_fields(paths, world, source):
    MODULES[source].run()
    asked = world.requested_fields()[source]
    allowed = set(config.SOURCES[source]["fields"]) | set(config.SOURCES[source].get("fetch_only", []))
    assert asked <= allowed, f"{source} requested {asked - allowed}"
    # the extras each agency really publishes are never even fetched,
    # except the one fetch_only input the privacy step needs
    assert not (asked & UNLISTED[source]) - set(config.SOURCES[source].get("fetch_only", []))


@pytest.mark.parametrize("source", sorted(MODULES))
def test_lands_only_allowlisted_stamped_columns(paths, world, source):
    MODULES[source].run()
    frame = landed(paths, source)
    assert set(frame.columns) <= set(config.allowed_fields(source))
    assert not set(frame.columns) & UNLISTED[source]
    assert frame[config.EXTRACTED_AT].notna().all()
    assert frame[config.EXTRACTED_AT].nunique() == 1, "one batch, one extraction time"


@pytest.mark.parametrize("source", sorted(MODULES))
def test_advances_the_watermark(paths, world, source):
    MODULES[source].run()
    state = json.loads(paths["STATE_PATH"].read_text())
    assert source in state


def test_pgc_lands_block_level_locations_only(paths, world):
    pgc.run()
    frame = landed(paths, "pgc").set_index("incident_case_id")
    assert frame.loc["PG26000101", "block_address"] == "9700 BLOCK GOOD LUCK RD"
    assert frame.loc["PG26000102", "block_address"] == "SANSBURY RD / RITCHIE MARLBORO RD SB"
    assert frame.loc["PG26000101", "latitude"] == "38.984"
    assert frame.loc["PG26000101", "longitude"] == "-76.842"
    # the exact house address appears nowhere in what was written
    for path in (paths["RAW_DIR"] / "pgc").glob("*/*.parquet"):
        assert b"9759 GOOD LUCK" not in path.read_bytes()


def test_geometry_becomes_coordinates_for_arcgis_point_sources(paths, world):
    fairfax.run()
    pwc.run()
    ffx = landed(paths, "fairfax").iloc[0]
    assert (ffx["latitude"], ffx["longitude"]) == ("38.7984", "-77.156")
    assert landed(paths, "pwc").iloc[0]["latitude"] == "38.641"


def test_a_second_run_pulls_only_the_overlap(paths, world):
    """Incremental: after one run, the next asks only for recent records."""
    moco.run()
    first_since = [p["$where"] for u, p in world.calls if "/resource/" in u][-1]
    world.calls.clear()
    moco.run()
    second_since = [p["$where"] for u, p in world.calls if "/resource/" in u][-1]
    assert second_since > first_since
