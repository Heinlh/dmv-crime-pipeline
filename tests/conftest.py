"""Shared fixtures. Every test runs against its own raw zone, watermarks,
warehouse and site-data directory, so nothing touches data/, state/ or
site/data/ in the checkout, and tests cannot leak state into each other.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from tests.fakes import build_world  # noqa: E402


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the pipeline's storage at a temp directory."""
    layout = {
        "RAW_DIR": tmp_path / "raw",
        "STATE_PATH": tmp_path / "state" / "watermarks.json",
        "WAREHOUSE_PATH": tmp_path / "warehouse" / "crime.duckdb",
        "SITE_DATA_DIR": tmp_path / "site_data",
    }
    for name, value in layout.items():
        monkeypatch.setattr(config, name, value)
    return layout


@pytest.fixture
def world(monkeypatch):
    """All five agency APIs, faked in memory and wired into the extractors."""
    return build_world().install(monkeypatch)
