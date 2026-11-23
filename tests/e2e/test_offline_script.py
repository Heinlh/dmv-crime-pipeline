"""The original offline end-to-end script (test_pipeline_offline.py at
the repo root) still passes. It runs on import and repoints config
globally, so it runs in its own interpreter."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_offline_pipeline_script():
    result = subprocess.run([sys.executable, "test_pipeline_offline.py"], cwd=ROOT,
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    assert "All assertions passed" in result.stdout
