"""Historical backfill: run the pipeline against past dates to build a
validation dataset before the real rainy season starts.

Iterates daily at 12:00 UTC through two full rainy seasons (May–Oct 2024/2025)
and runs the full pipeline in HISTORICAL mode against Open-Meteo's archive.

Each synthetic run:
  - writes data/outputs/history/{run_id}.json locally
  - writes runs + commune_predictions to Supabase (per-cell cells skipped to
    stay within free-tier storage)
  - does NOT touch latest.json / docs/data (the live dashboard is untouched)

Resume support: skips dates whose archive file already exists.

Usage:
    cd "/path/to/repo"
    export SUPABASE_URL=...
    export SUPABASE_SERVICE_KEY=...
    python scripts/historical_backfill.py               # full 2-season run
    python scripts/historical_backfill.py --sample      # 5 dates for smoke test
    python scripts/historical_backfill.py --start 2024-05-01 --end 2024-05-31
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Phnom Penh rainy season: May through October, inclusive.
SEASONS = [
    ("2024-05-01", "2024-10-31"),
    ("2025-05-01", "2025-10-31"),
]

# Anchor each historical run at 12:00 UTC (= 19:00 Cambodia local) —
# mid-afternoon, typical convective-storm window.
DAILY_HOUR = 12

# Pause between runs to avoid hammering Open-Meteo's free endpoint.
INTER_RUN_SLEEP_SEC = 2.0


def iter_dates(start: str, end: str, daily_hour: int = DAILY_HOUR):
    """Yield datetime objects at daily_hour UTC between start and end inclusive."""
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    current = d0.replace(hour=daily_hour, minute=0, second=0, microsecond=0)
    while current.date() <= d1.date():
        yield current
        current += timedelta(days=1)


def archive_exists_for(ref_time: datetime) -> bool:
    """Check if we already have a history/ file for this reference time."""
    run_id = ref_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    safe = run_id.replace(":", "-")
    return (OUTPUT_DIR / "history" / f"{safe}.json").exists()


def run_one(ref_time: datetime) -> tuple[bool, float]:
    """Invoke the pipeline in a subprocess with REFERENCE_TIME set.

    Subprocess isolation prevents any per-run leak (memory, rasterio handles,
    pysheds state) from compounding across hundreds of iterations.
    """
    t0 = time.time()
    run_iso = ref_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    env = {**_child_env(), "REFERENCE_TIME": run_iso}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "src.pipeline"],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        ok = result.returncode == 0
        if not ok:
            logger.warning("Pipeline exit %d for %s\n%s",
                           result.returncode, run_iso, result.stderr[-800:])
        return ok, time.time() - t0
    except subprocess.TimeoutExpired:
        logger.error("Timeout on %s", run_iso)
        return False, time.time() - t0


def _child_env() -> dict:
    """Current process env — child inherits SUPABASE_* and PATH."""
    import os
    return dict(os.environ)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true",
                    help="5-date smoke test instead of full range")
    ap.add_argument("--start", help="YYYY-MM-DD override start")
    ap.add_argument("--end",   help="YYYY-MM-DD override end")
    ap.add_argument("--daily-hour", type=int, default=DAILY_HOUR,
                    help="UTC hour anchor per day (default 12)")
    args = ap.parse_args()

    if args.sample:
        ranges = [("2024-07-15", "2024-07-19")]  # 5 dates, July 2024
    elif args.start and args.end:
        ranges = [(args.start, args.end)]
    else:
        ranges = SEASONS

    # Build the full date list
    all_dates = []
    for start, end in ranges:
        all_dates.extend(iter_dates(start, end, daily_hour=args.daily_hour))

    logger.info("Backfill target: %d runs across %d range(s)",
                len(all_dates), len(ranges))

    ok = skipped = failed = 0
    for i, ref_time in enumerate(all_dates, 1):
        run_iso = ref_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if archive_exists_for(ref_time):
            logger.info("[%d/%d] SKIP %s (already archived)",
                        i, len(all_dates), run_iso)
            skipped += 1
            continue

        logger.info("[%d/%d] RUN  %s", i, len(all_dates), run_iso)
        success, elapsed = run_one(ref_time)
        if success:
            ok += 1
            logger.info("[%d/%d] ✓    %s (%.1fs)", i, len(all_dates), run_iso, elapsed)
        else:
            failed += 1
            logger.warning("[%d/%d] ✗    %s (%.1fs)", i, len(all_dates), run_iso, elapsed)

        if i < len(all_dates):
            time.sleep(INTER_RUN_SLEEP_SEC)

    logger.info("=== Backfill complete: ok=%d, skipped=%d, failed=%d ===",
                ok, skipped, failed)
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
