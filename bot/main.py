"""Dangkor Flood Alert — Telegram Bot.

Async bot using python-telegram-bot >= 20 (PTB v20).
Runs in polling mode. Deploy as a Railway worker service.

Environment variables required:
  TELEGRAM_BOT_TOKEN   — BotFather token for @dangkor_flood_bot
  SUPABASE_URL         — Supabase project URL
  SUPABASE_SERVICE_KEY — Supabase service-role key (bypasses RLS)

Optional:
  LATEST_JSON_URL      — override the URL for the latest forecast JSON
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from supabase import create_client, Client
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MINI_APP_URL = "https://chhounk.github.io/flood-predict-dangkor/"
LATEST_JSON_URL = os.environ.get(
    "LATEST_JSON_URL",
    "https://chhounk.github.io/flood-predict-dangkor/data/latest.json",
)

WARN_THRESHOLD = 0.10
HIGH_IMPACT_THRESHOLD = 0.15

ALERT_EMOJI = {
    "CLEAR": "🟢",
    "WARN":  "🔴",
}

# ── Bilingual message strings ─────────────────────────────────────────────────
MSG_WELCOME = (
    "🌊 *Dangkor Flood Alert*\n\n"
    "Real-time 72h flood warnings for Dangkor District, Phnom Penh.\n"
    "Physics-based model · Sentinel-1 SAR validated · F1 = 0.878 · Lead time: 53h\n\n"
    "Use /subscribe to receive push alerts when flood risk is detected.\n"
    "Use /status to check current forecast.\n\n"
    "ប្រព័ន្ធព្រមានទឹកជំនន់សម្រាប់ស្រុកដានខ័រ ភ្នំពេញ\n"
    "ប្រើ /subscribe ដើម្បីទទួលការព្រមាន"
)

MSG_SUBSCRIBED = (
    "✅ *Subscribed\\!*\n"
    "You will receive flood alerts when district risk exceeds threshold\\.\n\n"
    "✅ *បានចុះឈ្មោះ\\!*\n"
    "អ្នកនឹងទទួលការព្រមានទឹកជំនន់ពីស្រុកដានខ័រ។"
)

MSG_ALREADY_SUBSCRIBED = (
    "You are already subscribed\\. Use /unsubscribe to stop alerts\\.\n"
    "អ្នកបានចុះឈ្មោះរួចហើយ។"
)

MSG_UNSUBSCRIBED = (
    "🔕 Unsubscribed\\. Use /subscribe to re\\-enable alerts\\.\n"
    "🔕 បានបញ្ឈប់ការជាវ។"
)

MSG_NOT_SUBSCRIBED = (
    "You are not currently subscribed\\. Use /subscribe to start receiving alerts\\.\n"
    "អ្នកមិនទាន់ចុះឈ្មោះទេ។ ប្រើ /subscribe ដើម្បីចុះឈ្មោះ។"
)

MSG_HELP = (
    "🌊 *Dangkor Flood Alert — Commands*\n\n"
    "/start — Welcome message and quick links\n"
    "/subscribe — Subscribe to flood alert notifications\n"
    "/unsubscribe — Unsubscribe from alerts\n"
    "/status — Check current 72h flood forecast\n"
    "/help — Show this help message\n\n"
    "*ពាក្យបញ្ជា:*\n"
    "/start — សារស្វាគមន៍\n"
    "/subscribe — ចុះឈ្មោះទទួលការព្រមាន\n"
    "/unsubscribe — បញ្ឈប់ការទទួលការព្រមាន\n"
    "/status — ពិនិត្យការព្យាករណ៍ទឹកជំនន់"
)


# ── Supabase client ───────────────────────────────────────────────────────────

def get_supabase() -> Optional[Client]:
    """Return a Supabase client or None if creds are not configured."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — Supabase disabled")
        return None
    return create_client(url, key)


# ── Inline keyboard helpers ───────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    """Two-button keyboard: Open Map (WebApp) + Subscribe."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🌊 Open Flood Map",
                web_app=WebAppInfo(url=MINI_APP_URL),
            ),
            InlineKeyboardButton(
                "🔔 Subscribe to Alerts",
                callback_data="subscribe",
            ),
        ]
    ])


def map_button() -> InlineKeyboardMarkup:
    """Single button: Open Map (WebApp)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🌊 Open Live Map",
                web_app=WebAppInfo(url=MINI_APP_URL),
            )
        ]
    ])


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /start."""
    await update.message.reply_text(
        MSG_WELCOME,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard(),
    )


