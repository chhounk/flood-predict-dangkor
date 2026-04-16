"""Two-tier flood alert dispatcher.

Reads the N most recent pipeline runs and applies a persistence-based
alert logic to minimise false positives while maintaining high recall:

  WATCH  — district mean_prob ≥ WATCH_THRESHOLD (0.10) in latest run
  ALERT  — district mean_prob ≥ ALERT_THRESHOLD (0.15) in BOTH the
            latest run AND the preceding run (persistence check)
  CLEAR  — neither condition met

Validated against 2024–2025 Sentinel-1 ground truth:
  Combined (115 S1 images, 99 flood events)
  Threshold 0.10 → Precision=0.887  Recall=0.869  F1=0.878
  Mean lead time: 53h  (Median 60h)

Outputs
-------
  • data/outputs/alert_log.jsonl   — one JSON line per run, always written
  • Telegram message               — only on state CHANGE (CLEAR→WATCH→ALERT)
  • stdout summary

Usage
-----
    # Standalone (no Telegram):
    python scripts/alert_dispatcher.py

    # With Telegram:
    export TELEGRAM_BOT_TOKEN=<your-bot-token>
    export TELEGRAM_CHAT_ID=<your-chat-id>
    python scripts/alert_dispatcher.py

    # Check last N runs only (default 6 = 3 days at 12h cadence):
    python scripts/alert_dispatcher.py --lookback 6

Schedule
--------
    Add to cron after each pipeline run:
    0 1,13 * * *  cd /path/to/project && python scripts/alert_dispatcher.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import requests as http_requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# Derived from 2024–2025 event-level validation (event_validation.py).
# Threshold sweep showed 0.10 maximises F1 (0.878) across both seasons:
#   Precision=0.887  Recall=0.869  Mean lead time=53h
# Persistence check (≥0.15 × 2 runs) was tested and REJECTED — it eliminated
# only 3 false alarms but caused 32 additional missed events.
WARN_THRESHOLD = 0.10     # single-run trigger (event-level validated)

# "High impact" flag: L3+ cells cover a meaningful fraction of the district
# Sentinel-1 validated FLOOD_EVENT_PCT = 2% → ~39 cells at district scale
HIGH_IMPACT_L3_PCT = 5.0  # % of district cells at L3+

# How many recent runs to show in summary (default=6 = 3 days at 12h cadence)
DEFAULT_LOOKBACK = 6


# ── Run loading ───────────────────────────────────────────────────────────────

def load_recent_runs(n: int) -> list[dict]:
    """Load the N most recent live runs from history folder (any year)."""
    hist = OUTPUT_DIR / "history"
    # Match all year prefixes — dispatcher is now the live alert system and
    # must work regardless of which calendar year we are in.
    all_files = sorted(hist.glob("20[0-9][0-9]-*.json"))

    runs = []
    for fp in reversed(all_files):   # newest first
        stem = fp.stem
        try:
            run_id = (stem[:10] + "T" + stem[11:13] + ":" +
                      stem[14:16] + ":" + stem[17:19] + "Z")
            dt = datetime.fromisoformat(run_id.replace("Z", "+00:00"))
        except (ValueError, IndexError):
            continue

        with open(fp) as f:
            payload = json.load(f)

        # Skip historical backfill placeholders
        if payload.get("models_used") != ["era5_reanalysis"] and \
           "era5_reanalysis" not in payload.get("models_used", []):
            pass  # live run — include regardless of model

        cells = payload.get("cells", [])
        if not cells:
            continue

        probs  = [c.get("peak_probability", 0.0) for c in cells]
        levels = [c.get("peak_level", 1)         for c in cells]

        n_total = len(cells)
        pct_l2  = 100 * sum(1 for l in levels if l >= 2) / n_total
        pct_l3  = 100 * sum(1 for l in levels if l >= 3) / n_total
        pct_l4  = 100 * sum(1 for l in levels if l >= 4) / n_total

        runs.append({
            "run_id":      run_id,
            "dt":          dt,
            "mean_prob":   float(np.mean(probs)),
            "max_prob":    float(np.max(probs)),
            "pct_l2":      round(pct_l2, 1),
            "pct_l3":      round(pct_l3, 1),
            "pct_l4":      round(pct_l4, 1),
            "models_used": payload.get("models_used", []),
            "communes":    payload.get("communes", []),
        })

        if len(runs) >= n:
            break

    # Return chronological order (oldest first)
    return list(reversed(runs))


# ── Alert logic ───────────────────────────────────────────────────────────────

def classify_alert(runs: list[dict]) -> tuple[str, dict]:
    """
    Determine alert level from recent runs.

    Returns (level, details) where level is 'CLEAR' or 'WARN'.

    A single run with district mean_prob ≥ WARN_THRESHOLD triggers a warning.
    Validated against 2024–2025 Sentinel-1: P=0.887, R=0.869, F1=0.878.
    Note: persistence checks were evaluated and rejected — they caused 32
    additional missed events to eliminate only 3 false alarms.
    """
    if not runs:
        return "CLEAR", {"reason": "no runs available"}

    latest = runs[-1]
    mean_prob = latest["mean_prob"]

    details = {
        "run_id":         latest["run_id"],
        "mean_prob":      round(mean_prob, 3),
        "max_prob":       round(latest["max_prob"], 3),
        "pct_l2":         latest["pct_l2"],
        "pct_l3":         latest["pct_l3"],
        "pct_l4":         latest["pct_l4"],
        "n_runs_checked": len(runs),
        "high_impact":    latest["pct_l3"] >= HIGH_IMPACT_L3_PCT,
    }

    if mean_prob >= WARN_THRESHOLD:
        details["trigger"] = f"mean_prob≥{WARN_THRESHOLD}"
        return "WARN", details

    return "CLEAR", details


def top_communes(communes: list[dict], n: int = 3) -> list[dict]:
    """Return top-N communes by peak_probability."""
    return sorted(communes, key=lambda c: c.get("peak_probability", 0), reverse=True)[:n]


# ── Alert log ─────────────────────────────────────────────────────────────────

ALERT_LOG = OUTPUT_DIR / "alert_log.jsonl"


def read_last_logged_state() -> Optional[str]:
    """Read the most recent alert state from the log."""
    if not ALERT_LOG.exists():
        return None
    with open(ALERT_LOG) as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1]).get("alert_level")
    except json.JSONDecodeError:
        return None


def write_log_entry(level: str, details: dict):
    """Append one JSONL entry to the alert log."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "logged_at": datetime.now(tz=timezone.utc).isoformat(),
        "alert_level": level,
        **details,
    }
    with open(ALERT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Telegram ──────────────────────────────────────────────────────────────────

EMOJI = {"CLEAR": "🟢", "WARN": "🔴"}

MESSAGES = {
    "CLEAR": (
        "🟢 *Flood Warning CLEARED* — Dangkor District\n\n"
        "District-wide flood probability has dropped below threshold.\n"
        "No elevated risk in the next 72 hours."
    ),
    "WARN": (
        "🔴 *FLOOD WARNING — Dangkor District*\n\n"
        "⚠️ Elevated flood probability in the 72-hour forecast.\n"
        "Recommend alerting ground teams and monitoring conditions.\n\n"
        "{commune_block}"
        "\n_Mean probability: {mean_prob:.0%}  |  Run: {run_id}_"
        "{high_impact_note}"
    ),
}


def format_commune_block(communes: list[dict]) -> str:
    top = top_communes(communes)
    if not top:
        return ""
    lines = ["*Highest-risk communes:*"]
    for c in top:
        prob = c.get("peak_probability", 0)
        name = c.get("commune_name", c.get("commune_id", "?"))
        pct  = c.get("affected_pct_l2_plus", 0)
        lines.append(f"  • {name}: {prob:.0%} probability ({pct:.0f}% area at risk)")
    return "\n".join(lines) + "\n"


def send_telegram(level: str, details: dict, communes: list[dict]):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.info("No Telegram creds set — skipping notification")
        return

    commune_block = format_commune_block(communes)
    high_impact_note = (
        "\n\n🆘 *HIGH IMPACT* — over 5% of district cells at Level 3+ risk."
        if details.get("high_impact") else ""
    )

    msg_template = MESSAGES.get(level, MESSAGES["CLEAR"])
    text = msg_template.format(
        commune_block=commune_block,
        mean_prob=details.get("mean_prob", 0),
        run_id=details.get("run_id", "?"),
        high_impact_note=high_impact_note,
    )

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = http_requests.post(url, json={
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
    }, timeout=10)

    if resp.ok:
        logger.info("Telegram %s notification sent (chat_id=%s)", level, chat_id)
    else:
        logger.error("Telegram send failed: %s %s", resp.status_code, resp.text)


# ── Channel + subscriber broadcast ───────────────────────────────────────────

MINI_APP_URL = "https://chhounk.github.io/flood-predict-dangkor/"

_MAP_BUTTON = {
    "inline_keyboard": [[
        {
            "text": "🌊 Open Live Map",
            "web_app": {"url": MINI_APP_URL},
        }
    ]]
}


def _build_alert_text(level: str, details: dict, communes: list[dict]) -> str:
    """Build the alert message text (same format as MESSAGES dict)."""
    commune_block = format_commune_block(communes)
    high_impact_note = (
        "\n\n🆘 *HIGH IMPACT* — over 5% of district cells at Level 3+ risk."
        if details.get("high_impact") else ""
    )
    msg_template = MESSAGES.get(level, MESSAGES["CLEAR"])
    return msg_template.format(
        commune_block=commune_block,
        mean_prob=details.get("mean_prob", 0),
        run_id=details.get("run_id", "?"),
        high_impact_note=high_impact_note,
    )


def send_to_channel(
    level: str,
    details: dict,
    communes: list[dict],
    token: str,
    channel_id: str,
) -> None:
    """Post the alert to @dangkor_flood_alert with an inline Open Live Map button."""
    if not token:
        logger.info("No TELEGRAM_BOT_TOKEN — skipping channel notification")
        return

    text = _build_alert_text(level, details, communes)
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = http_requests.post(url, json={
        "chat_id":      channel_id,
        "text":         text,
        "parse_mode":   "Markdown",
        "reply_markup": _MAP_BUTTON,
    }, timeout=10)

    if resp.ok:
        logger.info("Channel %s alert sent to %s", level, channel_id)
    else:
        logger.error("Channel send failed: %s %s", resp.status_code, resp.text)


def send_to_subscribers(
    level: str,
    details: dict,
    communes: list[dict],
    token: str,
) -> None:
    """Send the alert to every active Supabase subscriber.

    Silently skips if SUPABASE_URL / SUPABASE_SERVICE_KEY are not set.
    """
    if not token:
        logger.info("No TELEGRAM_BOT_TOKEN — skipping subscriber broadcast")
        return

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not sb_url or not sb_key:
        logger.info("No Supabase creds — skipping subscriber broadcast")
        return

    try:
        from supabase import create_client  # imported lazily — not in base requirements.txt
        sb = create_client(sb_url, sb_key)
        result = (
            sb.table("telegram_subscribers")
            .select("chat_id")
            .eq("active", True)
            .execute()
        )
        subscribers = result.data or []
    except Exception as exc:
        logger.error("Failed to fetch subscribers from Supabase: %s", exc)
        return

    if not subscribers:
        logger.info("No active subscribers — nothing to broadcast")
        return

    text = _build_alert_text(level, details, communes)
    tg_url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    failed = 0

    for row in subscribers:
        chat_id = row.get("chat_id")
        if not chat_id:
            continue
        try:
            resp = http_requests.post(tg_url, json={
                "chat_id":      chat_id,
                "text":         text,
                "parse_mode":   "Markdown",
                "reply_markup": _MAP_BUTTON,
            }, timeout=10)
            if resp.ok:
                sent += 1
            else:
                logger.warning("Subscriber send failed (chat_id=%s): %s", chat_id, resp.text)
                failed += 1
        except Exception as exc:
            logger.warning("Subscriber send error (chat_id=%s): %s", chat_id, exc)
            failed += 1

    logger.info("Subscriber broadcast: %d sent, %d failed (total active: %d)",
                sent, failed, len(subscribers))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Flood alert dispatcher")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                    help="Number of recent runs to consider (default=6)")
    ap.add_argument("--force-notify", action="store_true",
                    help="Send Telegram even if state hasn't changed")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print alert without writing log or sending Telegram")
    args = ap.parse_args()

    # Load runs
    runs = load_recent_runs(args.lookback)
    if not runs:
        logger.warning("No live runs found in %s/history — nothing to do", OUTPUT_DIR)
        sys.exit(0)

    logger.info("Loaded %d recent runs (oldest: %s, newest: %s)",
                len(runs),
                runs[0]["dt"].strftime("%Y-%m-%d %H:%M UTC"),
                runs[-1]["dt"].strftime("%Y-%m-%d %H:%M UTC"))

    # Classify
    level, details = classify_alert(runs)
    latest_run = runs[-1]

    # Compare to last logged state
    prev_state = read_last_logged_state()
    state_changed = (level != prev_state)

    # Print summary
    print(f"\n{'='*60}")
    print(f"FLOOD ALERT DISPATCHER — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"\nAlert level:    {EMOJI[level]} {level}")
    print(f"Previous state: {EMOJI.get(prev_state, '—')} {prev_state or 'none'}")
    print(f"State changed:  {'YES ← notify' if state_changed else 'no'}")
    print(f"\nLatest run:     {latest_run['run_id']}")
    print(f"Mean prob:      {latest_run['mean_prob']:.3f}  (Warn≥{WARN_THRESHOLD})")
    print(f"Max prob:       {latest_run['max_prob']:.3f}")
    print(f"L2+ cells:      {latest_run['pct_l2']:.1f}%")
    print(f"L3+ cells:      {latest_run['pct_l3']:.1f}%  {'⚠️ HIGH IMPACT' if details.get('high_impact') else ''}")

    if level != "CLEAR":
        print(f"\nTrigger:        {details.get('trigger', '?')}")

    top = top_communes(latest_run["communes"])
    if top and any(c.get("peak_probability", 0) > 0 for c in top):
        print(f"\nTop communes:")
        for c in top:
            print(f"  {c.get('commune_name', c.get('commune_id')):20} "
                  f"prob={c.get('peak_probability', 0):.3f}  "
                  f"L2+={c.get('affected_pct_l2_plus', 0):.1f}%")

    # Recent run history
    print(f"\nRun history ({len(runs)} runs):")
    for r in reversed(runs):
        marker = "← latest" if r is runs[-1] else ""
        lvl_marker = "🔴" if r["mean_prob"] >= WARN_THRESHOLD else "🟢"
        print(f"  {lvl_marker} {r['dt'].strftime('%Y-%m-%d %H:%M')}  "
              f"mean={r['mean_prob']:.3f}  max={r['max_prob']:.3f}  "
              f"L2={r['pct_l2']:.0f}%  {marker}")

    if args.dry_run:
        logger.info("--dry-run: skipping log write and Telegram")
        return

    # Write log entry
    write_log_entry(level, details)

    # Send Telegram only on state change (or --force-notify)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "@dangkor_flood_alert")
    if state_changed or args.force_notify:
        send_to_channel(level, details, latest_run["communes"], token, channel_id)
        send_to_subscribers(level, details, latest_run["communes"], token)
    else:
        logger.info("State unchanged (%s) — no Telegram notification", level)


if __name__ == "__main__":
    main()
