"""Entry point: extract both sources, then load and transform.

A failure in one extractor does not block the other; the loader runs
with whatever landed successfully.
"""

import logging
import sys

from export import export_site_data
from extractors import dc, moco
from load import load_duckdb

logger = logging.getLogger("pipeline")


def main() -> int:
    failures = []
    for name, module in (("moco", moco), ("dc", dc)):
        try:
            module.run()
        except Exception:
            logger.exception("Extractor %s failed", name)
            failures.append(name)

    load_duckdb.run()
    export_site_data.run()

    if failures:
        logger.error("Completed with extractor failures: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
