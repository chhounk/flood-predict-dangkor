"""Find historical backfill files that fell through to placeholder (due to
Open-Meteo transient failures), delete them, and re-run the backfill — which
now picks up only the missing dates via its resume logic.

A run is "placeholder" if models_used != ['era5_reanalysis'] or if
summary.cells_by_peak_level has everything in level 1. The former is the
tell-tale sign of the pipeline's L1 exception handler kicking in.

Usage:
    export SUPABASE_URL=...
    export SUPABASE_SERVICE_KEY=...
    python scripts/rescue_placeholders.py
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def is_placeholder(payload: dict) -> bool:
    """Detect a pipeline run that didn't actually use ERA5 reanalysis."""
    if payload.get("models_used") != ["era5_reanalysis"]:
        return True
    counts = payload.get("summary", {}).get("cells_by_peak_level", {})
    total = sum(counts.values()) if counts else 0
    l1 = counts.get("1", 0)
    # All-L1 with nonzero total → placeholder_cells() output
    if total > 0 and l1 == total:
        return True
    return False


def main():
    hist = OUTPUT_DIR / "history"
    placeholders: list[Path] = []
    for fp in sorted(hist.glob("202[45]-*.json")):
        with open(fp) as f:
            payload = json.load(f)
        if is_placeholder(payload):
            placeholders.append(fp)

    logger.info("Found %d placeholder files out of %d historical runs.",
                len(placeholders), len(list(hist.glob("202[45]-*.json"))))

    if not placeholders:
        logger.info("Nothing to rescue.")
        return

    # Delete the bad files so resume logic picks them up
    for fp in placeholders:
        fp.unlink()
    logger.info("Deleted %d placeholder files.", len(placeholders))

    # Re-invoke the backfill. It'll skip the 235 good archives and re-run
    # only the 133 deleted ones, using the new retry logic.
    logger.info("Launching backfill to rescue missing dates...")
    result = subprocess.run(
        [sys.executable, "scripts/historical_backfill.py"],
        cwd=str(REPO),
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
