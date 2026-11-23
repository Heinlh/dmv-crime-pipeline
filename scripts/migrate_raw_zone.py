"""One-time migration of the legacy raw zone onto the `data` branch.

Before the data branch existed, the raw parquet zone lived only in the
GitHub Actions cache. It was landed with every field each agency
returned, including some finer than block level, and with no record of
when each batch was fetched. This rewrites that history into the shape
the pipeline lands today, so the public branch is consistent from its
first commit:

  - every file passes through prepare_raw_frame, the same function
    land_raw uses: finer-than-block locations coarsened, allowlisted
    fields only (extractors/base.py, extractors/privacy.py)
  - every row gains EXTRACTED_AT, recovered from the file name the old
    land_raw wrote ({source}_{YYYY-MM-DD}_{HHMMSS}.parquet), which is
    when that batch was fetched
  - files keep their relative paths, so each still corresponds to one
    extraction batch
  - the watermarks file is copied unchanged

No rows are added or removed: the script checks every file's row count
and fails loudly on any mismatch. Re-running it over the same input
produces the same output.

    python -m scripts.migrate_raw_zone --src data/raw \\
        --state state/watermarks.json --dest .datastore
"""

import argparse
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from extractors.base import extraction_stamp, prepare_raw_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s migrate: %(message)s")
logger = logging.getLogger(__name__)

README_TEMPLATE = Path(__file__).with_name("data_branch_readme.md")

# {source}_{YYYY-MM-DD}_{HHMMSS}.parquet, optionally with a _N collision suffix
_FILE_STAMP = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{6})(?:_\d+)?\.parquet$")
_PARTITION = re.compile(r"^extract_date=(\d{4}-\d{2}-\d{2})$")


def extracted_at_from_path(path: Path) -> str:
    """When a legacy batch was fetched, from the name land_raw gave it.
    Falls back to midnight of the extract_date partition for a file whose
    name lacks the time; raises if neither carries a date, since guessing
    would silently misorder versions of a record."""
    match = _FILE_STAMP.search(path.name)
    if match:
        moment = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H%M%S")
        return extraction_stamp(moment.replace(tzinfo=timezone.utc))
    partition = _PARTITION.match(path.parent.name)
    if partition:
        logger.warning("%s has no time in its name; using its partition date", path)
        moment = datetime.strptime(partition.group(1), "%Y-%m-%d")
        return extraction_stamp(moment.replace(tzinfo=timezone.utc))
    raise ValueError(f"cannot tell when {path} was extracted")


def migrate_file(source: str, src_path: Path, dest_path: Path) -> dict:
    frame = pd.read_parquet(src_path)
    before_columns = set(frame.columns)
    if config.EXTRACTED_AT not in frame.columns:
        frame[config.EXTRACTED_AT] = extracted_at_from_path(src_path)
    shaped = prepare_raw_frame(source, frame)
    if len(shaped) != len(frame):
        raise RuntimeError(f"{src_path}: row count changed {len(frame)} -> {len(shaped)}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shaped.to_parquet(dest_path, index=False)
    return {"rows": len(shaped), "dropped": before_columns - set(shaped.columns)}


def migrate(src_dir: Path, state_path: Path | None, dest_dir: Path) -> dict:
    """Rewrite src_dir/{source}/*/*.parquet into dest_dir/raw/ and copy the
    watermarks to dest_dir/state/. Returns per-source stats."""
    summary: dict[str, dict] = {}
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw_out = dest_dir / "raw"
    if src_dir.exists():
        for source_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
            source = source_dir.name
            if source not in config.SOURCES:
                logger.warning("skipping %s: not a configured source", source_dir)
                continue
            stats = summary.setdefault(source, {"files": 0, "rows": 0, "dropped": set()})
            for src_path in sorted(source_dir.glob("*/*.parquet")):
                result = migrate_file(source, src_path, raw_out / src_path.relative_to(src_dir))
                stats["files"] += 1
                stats["rows"] += result["rows"]
                stats["dropped"] |= result["dropped"]
    else:
        logger.warning("no legacy raw zone at %s; the data branch starts empty", src_dir)

    if state_path and state_path.exists():
        (dest_dir / "state").mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_path, dest_dir / "state" / "watermarks.json")
    else:
        logger.warning("no legacy watermarks at %s; every source will backfill", state_path)

    shutil.copy2(README_TEMPLATE, dest_dir / "README.md")

    for source, stats in summary.items():
        logger.info("%s: %d files, %d rows; fields removed: %s", source, stats["files"],
                    stats["rows"], ", ".join(sorted(stats["dropped"])) or "none")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--src", type=Path, required=True, help="legacy raw zone (data/raw)")
    parser.add_argument("--state", type=Path, help="legacy watermarks.json")
    parser.add_argument("--dest", type=Path, required=True, help="data branch worktree")
    args = parser.parse_args(argv)
    migrate(args.src, args.state, args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