async def _do_subscribe(chat_id: int, username: Optional[str], first_name: Optional[str]) -> str:
    """
    Insert or reactivate a subscriber row in Supabase.
    Returns one of: 'subscribed', 'already', 'no_db'
    """
    sb = get_supabase()
    if sb is None:
        return "no_db"

    # Check if already exists
    result = (
        sb.table("telegram_subscribers")
        .select("id, active")
        .eq("chat_id", chat_id)
        .execute()
    )

    if result.data:
        row = result.data[0]
        if row["active"]:
            return "already"
        # Reactivate
        sb.table("telegram_subscribers").update({"active": True}).eq("chat_id", chat_id).execute()
        return "subscribed"

    # Insert new row
    sb.table("telegram_subscribers").insert({
        "chat_id":    chat_id,
        "username":   username,
        "first_name": first_name,
        "active":     True,
    }).execute()
    return "subscribed"


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /subscribe command."""
    user = update.effective_user
    status = await _do_subscribe(
        chat_id=update.effective_chat.id,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
    )

    if status == "subscribed":
        await update.message.reply_text(MSG_SUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
    elif status == "already":
        await update.message.reply_text(MSG_ALREADY_SUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text(
            "✅ Subscription noted locally \\(database not configured\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /unsubscribe."""
    chat_id = update.effective_chat.id
    sb = get_supabase()

    if sb is None:
        await update.message.reply_text(MSG_UNSUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
        return

    result = (
        sb.table("telegram_subscribers")
        .select("id, active")
        .eq("chat_id", chat_id)
        .execute()
    )

    if not result.data or not result.data[0]["active"]:
        await update.message.reply_text(MSG_NOT_SUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
        return

    sb.table("telegram_subscribers").update({"active": False}).eq("chat_id", chat_id).execute()
    await update.message.reply_text(MSG_UNSUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /status — fetches latest.json and summarises forecast."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(LATEST_JSON_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch latest.json: %s", exc)
        await update.message.reply_text(
            "⚠️ Could not retrieve forecast data\\. Please try again later\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    cells = data.get("cells", [])
    communes = data.get("communes", [])
    run_id = data.get("run_id", "unknown")

    if not cells:
        await update.message.reply_text(
            "⚠️ Forecast data is empty\\. Model may still be running\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Compute mean probability across all cells
    probs = [c.get("peak_probability", 0.0) for c in cells]
    mean_prob = sum(probs) / len(probs) if probs else 0.0
    max_prob = max(probs) if probs else 0.0

    # Classify alert level
    level = "WARN" if mean_prob >= WARN_THRESHOLD else "CLEAR"
    emoji = ALERT_EMOJI[level]

    # Top 3 communes by peak_probability
    top3 = sorted(communes, key=lambda c: c.get("peak_probability", 0), reverse=True)[:3]

    # Count cells by level
    levels = [c.get("peak_level", 1) for c in cells]
    n_total = len(cells)
    n_l2 = sum(1 for l in levels if l >= 2)
    n_l3 = sum(1 for l in levels if l >= 3)
    n_l4 = sum(1 for l in levels if l == 4)

    # Format run_id for display (strip seconds for brevity)
    run_display = run_id[:16] if len(run_id) >= 16 else run_id

    # Build commune lines
    commune_lines = ""
    if top3 and any(c.get("peak_probability", 0) > 0 for c in top3):
        commune_lines = "\n*Highest-risk communes:*\n"
        for c in top3:
            name = c.get("commune_name", c.get("commune_id", "?"))
            prob = c.get("peak_probability", 0)
            pct = c.get("affected_pct_l2_plus", 0)
            commune_lines += f"  • {name}: {prob:.0%} \\({pct:.0f}% area at risk\\)\n"

    high_impact_note = ""
    pct_l3 = 100 * n_l3 / n_total if n_total else 0
    if pct_l3 >= 5.0:
        high_impact_note = "\n🆘 *HIGH IMPACT* — over 5% of district cells at L3\\+ risk\\."

    # Escape special chars for MarkdownV2 in dynamic values
    run_esc = run_display.replace("-", "\\-").replace(":", "\\:").replace("T", "T")

    text = (
        f"{emoji} *Dangkor District — 72h Forecast*\n\n"
        f"Alert level: *{level}*\n"
        f"Mean probability: *{mean_prob:.1%}*\n"
        f"Peak probability: {max_prob:.1%}\n"
        f"L2\\+ cells: {n_l2}/{n_total}  \\|  L3\\+: {n_l3}  \\|  L4: {n_l4}\n"
        f"{commune_lines}"
        f"{high_impact_note}\n"
        f"_Run: {run_esc}_"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=map_button(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /help."""
    await update.message.reply_text(MSG_HELP, parse_mode=ParseMode.MARKDOWN)


# ── Callback query handler ────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()

    if query.data == "subscribe":
        user = query.from_user
        status = await _do_subscribe(
            chat_id=query.message.chat_id,
            username=user.username if user else None,
            first_name=user.first_name if user else None,
        )
        if status == "subscribed":
            await query.message.reply_text(MSG_SUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
        elif status == "already":
            await query.message.reply_text(MSG_ALREADY_SUBSCRIBED, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await query.message.reply_text(
                "✅ Subscription noted locally \\(database not configured\\)\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Starting @dangkor_flood_bot in polling mode…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
