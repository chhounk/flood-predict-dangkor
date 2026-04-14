"""Validation framework scaffold — compares predictions against observations.

In v1, this writes an empty-but-structured validation JSON.
Future versions will compare against Sentinel-1 SAR flood extent maps.

Usage:
    python -m src.validation.framework
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT, DOCS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"


def run_validation() -> Path:
    """Run the weekly validation scaffold.

    In v1, this creates a structured but empty validation result.
    Future: compare last week's predictions against Sentinel-1 observed flood extents.

    Returns:
        Path to the written validation JSON.
    """
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # Structure the validation output
    result = {
        "validation_date": date_str,
        "validation_type": "weekly",
        "engine_version": "engine-v0.1",
        "period": {
            "start": (now - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d"),
            "end": date_str,
        },
        "observations": {
            "sentinel1_sar": None,  # TODO: Sentinel-1 SAR flood extent
            "ground_truth": None,   # TODO: ground truth reports
            "source": "No observation data available in v1",
        },
        "predictions_evaluated": 0,
        "metrics": {
            "pod": None,           # Probability of Detection
            "far": None,           # False Alarm Ratio
            "csi": None,           # Critical Success Index
            "bias": None,          # Frequency Bias
            "accuracy": None,      # Overall accuracy
            "note": "Metrics will be populated when observation data becomes available",
        },
        "confusion_matrix": {
            "true_positive": None,
            "false_positive": None,
            "true_negative": None,
            "false_negative": None,
        },
        "notes": [
            "Validation framework scaffolded — no observation data available yet.",
            "Sentinel-1 SAR comparison is a documented TODO.",
            "See src/validation/sentinel1_stub.py for implementation notes.",
        ],
    }

    # Write weekly file
    weekly_path = VALIDATION_DIR / f"weekly_{date_str}.json"
    with open(weekly_path, "w") as f:
        json.dump(result, f, indent=2)
    logger.info("Validation result written: %s", weekly_path)

    # Write/update latest.json
    latest_path = VALIDATION_DIR / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(result, f, indent=2)
    logger.info("Validation latest updated: %s", latest_path)

    # Copy to docs for dashboard
    docs_data = DOCS_DIR / "data"
    docs_data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest_path, docs_data / "validation_latest.json")

    return weekly_path


if __name__ == "__main__":
    run_validation()
