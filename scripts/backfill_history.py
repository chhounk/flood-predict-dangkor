"""Backfill every archived run in data/outputs/history/ into Supabase.

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in the environment.

Usage:
    export SUPABASE_URL=https://xxxx.supabase.co
    export SUPABASE_SERVICE_KEY=eyJ...
    python scripts/backfill_history.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow running as a script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR  # noqa: E402
from src.db.supabase_sink import ingest_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    history_dir = OUTPUT_DIR / "history"
    if not history_dir.exists():
        logger.error("No history directory at %s", history_dir)
        sys.exit(1)

    files = sorted(history_dir.glob("*.json"))
    logger.info("Found %d archived runs", len(files))
    if not files:
        logger.info("Nothing to backfill")
        return

    ok, failed = 0, 0
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            if ingest_run(data):
                logger.info("✓ %s", f.name)
                ok += 1
            else:
                logger.warning("✗ %s — ingest_run returned False", f.name)
                failed += 1
        except Exception:
            logger.exception("✗ %s — exception", f.name)
            failed += 1

    logger.info("Backfill complete: %d ok, %d failed", ok, failed)
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
