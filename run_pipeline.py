"""Entry point: extract both sources, then load and transform.

A failure in one extractor does not block the other; the loader runs
with whatever landed successfully.
"""

import logging
import sys

from export import export_site_data, render_og_card
from extractors import dc, fairfax, moco, pgc
from load import load_duckdb

logger = logging.getLogger("pipeline")


def main() -> int:
    failures = []
    for name, module in (("moco", moco), ("dc", dc), ("pgc", pgc), ("fairfax", fairfax)):
        try:
            module.run()
        except Exception:
            logger.exception("Extractor %s failed", name)
            failures.append(name)

    load_duckdb.run()
    export_site_data.run()
    try:
        render_og_card.run()
    except Exception:
        # the share card is decoration; never let it fail the pipeline
        logger.exception("OG card rendering failed")

    if failures:
        logger.error("Completed with extractor failures: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
