"""Storage paths are overridable, which is how CI points the pipeline at
the data branch. config reads the environment at import, so each case
runs in a fresh interpreter."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _paths(env_overrides: dict) -> tuple[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("DMV_RAW_DIR", "DMV_STATE_PATH")}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", "import config; print(config.RAW_DIR); print(config.STATE_PATH)"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    return out[0], out[1]


def test_defaults_live_in_the_checkout():
    raw, state = _paths({})
    assert raw == str(ROOT / "data" / "raw")
    assert state == str(ROOT / "state" / "watermarks.json")


def test_env_redirects_both_paths(tmp_path):
    raw, state = _paths({"DMV_RAW_DIR": str(tmp_path / "r"),
                         "DMV_STATE_PATH": str(tmp_path / "s.json")})
    assert raw == str(tmp_path / "r")
    assert state == str(tmp_path / "s.json")


def test_relative_overrides_resolve_against_the_working_directory():
    raw, _ = _paths({"DMV_RAW_DIR": ".datastore/raw"})
    assert raw == str(ROOT / ".datastore" / "raw")


def test_empty_override_falls_back_to_the_default():
    raw, _ = _paths({"DMV_RAW_DIR": ""})
    assert raw == str(ROOT / "data" / "raw")
